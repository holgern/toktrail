from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from tests.cli.helpers import (
    _toml_path_value,
    create_codex_session_file,
    create_source_db,
    make_cli_usage_event,
)
from tests.helpers import (
    VALID_ASSISTANT,
    create_opencode_db,
    insert_message,
)
from toktrail.api.sessions import start_run
from toktrail.cli import app
from toktrail.db import (
    connect,
    create_tracking_session,
    insert_usage_events,
    migrate,
)
from toktrail.models import TokenBreakdown


def test_cli_init_start_refresh_status_stop(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    for args in (
        ["--db", str(state_db), "init"],
        ["--db", str(state_db), "run", "start", "--name", "test-session"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output

    create_source_db(source_db)

    for args in (
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
        ["--db", str(state_db), "run", "list"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output

    status_result = runner.invoke(
        app,
        ["--db", str(state_db), "run", "status", "1", "--json"],
    )
    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert payload["session"]["name"] == "test-session"
    assert payload["totals"]["total"] == 1500
    assert payload["totals"]["source_cost_usd"] == "0.05"
    assert payload["totals"]["actual_cost_usd"] == "0.05"
    assert payload["totals"]["virtual_cost_usd"] in ("0", "0.0")
    assert payload["totals"]["savings_usd"] == "-0.05"
    assert payload["totals"]["unpriced_count"] == 1

    stop_result = runner.invoke(app, ["--db", str(state_db), "run", "stop"])
    assert stop_result.exit_code == 0, stop_result.output


def test_cli_run_list_lists_tracking_runs(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(app, ["--db", str(state_db), "run", "list"])

    assert result.exit_code == 0, result.output
    assert "test-session" in result.output
    assert "Started" in result.output


def test_cli_run_start_accepts_harness_scope_and_status_json_includes_scope(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    init_result = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init_result.exit_code == 0, init_result.output

    start_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "run",
            "start",
            "--name",
            "codex-only",
            "--harness",
            "codex",
        ],
    )
    assert start_result.exit_code == 0, start_result.output
    assert "Scope: harness=codex" in start_result.output

    status_result = runner.invoke(
        app,
        ["--db", str(state_db), "run", "status", "--json", "--no-refresh"],
    )
    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert payload["session"]["scope"]["harnesses"] == ["codex"]


def test_cli_run_archive_hides_default_list(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "archive-me"])
    runner.invoke(app, ["--db", str(state_db), "run", "stop", "--no-refresh"])

    archive_result = runner.invoke(app, ["--db", str(state_db), "run", "archive", "1"])
    assert archive_result.exit_code == 0, archive_result.output

    default_list = runner.invoke(app, ["--db", str(state_db), "run", "list"])
    archived_list = runner.invoke(
        app,
        ["--db", str(state_db), "run", "list", "--archived"],
    )

    assert default_list.exit_code == 0, default_list.output
    assert archived_list.exit_code == 0, archived_list.output
    assert "archive-me" not in default_list.output
    assert "archive-me" in archived_list.output


def test_cli_run_archive_rejects_active_run(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "active"])

    result = runner.invoke(app, ["--db", str(state_db), "run", "archive", "1"])

    assert result.exit_code == 1
    assert "Cannot archive active run 1" in result.output


def test_cli_run_status_refresh_uses_stored_harness_scope(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    config_path = tmp_path / "toktrail.toml"
    create_source_db(opencode_db)
    create_codex_session_file(codex_file)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode", "codex"]
missing_source = "error"

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
codex = "{_toml_path_value(codex_file)}"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "run",
            "start",
            "--name",
            "codex-only",
            "--harness",
            "codex",
        ],
    )

    status_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "--json",
        ],
    )

    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert [row["harness"] for row in payload["by_harness"]] == ["codex"]


def test_cli_run_status_reports_only_usage_since_run_started(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        session_id = create_tracking_session(conn, "bounded", started_at_ms=1_000)
        insert_usage_events(
            conn,
            session_id,
            [
                make_cli_usage_event(
                    "old",
                    created_ms=999,
                    tokens=TokenBreakdown(input=100),
                ),
                make_cli_usage_event(
                    "new",
                    created_ms=1_001,
                    tokens=TokenBreakdown(input=7, output=3),
                ),
            ],
        )
    finally:
        conn.close()

    result = runner.invoke(
        app,
        ["--db", str(state_db), "run", "status", str(session_id), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["input"] == 7
    assert payload["totals"]["output"] == 3
    assert payload["totals"]["total"] == 10
    assert payload["filters"]["since_ms"] == 1_000


def test_cli_run_status_auto_refreshes_active_session(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    create_source_db(source_db)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "test"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["total"] > 0
    assert "refresh" not in payload


def test_cli_run_status_no_refresh_uses_stale_state(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    create_source_db(source_db)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "test"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "--json",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["total"] == 0


def test_cli_run_stop_refreshes_before_closing_session(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    conn = create_opencode_db(source_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    message = deepcopy(VALID_ASSISTANT)
    message["time"] = {
        "created": float(now_ms),
        "completed": float(now_ms + 500),
    }
    insert_message(conn, row_id="row-1", session_id="ses-1", data=message)
    conn.commit()
    conn.close()
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    start_run(state_db, name="test", started_at_ms=0)
    stop = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "run", "stop"],
    )
    assert stop.exit_code == 0, stop.output
    assert "Refreshed usage" not in stop.output

    status = runner.invoke(
        app,
        ["--db", str(state_db), "run", "status", "1", "--json", "--no-refresh"],
    )
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["totals"]["total"] > 0
