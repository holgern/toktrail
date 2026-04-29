from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from typer.testing import CliRunner

from tests.helpers import VALID_ASSISTANT, create_opencode_db, insert_message
from toktrail.cli import app


def write_jsonl_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )


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


def create_copilot_file(path: Path) -> None:
    write_jsonl_rows(
        path,
        [
            {
                "type": "span",
                "traceId": "trace-1",
                "spanId": "span-1",
                "name": "chat claude-sonnet-4",
                "endTime": [1775934264, 967317833],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.response.model": "claude-sonnet-4",
                    "gen_ai.conversation.id": "conv-1",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 5,
                },
            }
        ],
    )


def create_pi_session_file(path: Path) -> None:
    write_jsonl_rows(
        path,
        [
            {
                "type": "session",
                "id": "pi_ses_001",
                "timestamp": "2026-01-01T00:00:00.000Z",
                "cwd": "/tmp",
            },
            {
                "type": "message",
                "id": "msg_001",
                "parentId": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-3-5-sonnet",
                    "provider": "anthropic",
                    "usage": {
                        "input": 100,
                        "output": 50,
                        "cacheRead": 10,
                        "cacheWrite": 5,
                        "totalTokens": 165,
                    },
                },
            },
        ],
    )


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


def test_cli_import_copilot_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "copilot",
            "--copilot-file",
            str(copilot_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported Copilot usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "copilot"
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 5


def test_cli_import_missing_copilot_file_fails(tmp_path) -> None:
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
            "copilot",
            "--copilot-file",
            str(tmp_path / "missing.jsonl"),
        ],
    )

    assert result.exit_code == 1
    assert "Copilot telemetry file not found" in result.output


def test_cli_import_copilot_without_file_or_env_fails(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    result = runner.invoke(app, ["--db", str(state_db), "import", "copilot"])

    assert result.exit_code == 1
    assert "--copilot-file or TOKTRAIL_COPILOT_FILE" in result.output


def test_cli_watch_copilot_exits_cleanly_on_ctrl_c(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

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
            "copilot",
            "--copilot-file",
            str(copilot_file),
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Stopped watching Copilot." in result.output
    assert "rows imported: 1" in result.output


def test_cli_import_pi_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    session_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    create_pi_session_file(session_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "pi",
            "--pi-path",
            str(session_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported Pi usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "pi"
    assert payload["totals"]["total"] == 165
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 50
    assert payload["totals"]["cache_read"] == 10
    assert payload["totals"]["cache_write"] == 5
    assert payload["totals"]["reasoning"] == 0
    assert payload["totals"]["cost_usd"] == 0.0


def test_cli_pi_sessions_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd" / "session.jsonl")

    result = runner.invoke(
        app,
        ["pi", "sessions", "--pi-path", str(session_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "pi_ses_001" in result.output
    assert "messages=1" in result.output
    assert "tokens=165" in result.output


def test_cli_watch_pi_exits_cleanly_on_ctrl_c(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    session_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    create_pi_session_file(session_file)

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
            "pi",
            "--pi-path",
            str(session_file),
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Stopped watching Pi." in result.output
    assert "rows imported: 1" in result.output


def test_cli_import_pi_without_path_or_env_fails(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOKTRAIL_PI_SESSIONS", raising=False)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    result = runner.invoke(app, ["--db", str(state_db), "import", "pi"])

    assert result.exit_code == 1
    assert "Pi sessions path not found" in result.output
