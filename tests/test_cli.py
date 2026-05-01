from __future__ import annotations

import json
import shlex
import sqlite3
import subprocess
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.helpers import (
    VALID_ASSISTANT,
    create_codex_session_file,
    create_opencode_db,
    insert_message,
)
from toktrail.cli import app


@pytest.fixture(autouse=True)
def isolate_default_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(tmp_path / "missing-config.toml"))


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


def create_goose_source_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            model_config_json TEXT,
            provider_name TEXT,
            created_at TEXT,
            total_tokens INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            accumulated_total_tokens INTEGER,
            accumulated_input_tokens INTEGER,
            accumulated_output_tokens INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sessions (
            id,
            model_config_json,
            provider_name,
            created_at,
            total_tokens,
            input_tokens,
            output_tokens,
            accumulated_total_tokens,
            accumulated_input_tokens,
            accumulated_output_tokens
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "goose-1",
            '{"model_name":"claude-sonnet-4-20250514"}',
            "anthropic",
            "2026-04-14T16:18:53Z",
            100,
            60,
            30,
            150,
            90,
            40,
        ),
    )
    conn.commit()
    conn.close()


def create_droid_source(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "droid-1.settings.json").write_text(
        json.dumps(
            {
                "model": "custom:Claude-Opus-4.5-Thinking-[Anthropic]-0",
                "providerLock": "anthropic",
                "providerLockTimestamp": "2024-12-26T12:00:00Z",
                "tokenUsage": {
                    "inputTokens": 1234,
                    "outputTokens": 567,
                    "cacheCreationTokens": 89,
                    "cacheReadTokens": 12,
                    "thinkingTokens": 34,
                },
            }
        ),
        encoding="utf-8",
    )


def create_amp_source(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "thread-1.json").write_text(
        json.dumps(
            {
                "id": "thread-1",
                "created": 1775649600000,
                "messages": [
                    {
                        "role": "assistant",
                        "messageId": 1,
                        "usage": {
                            "model": "claude-sonnet-4-0",
                            "inputTokens": 100,
                            "outputTokens": 20,
                            "cacheReadInputTokens": 30,
                            "cacheCreationInputTokens": 40,
                            "credits": 0.75,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def create_thinking_source_db(path: Path) -> None:
    conn = create_opencode_db(path)
    high = deepcopy(VALID_ASSISTANT)
    high["thinkingLevel"] = "high"
    insert_message(conn, row_id="row-1", session_id="ses-1", data=high)
    low = deepcopy(VALID_ASSISTANT)
    low["id"] = "msg-low"
    low["thinkingLevel"] = "low"
    insert_message(conn, row_id="row-2", session_id="ses-1", data=low)
    conn.commit()
    conn.close()


def create_pricing_source_db(path: Path) -> None:
    conn = create_opencode_db(path)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    unpriced = deepcopy(VALID_ASSISTANT)
    unpriced["id"] = "msg-unpriced"
    unpriced["modelID"] = "gpt-5.2-codex"
    unpriced["providerID"] = "openai-codex"
    unpriced["cost"] = 0.10
    unpriced["tokens"] = {
        "input": 400,
        "output": 40,
        "reasoning": 10,
        "cache": {"read": 0, "write": 0},
    }
    insert_message(conn, row_id="row-2", session_id="ses-1", data=unpriced)
    conn.commit()
    conn.close()


def write_pricing_config(path: Path) -> None:
    path.write_text(
        """
config_version = 1

[costing]
default_actual_mode = "source"
default_virtual_mode = "pricing"
missing_price = "warn"

[[actual_cost]]
harness = "opencode"
mode = "source"

[[actual_cost]]
harness = "pi"
mode = "zero"

[[actual_cost]]
harness = "copilot"
mode = "zero"

[[pricing.virtual]]
provider = "anthropic"
model = "claude-sonnet-4"
aliases = ["Claude Sonnet 4", "claude-sonnet-4"]
input_usd_per_1m = 3.0
cached_input_usd_per_1m = 0.3
cache_write_usd_per_1m = 3.75
output_usd_per_1m = 15.0
category = "Versatile"
release_status = "GA"
""".strip(),
        encoding="utf-8",
    )


def setup_pricing_status_fixture(tmp_path: Path) -> tuple[CliRunner, Path, Path]:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "pricing.db"
    config_path = tmp_path / "toktrail.toml"
    create_pricing_source_db(source_db)
    write_pricing_config(config_path)
    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "pricing-session"]
    )
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "import",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )
    return runner, state_db, config_path


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
        ["--db", str(state_db), "run", "start", "--name", "test-session"],
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
        ["--db", str(state_db), "sessions"],
        ["--db", str(state_db), "run", "stop"],
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
    assert payload["totals"]["total"] == 1850
    assert payload["totals"]["source_cost_usd"] == "0.05"
    assert payload["totals"]["actual_cost_usd"] == "0.05"
    assert payload["totals"]["virtual_cost_usd"] in ("0", "0.0")
    assert payload["totals"]["savings_usd"] == "-0.05"
    assert payload["totals"]["unpriced_count"] == 1


def test_cli_sessions_without_subcommand_lists_tracking_sessions(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(app, ["--db", str(state_db), "sessions"])

    assert result.exit_code == 0, result.output
    assert "test-session" in result.output
    assert "started=202" in result.output
    assert "started=17" not in result.output


def test_cli_import_missing_opencode_db_fails(tmp_path) -> None:
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
            "import",
            "--harness",
            "opencode",
            "--source",
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
        ["source-sessions", "opencode", "--opencode-db", str(source_db)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "ses-1" in result.output
    assert "1,850" in result.output
    assert "2023-" in result.output


def test_cli_sources_lists_filtered_source(tmp_path) -> None:
    runner = CliRunner()
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    result = runner.invoke(
        app,
        [
            "sources",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == [
        {
            "harness": "opencode",
            "source_path": str(source_db),
            "exists": True,
            "sessions": 1,
            "messages": 1,
            "tokens": 1850,
            "warning": "",
        }
    ]


def test_cli_sources_reports_missing_configured_source(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "toktrail.toml"
    missing_db = tmp_path / "missing.db"
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]

[imports.sources]
opencode = "{missing_db}"
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["--config", str(config_path), "sources", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["harness"] == "opencode"
    assert payload[0]["exists"] is False
    assert payload[0]["sessions"] == 0
    assert "OpenCode database not found" in payload[0]["warning"]


def test_cli_watch_opencode_exits_cleanly_on_ctrl_c(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )

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
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "copilot",
            "--source",
            str(copilot_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported Copilot usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "copilot"
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 5


def test_cli_import_codex_status(tmp_path) -> None:
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
            "import",
            "--harness",
            "codex",
            "--source",
            str(codex_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported Codex usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "codex"
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["cache_read"] == 20
    assert payload["totals"]["output"] == 30
    assert payload["totals"]["reasoning"] == 5


def test_cli_import_goose_status(tmp_path) -> None:
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
            "import",
            "--harness",
            "goose",
            "--source",
            str(goose_db),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported Goose usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "goose"
    assert payload["totals"]["input"] == 90
    assert payload["totals"]["output"] == 40
    assert payload["totals"]["reasoning"] == 20
    assert payload["totals"]["total"] == 150
    assert payload["totals"]["source_cost_usd"] in ("0", "0.0")


def test_cli_import_droid_status(tmp_path) -> None:
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
            "import",
            "--harness",
            "droid",
            "--source",
            str(source_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported Droid usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "droid"
    assert payload["totals"]["input"] == 1234
    assert payload["totals"]["output"] == 567
    assert payload["totals"]["reasoning"] == 34
    assert payload["totals"]["cache_read"] == 12
    assert payload["totals"]["cache_write"] == 89
    assert payload["totals"]["total"] == 1936


def test_cli_import_amp_status(tmp_path) -> None:
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
            "import",
            "--harness",
            "amp",
            "--source",
            str(source_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported Amp usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "amp"
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 20
    assert payload["totals"]["cache_read"] == 30
    assert payload["totals"]["cache_write"] == 40
    assert payload["totals"]["total"] == 190
    assert payload["totals"]["source_cost_usd"] == "0.75"


def test_cli_sessions_droid_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    source_path = tmp_path / "factory" / "sessions"
    create_droid_source(source_path)

    result = runner.invoke(
        app,
        [
            "source-sessions",
            "droid",
            "--droid-path",
            str(source_path),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Droid source session droid-1" in result.output
    assert "input:" in result.output
    assert "output:" in result.output
    assert "reasoning:" in result.output
    assert "cache read:" in result.output
    assert "cache write:" in result.output


def test_cli_watch_droid_is_not_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["watch", "droid"])

    assert result.exit_code != 0


def test_cli_status_supports_thinking_filter_and_collapse(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_thinking_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    filtered_split = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "run",
            "status",
            "1",
            "--json",
            "--thinking",
            "high",
            "--split-thinking",
        ],
    )
    split_thinking = runner.invoke(
        app,
        ["--db", str(state_db), "run", "status", "1", "--json", "--split-thinking"],
    )
    collapsed_default = runner.invoke(
        app,
        ["--db", str(state_db), "run", "status", "1", "--json"],
    )
    human = runner.invoke(app, ["--db", str(state_db), "run", "status", "1"])

    assert filtered_split.exit_code == 0, filtered_split.output
    assert split_thinking.exit_code == 0, split_thinking.output
    assert collapsed_default.exit_code == 0, collapsed_default.output
    filtered_split_payload = json.loads(filtered_split.output)
    split_thinking_payload = json.loads(split_thinking.output)
    collapsed_payload = json.loads(collapsed_default.output)
    assert [
        (row["thinking_level"], row["message_count"])
        for row in filtered_split_payload["by_model"]
    ] == [("high", 1)]
    assert sorted(
        [
            (row["thinking_level"], row["message_count"])
            for row in split_thinking_payload["by_model"]
        ]
    ) == [("high", 1), ("low", 1)]
    assert [
        (row["thinking_level"], row["message_count"])
        for row in collapsed_payload["by_model"]
    ] == [(None, 2)]
    assert "reasoning" in human.output


def test_cli_plain_import_uses_config_without_active_session(tmp_path) -> None:
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
opencode = "{source_db}"
""".strip(),
        encoding="utf-8",
    )

    init_result = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "import", "--json"],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload[0]["harness"] == "opencode"
    assert payload[0]["tracking_session_id"] is None
    assert payload[0]["rows_imported"] == 1
    assert payload[0]["rows_linked"] == 0


def test_cli_usage_today_reports_unscoped_imports(tmp_path, monkeypatch) -> None:
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
opencode = "{source_db}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2023, 11, 14, 23, 0, tzinfo=tz),
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "--config", str(config_path), "import"])
    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "today", "--utc", "--json"],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["session"] is None
    assert payload["filters"]["period"] == "today"
    assert payload["filters"]["timezone"] == "UTC"
    assert payload["totals"]["total"] == 1850


def test_cli_usage_supports_explicit_since_until_boundaries(tmp_path) -> None:
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
opencode = "{source_db}"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "--config", str(config_path), "import"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--since",
            "2023-11-14T00:00:00Z",
            "--until",
            "2023-11-15T00:00:00Z",
            "--utc",
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["filters"]["since_ms"] == 1699920000000
    assert payload["filters"]["until_ms"] == 1700006400000
    assert payload["filters"]["timezone"] == "UTC"
    assert payload["totals"]["total"] == 1850


def test_cli_plain_import_supports_harness_override_and_source(tmp_path) -> None:
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
            "import",
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


def test_cli_plain_import_supports_codex_harness_override_and_source(tmp_path) -> None:
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
            "import",
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


def test_cli_plain_import_supports_amp_harness_override_and_source(tmp_path) -> None:
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
            "import",
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


def test_cli_import_with_no_session_inserts_unscoped_rows(tmp_path) -> None:
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
            "import",
            "--no-session",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload[0]["tracking_session_id"] is None
    assert payload[0]["rows_imported"] == 1


def test_cli_import_with_no_session_is_idempotent(tmp_path) -> None:
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
            "import",
            "--no-session",
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
            "import",
            "--no-session",
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


def test_cli_import_with_no_session_dry_run_does_not_persist(tmp_path) -> None:
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
            "import",
            "--no-session",
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


def test_cli_import_missing_copilot_file_fails(tmp_path) -> None:
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
            "import",
            "--harness",
            "copilot",
            "--source",
            str(tmp_path / "missing.jsonl"),
        ],
    )

    assert result.exit_code == 1
    assert "Copilot telemetry file not found" in result.output


def test_cli_import_codex_without_path_or_env_fails(tmp_path, monkeypatch) -> None:
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
            "import",
            "--harness",
            "codex",
            "--source",
            str(tmp_path / "missing_sessions"),
        ],
    )

    assert result.exit_code == 1
    assert "Codex source path not found" in result.output


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
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "copilot",
            "--source",
            str(tmp_path / "missing.jsonl"),
        ],
        env=env,
    )

    assert result.exit_code == 1
    assert "Copilot telemetry file not found" in result.output


def test_cli_watch_copilot_exits_cleanly_on_ctrl_c(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )

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
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "pi",
            "--source",
            str(session_file),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Imported Pi usage:" in result.output
    assert "rows imported: 1" in result.output

    status = runner.invoke(app, ["--db", str(state_db), "run", "status", "1", "--json"])
    payload = json.loads(status.output)
    assert payload["by_harness"][0]["harness"] == "pi"
    assert payload["totals"]["total"] == 165
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


def test_cli_status_filters_by_harness_and_source_session(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    session_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    create_source_db(source_db)
    create_pi_session_file(session_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "pi",
            "--source",
            str(session_file),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "run",
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
            "source_cost_usd": "0.0",
            "actual_cost_usd": "0",
            "virtual_cost_usd": "0",
            "savings_usd": "0",
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


def test_cli_config_prices_lists_virtual_prices(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "prices", "--provider", "openai"],
    )

    assert result.exit_code == 0, result.output
    assert "table" in result.output
    assert "provider" in result.output
    assert "model" in result.output
    assert "gpt-5-mini" in result.output


def test_cli_config_prices_json_includes_effective_fallback_prices(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "config",
            "prices",
            "--model",
            "gpt-5-mini",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["cache_write_usd_per_1m"] is None
    assert payload[0]["effective_cache_write_usd_per_1m"] == 0.25
    assert payload[0]["reasoning_usd_per_1m"] is None
    assert payload[0]["effective_reasoning_usd_per_1m"] == 2.0


def test_cli_config_prices_filters_provider_model_query_category_release(
    tmp_path,
) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "config",
            "prices",
            "--table",
            "all",
            "--provider",
            "openai",
            "--model",
            "GPT 5 mini",
            "--query",
            "mini",
            "--category",
            "Lightweight",
            "--release-status",
            "ga",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["provider"] == "openai"
    assert payload[0]["model"] == "gpt-5-mini"


def test_cli_config_prices_rejects_invalid_filter_values(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    bad_table = runner.invoke(
        app,
        ["--config", str(config_path), "config", "prices", "--table", "bogus"],
    )
    bad_sort = runner.invoke(
        app,
        ["--config", str(config_path), "config", "prices", "--sort", "bogus"],
    )

    assert bad_table.exit_code == 1
    assert "Unsupported --table" in bad_table.output
    assert bad_sort.exit_code == 1
    assert "Unsupported --sort" in bad_sort.output


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
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "copilot",
            "--source",
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
            "run",
            "status",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["source_cost_usd"] in ("0", "0.0")
    assert payload["totals"]["actual_cost_usd"] in ("0", "0.0")
    assert float(payload["totals"]["virtual_cost_usd"]) > 0.0
    assert payload["totals"]["savings_usd"] == payload["totals"]["virtual_cost_usd"]


def test_cli_status_human_output_contains_actual_virtual_and_savings(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "import",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(app, ["--db", str(state_db), "run", "status", "1"])

    assert result.exit_code == 0, result.output
    assert "Costs" in result.output
    assert "actual:" in result.output
    assert "virtual:" in result.output
    assert "savings:" in result.output


def test_cli_status_human_output_lists_unconfigured_models(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "run", "status", "1"],
    )

    assert result.exit_code == 0, result.output
    assert "Unconfigured models" in result.output
    assert "openai-codex/gpt-5.2-codex" in result.output
    assert result.output.index("Unconfigured models") < result.output.index(
        "By harness"
    )


def test_cli_status_json_contains_unconfigured_models(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["unconfigured_models"] == [
        {
            "required": ["virtual"],
            "harness": "opencode",
            "provider_id": "openai-codex",
            "model_id": "gpt-5.2-codex",
            "thinking_level": None,
            "message_count": 1,
            "input": 400,
            "output": 40,
            "reasoning": 10,
            "cache_read": 0,
            "cache_write": 0,
            "total": 450,
        }
    ]


def test_cli_status_price_state_unpriced_filters_model_table(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--json",
            "--price-state",
            "unpriced",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["display_filters"]["price_state"] == "unpriced"
    assert [row["model_id"] for row in payload["by_model"]] == ["gpt-5.2-codex"]
    assert payload["unconfigured_models"][0]["model_id"] == "gpt-5.2-codex"


def test_cli_status_sort_and_limit_apply_to_model_rows_only(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--json",
            "--sort",
            "provider",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["display_filters"] == {
        "price_state": "all",
        "min_messages": None,
        "min_tokens": None,
        "sort": "provider",
        "limit": 1,
    }
    assert [row["provider_id"] for row in payload["by_model"]] == ["anthropic"]
    assert payload["totals"]["total"] == 2300
    assert payload["unconfigured_models"][0]["provider_id"] == "openai-codex"


def test_cli_usage_supports_price_state_sort_limit(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "summary",
            "--json",
            "--price-state",
            "unpriced",
            "--sort",
            "tokens",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["display_filters"]["sort"] == "tokens"
    assert payload["display_filters"]["limit"] == 1
    assert [row["model_id"] for row in payload["by_model"]] == ["gpt-5.2-codex"]
    assert payload["totals"]["total"] == 2300


def test_cli_pricing_list_used_only_reports_used_models(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "pricing",
            "list",
            "--used-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [row["provider_id"] for row in payload] == ["anthropic", "openai-codex"]
    assert [row["model_id"] for row in payload] == [
        "claude-sonnet-4",
        "gpt-5.2-codex",
    ]


def test_cli_pricing_list_missing_only_reports_unconfigured_used_models(
    tmp_path,
) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "pricing",
            "list",
            "--missing-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == [
        {
            "required": ["virtual"],
            "harness": "opencode",
            "provider_id": "openai-codex",
            "model_id": "gpt-5.2-codex",
            "thinking_level": None,
            "message_count": 1,
            "input": 400,
            "output": 40,
            "reasoning": 10,
            "cache_read": 0,
            "cache_write": 0,
            "total": 450,
        }
    ]


def test_cli_status_rejects_invalid_display_filter_values(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    bad_price_state = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--price-state",
            "bogus",
        ],
    )
    bad_sort = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--sort",
            "bogus",
        ],
    )

    assert bad_price_state.exit_code == 1
    assert "Unsupported --price-state" in bad_price_state.output
    assert bad_sort.exit_code == 1
    assert "Unsupported --sort" in bad_sort.output


def test_cli_pi_sessions_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd" / "session.jsonl")

    result = runner.invoke(
        app,
        ["source-sessions", "pi", "--pi-path", str(session_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "pi_ses_001" in result.output
    assert "165" in result.output
    assert "2026-" in result.output


def test_cli_sessions_codex_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)

    result = runner.invoke(
        app,
        ["source-sessions", "codex", "--codex-path", str(codex_file)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "session-001" in result.output
    assert "155" in result.output
    assert "2026-" in result.output


def test_cli_sessions_amp_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    source_path = tmp_path / "amp" / "threads"
    create_amp_source(source_path)

    result = runner.invoke(
        app,
        ["source-sessions", "amp", "--amp-path", str(source_path)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "thread-1" in result.output
    assert "190" in result.output
    assert "2026-" in result.output


def test_cli_sessions_copilot_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    result = runner.invoke(
        app,
        ["source-sessions", "copilot", "--copilot-file", str(copilot_file)],
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
        [
            "source-sessions",
            "pi",
            "--pi-path",
            str(session_dir),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "claude-3-5-sonnet" in result.output


def test_cli_sessions_codex_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)

    result = runner.invoke(
        app,
        [
            "source-sessions",
            "codex",
            "--codex-path",
            str(codex_file),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "gpt-5.2-codex" in result.output


def test_cli_sessions_goose_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    goose_db = tmp_path / "goose" / "sessions.db"
    create_goose_source_db(goose_db)

    result = runner.invoke(
        app,
        [
            "source-sessions",
            "goose",
            "--goose-path",
            str(goose_db),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "claude-sonnet-4-20250514" in result.output


def test_cli_sessions_amp_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    source_path = tmp_path / "amp" / "threads"
    create_amp_source(source_path)

    result = runner.invoke(
        app,
        [
            "source-sessions",
            "amp",
            "--amp-path",
            str(source_path),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Amp source session thread-1" in result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "claude-sonnet-4-0" in result.output


def test_cli_watch_goose_is_not_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["watch", "goose"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_cli_sessions_codex_supports_limit_sort_and_columns(tmp_path) -> None:
    runner = CliRunner()
    codex_dir = tmp_path / "codex"
    create_codex_session_file(codex_dir / "session-001.jsonl")
    write_jsonl_rows(
        codex_dir / "session-002.jsonl",
        [
            {
                "timestamp": "2026-01-01T00:00:02Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "model": "gpt-5.2-codex",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 200,
                            "output_tokens": 20,
                        }
                    },
                },
            }
        ],
    )

    result = runner.invoke(
        app,
        [
            "source-sessions",
            "codex",
            "--codex-path",
            str(codex_dir),
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
    assert "session-002" in result.output
    assert "session-001" not in result.output


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
            "source-sessions",
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
                "source-sessions",
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
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )

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


def test_cli_watch_codex_exits_cleanly_on_ctrl_c(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "watch",
            "codex",
            "--codex-path",
            str(codex_file),
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Stopped watching Codex." in result.output
    assert "rows imported: 1" in result.output


def test_cli_watch_amp_exits_cleanly_on_ctrl_c(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_path = tmp_path / "amp" / "threads"
    create_amp_source(source_path)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "watch",
            "amp",
            "--amp-path",
            str(source_path),
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Stopped watching Amp." in result.output
    assert "rows imported: 1" in result.output


def test_cli_import_pi_without_path_or_env_fails(tmp_path, monkeypatch) -> None:
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
            "import",
            "--harness",
            "pi",
            "--source",
            str(tmp_path / "missing_sessions"),
        ],
    )

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
