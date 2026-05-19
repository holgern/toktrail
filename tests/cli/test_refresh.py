from __future__ import annotations

import json
import sqlite3

from typer.testing import CliRunner

from tests.cli.helpers import (
    _toml_path_value,
    create_amp_source,
    create_codex_session_file,
    create_copilot_file,
    create_droid_source,
    create_goose_source_db,
    create_harnessbridge_source,
    create_pi_session_file,
    create_source_db,
)
from toktrail.cli import app


def test_cli_refresh_missing_opencode_db_fails(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(tmp_path / "missing.db"),
        ],
    )

    assert result.exit_code == 1
    assert "OpenCode database not found" in result.output


def test_cli_refresh_copilot_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "copilot",
            "--source",
            str(copilot_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed Copilot usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "copilot"
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 5


def test_cli_refresh_codex_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "codex",
            "--source",
            str(codex_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed Codex usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "codex"
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["cache_read"] == 20
    assert payload["totals"]["output"] == 30
    assert payload["totals"]["reasoning"] == 5


def test_cli_refresh_code_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    code_file = tmp_path / "code" / "session-001.jsonl"
    create_codex_session_file(code_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "code",
            "--source",
            str(code_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed Code usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "code"
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["cache_read"] == 20
    assert payload["totals"]["output"] == 30
    assert payload["totals"]["reasoning"] == 5


def test_cli_refresh_goose_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    goose_db = tmp_path / "goose" / "sessions.db"
    create_goose_source_db(goose_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "goose",
            "--source",
            str(goose_db),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed Goose usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "goose"
    assert payload["totals"]["input"] == 90
    assert payload["totals"]["output"] == 40
    assert payload["totals"]["reasoning"] == 20
    assert payload["totals"]["total"] == 130
    assert payload["totals"]["source_cost_usd"] in ("0", "0.0")


def test_cli_refresh_droid_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_path = tmp_path / "factory" / "sessions"
    create_droid_source(source_path)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "droid"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "droid",
            "--source",
            str(source_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed Droid usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "droid"
    assert payload["totals"]["input"] == 1234
    assert payload["totals"]["output"] == 567
    assert payload["totals"]["reasoning"] == 34
    assert payload["totals"]["cache_read"] == 12
    assert payload["totals"]["cache_write"] == 89
    assert payload["totals"]["total"] == 1801


def test_cli_refresh_amp_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_path = tmp_path / "amp" / "threads"
    create_amp_source(source_path)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "amp"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "amp",
            "--source",
            str(source_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed Amp usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "amp"
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 20
    assert payload["totals"]["cache_read"] == 30
    assert payload["totals"]["cache_write"] == 40
    assert payload["totals"]["total"] == 120
    assert payload["totals"]["source_cost_usd"] == "0.75"


def test_cli_plain_refresh_uses_config_without_active_session(tmp_path) -> None:
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

    init_result = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "refresh", "--json"],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload[0]["harness"] == "opencode"
    assert payload[0]["run_id"] is None
    assert payload[0]["rows_imported"] == 1
    assert payload[0]["rows_linked"] == 0


def test_cli_import_command_removed_pre_release() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["import"])
    assert result.exit_code != 0


def test_cli_refresh_respects_raw_json_config_default_false(tmp_path) -> None:
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
    result = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "refresh"],
    )
    assert result.exit_code == 0, result.output

    conn = sqlite3.connect(state_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE raw_json IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_cli_refresh_raw_overrides_config(tmp_path) -> None:
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
    result = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "refresh", "--raw"],
    )
    assert result.exit_code == 0, result.output

    conn = sqlite3.connect(state_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE raw_json IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert count > 0


def test_cli_plain_refresh_supports_harness_override_and_source(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    create_source_db(source_db)
    config_path.write_text(
        """
config_version = 1

[imports]
harnesses = ["pi"]
missing_source = "warn"
include_raw_json = false
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert [row["harness"] for row in payload] == ["opencode"]
    assert payload[0]["rows_imported"] == 1


def test_cli_plain_refresh_supports_codex_harness_override_and_source(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    config_path = tmp_path / "toktrail.toml"
    create_codex_session_file(codex_file)
    config_path.write_text(
        """
config_version = 1

[imports]
harnesses = ["pi"]
missing_source = "warn"
include_raw_json = false
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "refresh",
            "--harness",
            "codex",
            "--source",
            str(codex_file),
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert [row["harness"] for row in payload] == ["codex"]
    assert payload[0]["rows_imported"] == 1


def test_cli_plain_refresh_supports_code_harness_override_and_source(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    code_file = tmp_path / "code" / "session-001.jsonl"
    config_path = tmp_path / "toktrail.toml"
    create_codex_session_file(code_file)
    config_path.write_text(
        """
config_version = 1

[imports]
harnesses = ["pi"]
missing_source = "warn"
include_raw_json = false
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "refresh",
            "--harness",
            "code",
            "--source",
            str(code_file),
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert [row["harness"] for row in payload] == ["code"]
    assert payload[0]["rows_imported"] == 1


def test_cli_plain_refresh_supports_amp_harness_override_and_source(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_path = tmp_path / "amp" / "threads"
    config_path = tmp_path / "toktrail.toml"
    create_amp_source(source_path)
    config_path.write_text(
        """
config_version = 1

[imports]
harnesses = ["pi"]
missing_source = "warn"
include_raw_json = false
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "refresh",
            "--harness",
            "amp",
            "--source",
            str(source_path),
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert [row["harness"] for row in payload] == ["amp"]
    assert payload[0]["rows_imported"] == 1


def test_cli_refresh_with_no_session_inserts_unscoped_rows(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--no-run",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload[0]["run_id"] is None
    assert payload[0]["rows_imported"] == 1


def test_cli_refresh_with_no_session_is_idempotent(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])

    # First import
    result1 = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--no-run",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--json",
        ],
    )
    payload1 = json.loads(result1.output)
    assert payload1[0]["rows_imported"] == 1

    # Second import should skip duplicate
    result2 = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--no-run",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--json",
        ],
    )
    payload2 = json.loads(result2.output)
    assert payload2[0]["rows_imported"] == 0
    assert payload2[0]["rows_skipped"] == 1


def test_cli_refresh_harnessbridge_with_no_session_is_idempotent(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_file = tmp_path / "hb.jsonl"
    create_harnessbridge_source(source_file)

    runner.invoke(app, ["--db", str(state_db), "init"])

    result1 = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--no-run",
            "--harness",
            "harnessbridge",
            "--source",
            str(source_file),
            "--json",
        ],
    )
    payload1 = json.loads(result1.output)

    assert result1.exit_code == 0, result1.output
    assert payload1[0]["rows_imported"] == 1

    result2 = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--no-run",
            "--harness",
            "harnessbridge",
            "--source",
            str(source_file),
            "--json",
        ],
    )
    payload2 = json.loads(result2.output)

    assert result2.exit_code == 0, result2.output
    assert payload2[0]["rows_imported"] == 0
    assert payload2[0]["rows_skipped"] == 1


def test_cli_refresh_with_no_session_dry_run_does_not_persist(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])

    # Dry-run import (without --json to see the message)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--no-run",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[dry-run: changes were not persisted]" in result.output

    # Verify no rows were actually inserted
    conn = sqlite3.connect(state_db)
    count = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    conn.close()
    assert count == 0


def test_cli_refresh_missing_copilot_file_fails(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "copilot",
            "--source",
            str(tmp_path / "missing.jsonl"),
        ],
    )

    assert result.exit_code == 1
    assert "Copilot telemetry file not found" in result.output


def test_cli_refresh_codex_without_path_or_env_fails(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOKTRAIL_CODEX_SESSIONS", raising=False)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "codex",
            "--source",
            str(tmp_path / "missing_sessions"),
        ],
    )

    assert result.exit_code == 1
    assert "Codex source path not found" in result.output


def test_cli_refresh_code_without_path_or_env_fails(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOKTRAIL_CODE_SESSIONS", raising=False)
    monkeypatch.delenv("CODE_HOME", raising=False)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "code",
            "--source",
            str(tmp_path / "missing_sessions"),
        ],
    )

    assert result.exit_code == 1
    assert "Code source path not found" in result.output


def test_cli_refresh_copilot_without_file_or_env_fails(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    env = {
        "HOME": str(tmp_path),
        "TOKTRAIL_COPILOT_FILE": "",
        "COPILOT_OTEL_FILE_EXPORTER_PATH": "",
        "TOKTRAIL_COPILOT_OTEL_DIR": "",
    }

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "copilot",
            "--source",
            str(tmp_path / "missing.jsonl"),
        ],
        env=env,
    )

    assert result.exit_code == 1
    assert "Copilot telemetry file not found" in result.output


def test_cli_refresh_pi_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    session_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    create_pi_session_file(session_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "pi",
            "--source",
            str(session_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed Pi usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "pi"
    assert payload["totals"]["total"] == 150
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 50
    assert payload["totals"]["cache_read"] == 10
    assert payload["totals"]["cache_write"] == 5
    assert payload["totals"]["reasoning"] == 0
    assert payload["totals"]["source_cost_usd"] in ("0", "0.0")
    assert payload["totals"]["actual_cost_usd"] in ("0", "0.0")
    assert payload["totals"]["virtual_cost_usd"] in ("0", "0.0")
    assert payload["totals"]["savings_usd"] in ("0", "0.0")
    assert payload["totals"]["unpriced_count"] == 1


def test_cli_refresh_pi_without_path_or_env_fails(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOKTRAIL_PI_SESSIONS", raising=False)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "pi",
            "--source",
            str(tmp_path / "missing_sessions"),
        ],
    )

    assert result.exit_code == 1
    assert "Pi sessions path not found" in result.output
