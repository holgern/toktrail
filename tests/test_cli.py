from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from typer.testing import CliRunner

from tests.helpers import VALID_ASSISTANT, create_opencode_db, insert_message
from toktrail.cli import app


def create_source_db(path: Path) -> None:
    conn = create_opencode_db(path)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()


def test_cli_init_start_import_status_stop(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    for args in (
        ["--db", str(state_db), "init"],
        ["--db", str(state_db), "start", "--name", "test-session"],
        ["--db", str(state_db), "import", "opencode", "--opencode-db", str(source_db)],
        ["--db", str(state_db), "sessions"],
        ["--db", str(state_db), "stop"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output

    status_result = runner.invoke(
        app,
        ["--db", str(state_db), "status", "1", "--json"],
    )
    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert payload["session"]["name"] == "test-session"
    assert payload["totals"]["total"] == 1850
    assert payload["totals"]["cost_usd"] == 0.05


def test_cli_import_missing_opencode_db_fails(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "opencode",
            "--opencode-db",
            str(tmp_path / "missing.db"),
        ],
    )

    assert result.exit_code == 1
    assert "OpenCode database not found" in result.output


def test_cli_opencode_sessions_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    result = runner.invoke(
        app,
        ["opencode", "sessions", "--opencode-db", str(source_db)],
    )

    assert result.exit_code == 0, result.output
    assert "ses-1" in result.output
    assert "messages=1" in result.output
    assert "tokens=1850" in result.output


def test_cli_watch_opencode_exits_cleanly_on_ctrl_c(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "watch",
            "opencode",
            "--opencode-db",
            str(source_db),
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Stopped watching OpenCode." in result.output
    assert "rows imported: 1" in result.output
