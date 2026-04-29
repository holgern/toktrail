from __future__ import annotations

import json
import shlex
import subprocess
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
    assert payload["totals"]["source_cost_usd"] == 0.05
    assert payload["totals"]["actual_cost_usd"] == 0.05
    assert payload["totals"]["virtual_cost_usd"] == 0.0
    assert payload["totals"]["savings_usd"] == -0.05
    assert payload["totals"]["unpriced_count"] == 1


def test_cli_sessions_without_subcommand_lists_tracking_sessions(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    result = runner.invoke(app, ["--db", str(state_db), "sessions"])

    assert result.exit_code == 0, result.output
    assert "test-session" in result.output
    assert "started=202" in result.output
    assert "started=17" not in result.output


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
        ["sessions", "opencode", "--opencode-db", str(source_db)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "ses-1" in result.output
    assert "1,850" in result.output
    assert "2023-" in result.output


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
    env = {
        "HOME": str(tmp_path),
        "TOKTRAIL_COPILOT_FILE": "",
        "COPILOT_OTEL_FILE_EXPORTER_PATH": "",
        "TOKTRAIL_COPILOT_OTEL_DIR": "",
    }

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    result = runner.invoke(app, ["--db", str(state_db), "import", "copilot"], env=env)

    assert result.exit_code == 1
    assert "Copilot source path not found" in result.output


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
    assert payload["totals"]["source_cost_usd"] == 0.0
    assert payload["totals"]["actual_cost_usd"] == 0.0
    assert payload["totals"]["virtual_cost_usd"] == 0.0
    assert payload["totals"]["savings_usd"] == 0.0
    assert payload["totals"]["unpriced_count"] == 1


def test_cli_status_filters_by_harness_and_source_session(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    session_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    create_source_db(source_db)
    create_pi_session_file(session_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    runner.invoke(
        app,
        ["--db", str(state_db), "import", "opencode", "--opencode-db", str(source_db)],
    )
    runner.invoke(
        app,
        ["--db", str(state_db), "import", "pi", "--pi-path", str(session_file)],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "status",
            "--harness",
            "pi",
            "--source-session",
            "pi_ses_001",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["filters"] == {
        "harness": "pi",
        "source_session_id": "pi_ses_001",
    }
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 50
    assert payload["by_harness"] == [
        {
            "harness": "pi",
            "message_count": 1,
            "total_tokens": 165,
            "source_cost_usd": 0.0,
            "actual_cost_usd": 0.0,
            "virtual_cost_usd": 0.0,
            "savings_usd": 0.0,
            "unpriced_count": 1,
        }
    ]


def test_cli_config_path_init_and_validate(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"

    path_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "path"],
    )
    assert path_result.exit_code == 0, path_result.output
    assert path_result.output.strip() == str(config_path)

    init_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )
    assert init_result.exit_code == 0, init_result.output
    assert config_path.exists()

    validate_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "validate"],
    )
    assert validate_result.exit_code == 0, validate_result.output
    assert "Config valid:" in validate_result.output
    assert "virtual prices:" in validate_result.output


def test_cli_status_with_template_config_computes_copilot_virtual_cost(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config" / "toktrail.toml"
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )
    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    runner.invoke(
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

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "status",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["source_cost_usd"] == 0.0
    assert payload["totals"]["actual_cost_usd"] == 0.0
    assert payload["totals"]["virtual_cost_usd"] > 0.0
    assert payload["totals"]["savings_usd"] == payload["totals"]["virtual_cost_usd"]


def test_cli_status_human_output_contains_actual_virtual_and_savings(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "start", "--name", "test-session"])
    runner.invoke(
        app,
        ["--db", str(state_db), "import", "opencode", "--opencode-db", str(source_db)],
    )

    result = runner.invoke(app, ["--db", str(state_db), "status", "1"])

    assert result.exit_code == 0, result.output
    assert "Costs" in result.output
    assert "actual:" in result.output
    assert "virtual:" in result.output
    assert "savings:" in result.output


def test_cli_pi_sessions_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd" / "session.jsonl")

    result = runner.invoke(
        app,
        ["sessions", "pi", "--pi-path", str(session_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "pi_ses_001" in result.output
    assert "165" in result.output
    assert "2026-" in result.output


def test_cli_sessions_copilot_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    result = runner.invoke(
        app,
        ["sessions", "copilot", "--copilot-file", str(copilot_file)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "conv-1" in result.output
    assert "105" in result.output


def test_cli_harness_first_sessions_are_removed(tmp_path) -> None:
    runner = CliRunner()
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd" / "session.jsonl")
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    commands = (
        ["opencode", "sessions", "--opencode-db", str(source_db)],
        ["pi", "sessions", "--pi-path", str(session_dir)],
        ["copilot", "sessions", "--copilot-file", str(copilot_file)],
    )

    for args in commands:
        result = runner.invoke(app, args)
        assert result.exit_code != 0


def test_cli_sessions_pi_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd" / "session.jsonl")

    result = runner.invoke(
        app,
        ["sessions", "pi", "--pi-path", str(session_dir), "--last", "--breakdown"],
    )

    assert result.exit_code == 0, result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "claude-3-5-sonnet" in result.output


def test_cli_sessions_pi_supports_limit_sort_and_columns(tmp_path) -> None:
    runner = CliRunner()
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd-a" / "session-a.jsonl")
    write_jsonl_rows(
        session_dir / "encoded-cwd-b" / "session-b.jsonl",
        [
            {
                "type": "session",
                "id": "pi_ses_999",
                "timestamp": "2026-01-01T00:00:00.000Z",
                "cwd": "/tmp",
            },
            {
                "type": "message",
                "id": "msg_999",
                "parentId": None,
                "timestamp": "2026-01-01T00:00:02.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-3-5-sonnet",
                    "provider": "anthropic",
                    "usage": {
                        "input": 200,
                        "output": 100,
                        "cacheRead": 20,
                        "cacheWrite": 10,
                        "totalTokens": 330,
                    },
                },
            },
        ],
    )

    result = runner.invoke(
        app,
        [
            "sessions",
            "pi",
            "--pi-path",
            str(session_dir),
            "--sort",
            "tokens",
            "--limit",
            "1",
            "--columns",
            "source_session_id,total",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "total" in result.output
    assert "pi_ses_999" in result.output
    assert "pi_ses_001" not in result.output


def test_cli_sessions_copilot_supports_virtual_and_savings_sort(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    copilot_dir = tmp_path / "copilot"
    write_jsonl_rows(
        copilot_dir / "first.jsonl",
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
    write_jsonl_rows(
        copilot_dir / "second.jsonl",
        [
            {
                "type": "span",
                "traceId": "trace-2",
                "spanId": "span-2",
                "name": "chat claude-sonnet-4",
                "endTime": [1775934265, 967317833],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.response.model": "claude-sonnet-4",
                    "gen_ai.conversation.id": "conv-2",
                    "gen_ai.usage.input_tokens": 300,
                    "gen_ai.usage.output_tokens": 10,
                },
            }
        ],
    )
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    for sort_value in ("virtual", "savings"):
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_path),
                "sessions",
                "copilot",
                "--copilot-file",
                str(copilot_dir),
                "--sort",
                sort_value,
                "--limit",
                "1",
                "--columns",
                "source_session_id,actual,virtual,savings",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "actual" in result.output
        assert "virtual" in result.output
        assert "savings" in result.output
        assert "conv-2" in result.output
        assert "conv-1" not in result.output


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


def test_cli_copilot_run_sets_otel_environment(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        env: dict[str, str],
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("toktrail.cli.subprocess.run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(app, ["copilot", "run", "--no-import", "--", "echo", "hi"])

    assert result.exit_code == 0, result.output
    assert captured["command"] == ["echo", "hi"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["COPILOT_OTEL_ENABLED"] == "true"
    assert env["COPILOT_OTEL_EXPORTER_TYPE"] == "file"
    assert env["COPILOT_OTEL_FILE_EXPORTER_PATH"].endswith(".jsonl")
    assert env["TOKTRAIL_COPILOT_FILE"] == env["COPILOT_OTEL_FILE_EXPORTER_PATH"]
    assert "Copilot OTEL file:" in result.output


def test_cli_copilot_env_outputs_shell_exports(tmp_path) -> None:
    runner = CliRunner()
    otel_file = tmp_path / "otel dir" / "copilot file.jsonl"
    otel_file_str = str(otel_file)

    expected_lines = {
        "bash": [
            "export COPILOT_OTEL_ENABLED=true",
            "export COPILOT_OTEL_EXPORTER_TYPE=file",
            f"export COPILOT_OTEL_FILE_EXPORTER_PATH={shlex.quote(otel_file_str)}",
            f"export TOKTRAIL_COPILOT_FILE={shlex.quote(otel_file_str)}",
        ],
        "zsh": [
            "export COPILOT_OTEL_ENABLED=true",
            "export COPILOT_OTEL_EXPORTER_TYPE=file",
            f"export COPILOT_OTEL_FILE_EXPORTER_PATH={shlex.quote(otel_file_str)}",
            f"export TOKTRAIL_COPILOT_FILE={shlex.quote(otel_file_str)}",
        ],
        "fish": [
            "set -gx COPILOT_OTEL_ENABLED 'true'",
            "set -gx COPILOT_OTEL_EXPORTER_TYPE 'file'",
            f"set -gx COPILOT_OTEL_FILE_EXPORTER_PATH '{otel_file_str}'",
            f"set -gx TOKTRAIL_COPILOT_FILE '{otel_file_str}'",
        ],
        "nu": [
            '$env.COPILOT_OTEL_ENABLED = "true"',
            '$env.COPILOT_OTEL_EXPORTER_TYPE = "file"',
            f"$env.COPILOT_OTEL_FILE_EXPORTER_PATH = {json.dumps(otel_file_str)}",
            f"$env.TOKTRAIL_COPILOT_FILE = {json.dumps(otel_file_str)}",
        ],
        "powershell": [
            "$env:COPILOT_OTEL_ENABLED = 'true'",
            "$env:COPILOT_OTEL_EXPORTER_TYPE = 'file'",
            f"$env:COPILOT_OTEL_FILE_EXPORTER_PATH = '{otel_file_str}'",
            f"$env:TOKTRAIL_COPILOT_FILE = '{otel_file_str}'",
        ],
    }

    for shell, lines in expected_lines.items():
        result = runner.invoke(
            app,
            ["copilot", "env", shell, "--otel-file", otel_file_str],
        )
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == lines


def test_cli_copilot_env_accepts_shell_aliases(tmp_path) -> None:
    runner = CliRunner()
    otel_file = tmp_path / "copilot.jsonl"
    otel_file_str = str(otel_file)

    result_nushell = runner.invoke(
        app,
        ["copilot", "env", "nushell", "--otel-file", otel_file_str],
    )
    result_nu = runner.invoke(
        app,
        ["copilot", "env", "nu", "--otel-file", otel_file_str],
    )
    assert result_nushell.exit_code == 0, result_nushell.output
    assert result_nu.exit_code == 0, result_nu.output
    assert result_nushell.output == result_nu.output

    result_pwsh = runner.invoke(
        app,
        ["copilot", "env", "pwsh", "--otel-file", otel_file_str],
    )
    result_powershell = runner.invoke(
        app,
        ["copilot", "env", "powershell", "--otel-file", otel_file_str],
    )
    assert result_pwsh.exit_code == 0, result_pwsh.output
    assert result_powershell.exit_code == 0, result_powershell.output
    assert result_pwsh.output == result_powershell.output


def test_cli_copilot_env_rejects_unknown_shell() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["copilot", "env", "csh"])

    assert result.exit_code == 1
    assert "Unsupported shell. Use bash, zsh, fish, nu, or powershell." in result.output


def test_cli_copilot_env_json_outputs_valid_json(tmp_path) -> None:
    runner = CliRunner()
    otel_file = tmp_path / "copilot.jsonl"
    otel_file_str = str(otel_file)

    for shell in ("nu", "bash", "fish", "powershell"):
        result = runner.invoke(
            app,
            ["copilot", "env", shell, "--otel-file", otel_file_str, "--json"],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert set(parsed.keys()) == {
            "COPILOT_OTEL_ENABLED",
            "COPILOT_OTEL_EXPORTER_TYPE",
            "COPILOT_OTEL_FILE_EXPORTER_PATH",
            "TOKTRAIL_COPILOT_FILE",
        }
        assert parsed["COPILOT_OTEL_ENABLED"] == "true"
        assert parsed["COPILOT_OTEL_EXPORTER_TYPE"] == "file"
        assert parsed["COPILOT_OTEL_FILE_EXPORTER_PATH"] == otel_file_str
        assert parsed["TOKTRAIL_COPILOT_FILE"] == otel_file_str


def test_cli_copilot_env_json_does_not_affect_default_output(tmp_path) -> None:
    runner = CliRunner()
    otel_file = tmp_path / "copilot.jsonl"
    otel_file_str = str(otel_file)

    result = runner.invoke(
        app,
        ["copilot", "env", "nu", "--otel-file", otel_file_str],
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("$env.COPILOT_OTEL_ENABLED")
    # Must not be valid JSON object output
    assert not result.output.strip().startswith("{")
