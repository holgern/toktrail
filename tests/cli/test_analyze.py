from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.cli.helpers import (
    create_opencode_cache_analysis_source_db,
)
from toktrail.cli import app


def test_cli_analyze_session_opencode_last_human_output(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "--source",
            str(source_db),
            "--last",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "opencode source session ses-cache" in result.output
    assert "estimated source cache loss:" in result.output
    assert "Per call" in result.output
    assert "Clusters" in result.output


def test_cli_analyze_session_opencode_json_shape(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "--source",
            str(source_db),
            "--last",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "session_cache_analysis"
    assert payload["harness"] == "opencode"
    assert payload["source_session_id"] == "ses-cache"
    assert payload["call_count"] == 2
    assert payload["totals"]["cache_read"] == 120000
    assert payload["totals"]["unpriced_count"] == 2
    assert payload["calls"][0]["ordinal"] == 1
    assert "context_tokens" in payload["calls"][0]
    assert "virtual_price_context_label" in payload["calls"][0]
    assert "missing_price_kinds" in payload["calls"][0]
    assert "call_ordinals" in payload["clusters"][0]
    assert payload["clusters"][0]["call_count"] == 2


def test_cli_analyze_session_opencode_known_source_session_id(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "ses-cache",
            "--source",
            str(source_db),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ses-cache" in result.output


def test_cli_analyze_session_rejects_last_and_source_session_id(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "ses-cache",
            "--source",
            str(source_db),
            "--last",
        ],
    )

    assert result.exit_code == 1
    assert "cannot be used together" in result.output


def test_cli_analyze_session_no_raw_json_in_output(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "--source",
            str(source_db),
            "--last",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "raw_json" not in result.output
    assert all("raw_json" not in row for row in payload["calls"])


def _write_codex_digest_source(path: Path) -> None:
    rows = [
        {
            "type": "turn_context",
            "payload": {
                "working_directory": "/tmp/toktrail",
                "session_title": "Digest work",
            },
        },
        {
            "type": "turn.completed",
            "model": "gpt-5",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "exec_command",
                "cmd": "pytest tests/test_codex_session_digest.py",
                "exit_code": 1,
                "stderr": "failed",
                "path": "tests/test_codex_session_digest.py",
            },
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_cli_analyze_session_codex_json_compact_and_persisted_usage_summary(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_digest_source(source_dir / "codex_ses_1.jsonl")

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "codex",
            "--source",
            str(source_dir),
            "--last",
            "--persist",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "session_compact_report"
    assert payload["harness"] == "codex"
    assert payload["source_session_id"] == "codex_ses_1"
    assert payload["message_count"] == 1
    assert payload["usage"]["total"] == 12
    assert payload["cache"]["cache_read_tokens"] == 0
    assert payload["tool_health"]["tool_call_count"] == 1
    assert payload["tool_health"]["tool_failure_count"] == 1
    assert payload["digest_available"] is True
    assert payload["privacy"]["contains_raw_transcript"] is False
    assert payload["privacy"]["artifacts_included"] is False
    assert "artifacts" not in payload
    assert "raw_json" not in result.output
    assert "pytest tests/test_codex_session_digest.py" not in result.output
    assert "calls" not in payload

    usage = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--with-summary",
            "--no-refresh",
        ],
    )

    assert usage.exit_code == 0, usage.output
    assert "Summary:" in usage.output
    assert "tool_failures=1" in usage.output


def test_cli_analyze_session_codex_details_json_can_include_artifacts(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_digest_source(source_dir / "codex_ses_1.jsonl")

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "codex",
            "--source",
            str(source_dir),
            "--last",
            "--details",
            "--rich",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "session_digest"
    assert payload["privacy"]["artifacts_included"] is True
    assert payload["artifacts"]["files_mentioned"] == [
        "tests/test_codex_session_digest.py"
    ]


def test_cli_analyze_session_rejects_last_and_source_session_id_for_digest(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_digest_source(source_dir / "codex_ses_1.jsonl")

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "codex",
            "codex_ses_1",
            "--source",
            str(source_dir),
            "--last",
        ],
    )

    assert result.exit_code == 1
    assert "cannot be used together" in result.output


def _write_codex_bad_tool_calls_source(path: Path) -> None:
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
                "cwd": "/tmp/project",
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
                "cwd": "/tmp/project",
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


def test_cli_analyze_session_codex_bad_calls_human_output(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_bad_tool_calls_source(source_dir / "codex_ses_bc.jsonl")

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "codex",
            "--source",
            str(source_dir),
            "--last",
            "--bad-calls",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Bad tool calls: 1 of 2" in result.output
    assert "exec_command" in result.output
    assert "FAILED" in result.output or "failed" in result.output


def test_cli_analyze_session_codex_bad_calls_json_shape(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_bad_tool_calls_source(source_dir / "codex_ses_bc2.jsonl")

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "codex",
            "--source",
            str(source_dir),
            "--last",
            "--bad-calls",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "session_tool_calls"
    assert payload["failure_count"] == 1
    assert payload["timeout_count"] == 0
    assert payload["tool_call_count"] == 2
    assert len(payload["bad_calls"]) >= 1
    bad = payload["bad_calls"][0]
    assert bad["tool_name"] == "exec_command"
    assert bad["status"] == "failed"
    assert bad["exit_code"] == 2


def test_cli_analyze_session_codex_bad_calls_no_raw_json_in_output(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_bad_tool_calls_source(source_dir / "codex_ses_bc3.jsonl")

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "codex",
            "--source",
            str(source_dir),
            "--last",
            "--bad-calls",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "raw_json" not in result.output


def test_cli_analyze_session_codex_bad_calls_rejects_raw_tool_json_without_json(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_bad_tool_calls_source(source_dir / "codex_ses_bc4.jsonl")

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "codex",
            "--source",
            str(source_dir),
            "--last",
            "--bad-calls",
            "--raw-tool-json",
        ],
    )

    assert result.exit_code == 1
    assert "--raw-tool-json" in result.output or "--json" in result.output


def test_cli_analyze_session_without_bad_calls_unaffected(tmp_path: Path) -> None:
    """Verify analyze session without --bad-calls still shows the compact report."""
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "codex"
    _write_codex_digest_source(source_dir / "codex_ses_bc5.jsonl")

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "session",
            "codex",
            "--source",
            str(source_dir),
            "--last",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "session_compact_report"
