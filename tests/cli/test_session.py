from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from tests.cli.helpers import _future_ms, create_source_db, make_cli_usage_event
from toktrail.cli import app
from toktrail.db import (
    assign_area_to_source_session,
    connect,
    ensure_area,
    get_local_machine_id,
    insert_usage_events,
    migrate,
)
from toktrail.models import TokenBreakdown


def _seed_usage_sessions(state_db: Path) -> None:
    conn = connect(state_db)
    try:
        migrate(conn)
        local_machine_id = get_local_machine_id(conn)
        area = ensure_area(conn, "work/toktrail")
        created_ms = _future_ms()
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "session-opencode",
                    source_session_id="ses-opencode",
                    created_ms=created_ms,
                    tokens=TokenBreakdown(input=12, output=3),
                ),
                replace(
                    make_cli_usage_event(
                        "session-codex",
                        source_session_id="ses-codex",
                        created_ms=created_ms + 1_000,
                        tokens=TokenBreakdown(input=7, output=2),
                    ),
                    harness="codex",
                ),
            ],
        )
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=local_machine_id,
            harness="opencode",
            source_session_id="ses-opencode",
        )
        conn.commit()
    finally:
        conn.close()


def _write_codex_tool_calls_source(path: Path) -> None:
    rows = [
        {
            "timestamp": "2026-05-24T05:41:18Z",
            "type": "event_msg",
            "payload": {
                "type": "exec_command",
                "call_id": "call_ok_1",
                "command": "echo ok",
                "exit_code": 0,
                "duration_ms": 50,
                "cwd": "/workspace/project",
            },
        },
        {
            "timestamp": "2026-05-24T05:41:19Z",
            "type": "event_msg",
            "payload": {
                "type": "exec_command",
                "call_id": "call_bad_1",
                "command": "rg -n missing toktrail",
                "exit_code": 2,
                "duration_ms": 183,
                "stderr": "No files were searched",
                "cwd": "/workspace/project",
            },
        },
        {
            "timestamp": "2026-05-24T05:41:20Z",
            "type": "turn.completed",
            "model": "gpt-5",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_cli_session_list_filters(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    _seed_usage_sessions(state_db)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "session",
            "list",
            "--area",
            "work",
            "--harness",
            "opencode",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "usage_sessions"
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["key"].endswith("/opencode/ses-opencode")
    assert payload["sessions"][0]["area_path"] == "work/toktrail"


def test_cli_session_get_by_key(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    _seed_usage_sessions(state_db)

    sessions = runner.invoke(
        app,
        ["--db", str(state_db), "session", "list", "--json"],
    )
    assert sessions.exit_code == 0, sessions.output
    session_key = json.loads(sessions.output)["sessions"][0]["key"]

    result = runner.invoke(
        app,
        ["--db", str(state_db), "session", "get", session_key, "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "usage_session"
    assert payload["key"] == session_key
    assert payload["source_session_id"] == "ses-codex"


def test_cli_session_health_json_uses_persisted_digest(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    init = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init.exit_code == 0, init.output
    refresh = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--no-run",
        ],
    )
    assert refresh.exit_code == 0, refresh.output

    sessions = runner.invoke(
        app,
        ["--db", str(state_db), "session", "list", "--json"],
    )
    assert sessions.exit_code == 0, sessions.output
    session_key = json.loads(sessions.output)["sessions"][0]["key"]

    persist_digest = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "opencode",
            "ses-1",
            "--source",
            str(source_db),
            "--details",
            "--persist",
            "--json",
        ],
    )
    assert persist_digest.exit_code == 0, persist_digest.output

    source_db.unlink()

    result = runner.invoke(
        app,
        ["--db", str(state_db), "session", "health", session_key, "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "session_digest"
    assert payload["key"] == session_key
    assert payload["source_session_id"] == "ses-1"
    assert payload["summary"]["generator"] == "heuristic:v1"
    assert "tool_health" in payload


def test_cli_session_tool_calls_delegate_to_scanner(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_tool_calls_source(source_dir / "codex_ses_tools.jsonl")

    init = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init.exit_code == 0, init.output
    refresh = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "codex",
            "--source",
            str(source_dir),
            "--no-run",
        ],
    )
    assert refresh.exit_code == 0, refresh.output

    sessions = runner.invoke(
        app,
        ["--db", str(state_db), "session", "list", "--harness", "codex", "--json"],
    )
    assert sessions.exit_code == 0, sessions.output
    session_key = json.loads(sessions.output)["sessions"][0]["key"]

    result = runner.invoke(
        app,
        ["--db", str(state_db), "session", "tool-calls", session_key, "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "session_tool_calls"
    assert payload["key"] == session_key
    assert payload["tool_call_count"] == 2
    assert len(payload["calls"]) == 2
    assert len(payload["bad_calls"]) == 1
    assert payload["bad_calls"][0]["tool_name"] == "exec_command"
    assert payload["bad_calls"][0]["status"] == "failed"


def test_cli_session_missing_session_errors_are_explicit(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    init = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init.exit_code == 0, init.output

    missing_key = "missing-machine/opencode/ses-404"
    get_result = runner.invoke(
        app,
        ["--db", str(state_db), "session", "get", missing_key],
    )
    health_result = runner.invoke(
        app,
        ["--db", str(state_db), "session", "health", missing_key],
    )

    assert get_result.exit_code == 1
    assert f"Session not found: {missing_key}" in get_result.output
    assert health_result.exit_code == 1
    assert f"Session not found: {missing_key}" in health_result.output
