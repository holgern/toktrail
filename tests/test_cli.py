from __future__ import annotations

import json
import re
import shlex
import sqlite3
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.helpers import (
    VALID_ASSISTANT,
    create_opencode_db,
    insert_message,
)
from toktrail.api.sessions import init_state, start_run
from toktrail.cli import app
from toktrail.db import (
    archive_tracking_session,
    connect,
    create_tracking_session,
    end_tracking_session,
    insert_usage_events,
    migrate,
)
from toktrail.models import TokenBreakdown, UsageEvent

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _toml_path_value(path: Path) -> str:
    return str(path).replace("\\", "/")


@pytest.fixture(autouse=True)
def isolate_default_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    default_config = tmp_path / "default-config.toml"
    default_config.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(tmp_path / "missing-opencode.db")}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(default_config))
    for key in (
        "TOKTRAIL_PI_SESSIONS",
        "TOKTRAIL_COPILOT_FILE",
        "COPILOT_OTEL_FILE_EXPORTER_PATH",
        "TOKTRAIL_COPILOT_OTEL_DIR",
        "TOKTRAIL_CODE_SESSIONS",
        "CODE_HOME",
        "TOKTRAIL_CODEX_SESSIONS",
        "CODEX_HOME",
        "TOKTRAIL_GOOSE_SESSIONS",
        "GOOSE_PATH_ROOT",
        "TOKTRAIL_HARNESSBRIDGE_SESSIONS",
        "TOKTRAIL_DROID_SESSIONS",
        "TOKTRAIL_AMP_THREADS",
    ):
        monkeypatch.delenv(key, raising=False)


def write_jsonl_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )


def _future_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000


def _future_iso() -> str:
    return (
        datetime.fromtimestamp(_future_ms() / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _stamp_opencode_message(message: dict[str, object]) -> dict[str, object]:
    stamped = deepcopy(message)
    created = float(_future_ms())
    stamped["time"] = {
        "created": created,
        "completed": created + 500.0,
    }
    return stamped


def create_source_db(path: Path) -> None:
    conn = create_opencode_db(path)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=_stamp_opencode_message(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()


def _rich_is_available() -> bool:
    try:
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _assert_rich_result_or_missing_dependency(result) -> None:
    if _rich_is_available():
        assert result.exit_code == 0, result.output
        assert any(ch in result.output for ch in "┏┌╭"), result.output
    else:
        assert result.exit_code != 0
        assert "Rich output requires installing toktrail[rich]." in result.output


def make_cli_usage_event(
    dedup_suffix: str,
    *,
    created_ms: int,
    tokens: TokenBreakdown,
    source_session_id: str = "ses-1",
) -> UsageEvent:
    return UsageEvent(
        harness="opencode",
        source_session_id=source_session_id,
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"dedup-{dedup_suffix}",
        global_dedup_key=f"global-{dedup_suffix}",
        fingerprint_hash=f"fp-{dedup_suffix}",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
        thinking_level=None,
        agent="build",
        created_ms=created_ms,
        completed_ms=created_ms + 1,
        tokens=tokens,
        source_cost_usd=Decimal("0"),
        raw_json=None,
    )


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
            _future_iso(),
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
                "providerLockTimestamp": _future_iso(),
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


def create_harnessbridge_source(path: Path) -> None:
    write_jsonl_rows(
        path,
        [
            {
                "type": "session",
                "id": "hb-session-1",
                "accounting": "primary",
                "started_ms": _future_ms(),
            },
            {
                "type": "usage",
                "id": "evt-1",
                "harness": "pi",
                "accounting": "primary",
                "provider_id": "anthropic",
                "model_id": "claude-sonnet-4",
                "created_ms": _future_ms(),
                "tokens": {"input": 10, "output": 5},
                "source_cost_usd": "0.12",
            },
        ],
    )


def create_amp_source(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "thread-1.json").write_text(
        json.dumps(
            {
                "id": "thread-1",
                "created": _future_ms(),
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


def test_cli_statusline_top_level_matches_usage_wrapper(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "statusline",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=1_200, output=300, cache_read=500),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    top_level = runner.invoke(
        app,
        ["--db", str(state_db), "statusline", "--no-refresh"],
    )
    legacy = runner.invoke(
        app, ["--db", str(state_db), "usage", "statusline", "--no-refresh"]
    )

    assert top_level.exit_code == 0, top_level.output
    assert legacy.exit_code == 0, legacy.output
    assert top_level.output == legacy.output
    assert "opencode" in top_level.output
    assert "tok" in top_level.output


def test_cli_statusline_json_shape(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "json",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=400, output=100, cache_read=200),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        app, ["--db", str(state_db), "statusline", "--no-refresh", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "statusline"
    assert payload["harness"] == "opencode"
    assert payload["tokens"]["total"] == 500
    assert "line" in payload
    assert payload["cache"]["cached_tokens"] == 200


def test_cli_statusline_harnessbridge_refresh_always_bypasses_cached_empty_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "harnessbridge-sessions"
    source_file = source_dir / "hb.jsonl"
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["harnessbridge"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
harnessbridge = "{_toml_path_value(source_dir)}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(config_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))

    runner = CliRunner()
    init_result = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init_result.exit_code == 0, init_result.output

    empty_result = runner.invoke(
        app,
        ["--db", str(state_db), "statusline", "--harness", "harnessbridge"],
    )
    assert empty_result.exit_code == 0, empty_result.output
    assert "no usage sources" in empty_result.output

    write_jsonl_rows(
        source_file,
        [
            {
                "type": "session",
                "id": "hb_20260513T161435Z_8f434346",
                "harness": "pi",
                "accounting": "primary",
                "started_at": "2026-05-13T16:14:35.963000+00:00",
            },
            {
                "type": "usage",
                "id": "usage_0001",
                "harness": "pi",
                "timestamp": "2026-05-13T16:14:44.720215+00:00",
                "provider": "zai",
                "model": "zai/glm-5.1",
                "dedup_key": "harnessbridge:hb_20260513T161435Z_8f434346:usage_0001",
                "tokens": {"input": 815, "output": 48, "cacheRead": 1024},
                "cost": {"total": "0.0009686"},
            },
        ],
    )

    refreshed_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "statusline",
            "--harness",
            "harnessbridge",
            "--refresh",
            "always",
        ],
    )

    assert refreshed_result.exit_code == 0, refreshed_result.output
    assert "no usage sources" not in refreshed_result.output
    assert "pi" in refreshed_result.output
    assert "glm-5.1" in refreshed_result.output


def test_cli_statusline_shows_compact_unpriced_marker(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "unpriced",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=400, output=100),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["--db", str(state_db), "statusline", "--no-refresh"])

    assert result.exit_code == 0, result.output
    assert "?1" in result.output


def test_cli_statusline_renders_stale_element_when_configured(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[statusline]
elements = ["harness", "stale", "tokens"]

[statusline.cache]
stale_after_secs = 60
""".strip(),
        encoding="utf-8",
    )
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "stale",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=400, output=100),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "statusline",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "stale " in result.output
    assert "opencode" in result.output


def test_cli_statusline_test_outputs_diagnostics(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "diag",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=700, output=150, cache_read=50),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--db", str(state_db), "statusline", "test", "--no-refresh"],
    )

    assert result.exit_code == 0, result.output
    assert "Source:" in result.output
    assert "Model:" in result.output
    assert "Output cache: miss" in result.output
    assert "Line:" in result.output


def test_cli_statusline_install_starship_prints_snippet() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["statusline", "install", "--target", "starship"])

    assert result.exit_code == 0, result.output
    assert "[custom.toktrail]" in result.output
    assert "toktrail statusline --no-refresh" in result.output


def test_cli_statusline_config_show_and_set(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    runner = CliRunner()

    set_result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "statusline",
            "config",
            "set",
            "basis",
            "source",
        ],
    )
    show_result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "statusline",
            "config",
            "show",
        ],
    )

    assert set_result.exit_code == 0, set_result.output
    assert 'basis = "source"' in config_path.read_text(encoding="utf-8")
    assert show_result.exit_code == 0, show_result.output
    assert "basis:         source" in show_result.output


def create_thinking_source_db(path: Path) -> None:
    conn = create_opencode_db(path)
    high = _stamp_opencode_message(VALID_ASSISTANT)
    high["thinkingLevel"] = "high"
    insert_message(conn, row_id="row-1", session_id="ses-1", data=high)
    low = _stamp_opencode_message(VALID_ASSISTANT)
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
        data=_stamp_opencode_message(VALID_ASSISTANT),
    )
    unpriced = _stamp_opencode_message(VALID_ASSISTANT)
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
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )
    return runner, state_db, config_path


def create_copilot_file(path: Path) -> None:
    future_ms = _future_ms()
    write_jsonl_rows(
        path,
        [
            {
                "type": "span",
                "traceId": "trace-1",
                "spanId": "span-1",
                "name": "chat claude-sonnet-4",
                "endTime": [future_ms // 1000, (future_ms % 1000) * 1_000_000],
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


def create_codex_session_file(path: Path) -> None:
    future_iso = (
        datetime.fromtimestamp(_future_ms() / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    write_jsonl_rows(
        path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "source": "interactive",
                    "model_provider": "openai",
                    "agent_nickname": "builder",
                },
            },
            {
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5.2-codex",
                },
            },
            {
                "timestamp": future_iso,
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 120,
                            "cached_input_tokens": 20,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 5,
                        },
                        "last_token_usage": {
                            "input_tokens": 120,
                            "cached_input_tokens": 20,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 5,
                        },
                    },
                },
            },
        ],
    )


def create_pi_session_file(path: Path) -> None:
    future = datetime.fromtimestamp(_future_ms() / 1000, tz=timezone.utc)
    session_ts = future.isoformat().replace("+00:00", "Z")
    message_ts = (future.replace(microsecond=0)).isoformat().replace("+00:00", "Z")
    write_jsonl_rows(
        path,
        [
            {
                "type": "session",
                "id": "pi_ses_001",
                "timestamp": session_ts,
                "cwd": "/tmp",
            },
            {
                "type": "message",
                "id": "msg_001",
                "parentId": None,
                "timestamp": message_ts,
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


def test_cli_opencode_sessions_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    result = runner.invoke(
        app,
        ["sources", "sessions", "opencode", "--source", str(source_db)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "ses-1" in result.output
    assert "1,500" in result.output
    assert "202" in result.output


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
            "tokens": 1500,
            "warning": "",
            "config_key": "opencode_db",
            "id_prefix": "opencode",
            "watch_subdirs": [],
            "file_based": True,
            "effective_roots": [str(source_db)],
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
opencode = "{_toml_path_value(missing_db)}"
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


def test_cli_sessions_droid_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    source_path = tmp_path / "factory" / "sessions"
    create_droid_source(source_path)

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "droid",
            "--source",
            str(source_path),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Droid source session droid-1" in result.output
    assert "token usage:" in result.output


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
            "refresh",
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


def test_cli_usage_today_reports_unscoped_refreshes(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime.now(tz=tz),
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--utc",
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["session"] is None
    assert payload["filters"]["period"] == "today"
    assert payload["filters"]["timezone"] == "UTC"
    assert payload["totals"]["total"] == 1500
    assert "refresh" not in payload


def test_cli_usage_no_refresh_uses_existing_state_only(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime.now(tz=tz),
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--utc",
            "--json",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["total"] == 0


def test_cli_usage_runs_excludes_archived_by_default(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        run_id = create_tracking_session(conn, "archived", started_at_ms=1_000)
        insert_usage_events(
            conn,
            run_id,
            [
                make_cli_usage_event(
                    "archived",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=10, output=5),
                )
            ],
        )
        end_tracking_session(conn, run_id, ended_at_ms=1_100)
        archive_tracking_session(conn, run_id, archived_at_ms=1_200)
    finally:
        conn.close()

    default_result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--json", "--no-refresh"],
    )
    archived_result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--json", "--all", "--no-refresh"],
    )

    assert default_result.exit_code == 0, default_result.output
    assert archived_result.exit_code == 0, archived_result.output
    assert json.loads(default_result.output)["runs"] == []
    assert len(json.loads(archived_result.output)["runs"]) == 1


def test_cli_usage_refresh_details_prints_compact_refresh_summary(tmp_path) -> None:
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
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--refresh-details",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed usage" in result.output
    assert "inserted" in result.output
    assert "source path" not in result.output.lower()


def test_cli_usage_refresh_details_json_wraps_refresh_and_report(tmp_path) -> None:
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
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--json",
            "--refresh-details",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "refresh" in payload
    assert "report" in payload
    assert payload["report"]["totals"]["total"] == 1500


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
opencode = "{_toml_path_value(source_db)}"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "--config", str(config_path), "refresh"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--since",
            "2000-01-01T00:00:00Z",
            "--until",
            "2100-01-01T00:00:00Z",
            "--utc",
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["filters"]["since_ms"] == 946684800000
    assert payload["filters"]["until_ms"] == 4102444800000
    assert payload["filters"]["timezone"] == "UTC"
    assert payload["totals"]["total"] == 1500


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
            "refresh",
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
            "refresh",
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
    assert payload["filters"]["harness"] == "pi"
    assert payload["filters"]["source_session_id"] == "pi_ses_001"
    assert isinstance(payload["filters"]["since_ms"], int)
    assert payload["filters"]["since_ms"] == payload["session"]["started_at_ms"]
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 50
    assert payload["by_harness"] == [
        {
            "harness": "pi",
            "message_count": 1,
            "input": 100,
            "output": 50,
            "reasoning": 0,
            "cache_read": 10,
            "cache_write": 5,
            "cache_output": 0,
            "total": 150,
            "prompt_total": 115,
            "output_total": 50,
            "accounting_total": 165,
            "source_cost_usd": "0",
            "actual_cost_usd": "0",
            "virtual_cost_usd": "0",
            "savings_usd": "0",
            "unpriced_count": 1,
        }
    ]


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


def test_cli_config_path_init_and_validate(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    prices_path = config_path.with_name("prices.toml")
    prices_dir = config_path.with_name("prices")
    subscriptions_path = config_path.with_name("subscriptions.toml")

    path_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "path"],
    )
    assert path_result.exit_code == 0, path_result.output
    assert f"config:        {config_path}" in path_result.output
    assert f"prices:        {prices_path}" in path_result.output
    assert f"prices dir:    {prices_dir}" in path_result.output
    assert f"subscriptions: {subscriptions_path}" in path_result.output

    init_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )
    assert init_result.exit_code == 0, init_result.output
    assert config_path.exists()
    assert prices_path.exists()
    assert prices_dir.exists()
    assert subscriptions_path.exists()

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
        ["--config", str(config_path), "prices", "list", "--provider", "openai"],
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
            "prices",
            "list",
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
            "prices",
            "list",
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
        ["--config", str(config_path), "prices", "list", "--table", "bogus"],
    )
    bad_sort = runner.invoke(
        app,
        ["--config", str(config_path), "prices", "list", "--sort", "bogus"],
    )

    assert bad_table.exit_code == 1
    assert "Unsupported --table" in bad_table.output
    assert bad_sort.exit_code == 1
    assert "Unsupported --sort" in bad_sort.output


def test_cli_root_prices_and_subscriptions_overrides(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    prices_path = tmp_path / "prices.toml"
    subscriptions_path = tmp_path / "subscriptions.toml"
    config_path.write_text(
        """
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false
""".strip(),
        encoding="utf-8",
    )
    prices_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5-mini"
input_usd_per_1m = 0.25
output_usd_per_1m = 2.0
""".strip(),
        encoding="utf-8",
    )
    subscriptions_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]

[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    prices_result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "--prices",
            str(prices_path),
            "prices",
            "list",
            "--provider",
            "openai",
            "--json",
        ],
    )
    assert prices_result.exit_code == 0, prices_result.output
    prices_payload = json.loads(prices_result.output)
    assert prices_payload[0]["provider"] == "openai"

    show_result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "--prices",
            str(prices_path),
            "--subscriptions",
            str(subscriptions_path),
            "config",
            "show",
        ],
    )
    assert show_result.exit_code == 0, show_result.output
    assert f"subs path:       {subscriptions_path}" in show_result.output


def test_cli_pricing_parse_openai_standard_to_stdout(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--out",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[[pricing.virtual]]" in result.output
    assert 'provider = "openai"' in result.output


def test_cli_pricing_parse_defaults_to_provider_file(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    runner.invoke(app, ["--config", str(config_path), "config", "init"])
    target_path = config_path.with_name("prices") / "openai.toml"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert target_path.exists()
    assert f"Wrote prices TOML: {target_path}" in result.output


def test_cli_pricing_parse_accepts_output_alias(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "openai-pricing.jsx"
    output_path = tmp_path / "custom-openai.toml"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exists()


def test_cli_pricing_parse_output_dash_prints_stdout(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--output",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[[pricing.virtual]]" in result.output
    assert not (config_path.with_name("prices") / "openai.toml").exists()


def test_cli_pricing_parse_json_writes_file(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["wrote"] is True
    assert (config_path.with_name("prices") / "openai.toml").exists()


def test_cli_pricing_parse_json_dry_run_does_not_write(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--json",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["wrote"] is False
    assert payload["dry_run"] is True
    assert not (config_path.with_name("prices") / "openai.toml").exists()


def test_cli_pricing_parse_zai_to_stdout(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "zai-pricing.md"
    input_path.write_text(
        """
### Text Models
| Model | Input | Cached Input | Output |
| --- | --- | --- | --- |
| GLM-5.1 | $1.4 | $0.26 | $4.4 |
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "zai",
            "--input",
            str(input_path),
            "--out",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert 'provider = "zai"' in result.output
    assert 'model = "glm-5.1"' in result.output


def test_cli_pricing_parse_opencode_go_actual_to_stdout(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "opencode-go.txt"
    input_path.write_text(
        """
Model        Input    Output    Cached Read    Cached Write
GLM 5.1      $1.40    $4.40     $0.26          -
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "opencode-go",
            "--table",
            "actual",
            "--input",
            str(input_path),
            "--out",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[[pricing.actual]]" in result.output
    assert 'provider = "opencode-go"' in result.output


def test_cli_pricing_parse_github_copilot_to_stdout(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "github-copilot.md"
    input_path.write_text(
        """
OpenAI
Model\tRelease status\tCategory\tInput\tCached input\tOutput
GPT-5.2\tGA\tVersatile\t$1.75\t$0.175\t$14.00
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "github-copilot",
            "--input",
            str(input_path),
            "--out",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert 'provider = "github-copilot"' in result.output
    assert 'model = "gpt-5.2"' in result.output


def test_cli_pricing_parse_merge_replaces_provider_rows(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "openai-pricing.jsx"
    prices_path = tmp_path / "prices.toml"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 6, 0.6, 36] ]}',
        encoding="utf-8",
    )
    prices_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30

[[pricing.virtual]]
provider = "anthropic"
model = "claude-sonnet-4"
input_usd_per_1m = 3
output_usd_per_1m = 15
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--out",
            str(prices_path),
            "--merge",
        ],
    )

    assert result.exit_code == 0, result.output
    merged = prices_path.read_text(encoding="utf-8")
    assert "input_usd_per_1m = 6.0" in merged
    assert 'provider = "anthropic"' in merged


def test_cli_pricing_parse_context_tier_preserves_variants_without_warning(
    tmp_path,
) -> None:
    runner = CliRunner()
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        """
TextTokenPricingTables tier="standard" rows={[
  ["gpt-5.5 (<272K context length)", 5, 0.5, 30],
  ["GPT 5.5 (> 272K tokens)", 6, 0.6, 36],
]}
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["warnings"] == []
    assert payload["price_count"] == 2


def test_cli_config_prices_loads_provider_directory(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(tmp_path / "missing-opencode.db")}"
""".strip(),
        encoding="utf-8",
    )
    provider_path = config_path.with_name("prices") / "openai.toml"
    provider_path.parent.mkdir(parents=True, exist_ok=True)
    provider_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "list",
            "--provider",
            "openai",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload
    assert payload[0]["provider"] == "openai"
    assert "context_min_tokens" in payload[0]
    assert "context_max_tokens" in payload[0]
    assert payload[0]["context_basis"] == "prompt_like"


def test_cli_config_show_lists_price_paths(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(app, ["--config", str(config_path), "config", "init"])
    provider_path = config_path.with_name("prices") / "openai.toml"
    provider_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--config", str(config_path), "config", "show"])

    assert result.exit_code == 0, result.output
    assert f"prices dir:      {config_path.with_name('prices')}" in result.output
    assert "price files:" in result.output
    assert str(provider_path) in result.output


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
            "refresh",
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
            "refresh",
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
            "cache_output": 0,
            "total": 440,
            "prompt_total": 400,
            "output_total": 40,
            "accounting_total": 450,
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
    assert payload["totals"]["total"] == 1940
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
    assert payload["totals"]["total"] == 1940


def test_cli_pricing_list_used_only_reports_used_models(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "prices",
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
            "prices",
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
            "cache_output": 0,
            "total": 440,
            "prompt_total": 400,
            "output_total": 40,
            "accounting_total": 450,
        }
    ]


def test_cli_pricing_list_used_only_auto_refreshes_configured_sources(tmp_path) -> None:
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
    stale = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "prices",
            "list",
            "--used-only",
            "--json",
            "--no-refresh",
        ],
    )
    assert stale.exit_code == 0, stale.output
    assert json.loads(stale.output) == []

    refreshed = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "prices",
            "list",
            "--used-only",
            "--json",
        ],
    )
    assert refreshed.exit_code == 0, refreshed.output
    payload = json.loads(refreshed.output)
    assert len(payload) > 0


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
        ["sources", "sessions", "pi", "--source", str(session_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "pi_ses_001" in result.output
    assert "150" in result.output
    assert "2026-" in result.output


def test_cli_sessions_codex_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)

    result = runner.invoke(
        app,
        ["sources", "sessions", "codex", "--source", str(codex_file)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "session-001" in result.output
    assert "130" in result.output
    assert "2026-" in result.output


def test_cli_sessions_code_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    code_file = tmp_path / "code" / "session-001.jsonl"
    create_codex_session_file(code_file)

    result = runner.invoke(
        app,
        ["sources", "sessions", "code", "--source", str(code_file)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "session-001" in result.output
    assert "130" in result.output
    assert "2026-" in result.output


def test_cli_sessions_amp_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    source_path = tmp_path / "amp" / "threads"
    create_amp_source(source_path)

    result = runner.invoke(
        app,
        ["sources", "sessions", "amp", "--source", str(source_path)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "thread-1" in result.output
    assert "120" in result.output
    assert "2026-" in result.output


def test_cli_sessions_copilot_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    result = runner.invoke(
        app,
        ["sources", "sessions", "copilot", "--source", str(copilot_file)],
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
        ["pi", "sessions", "--source", str(session_dir)],
        ["copilot", "sessions", "--source", str(copilot_file)],
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
            "sources",
            "sessions",
            "pi",
            "--source",
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
            "sources",
            "sessions",
            "codex",
            "--source",
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


def test_cli_sessions_code_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    code_file = tmp_path / "code" / "session-001.jsonl"
    create_codex_session_file(code_file)

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "code",
            "--source",
            str(code_file),
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
            "sources",
            "sessions",
            "goose",
            "--source",
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
            "sources",
            "sessions",
            "amp",
            "--source",
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
            "sources",
            "sessions",
            "codex",
            "--source",
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
            "sources",
            "sessions",
            "pi",
            "--source",
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
                "sources",
                "sessions",
                "copilot",
                "--source",
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


def test_cli_watch_imports_configured_harnesses_and_prints_token_delta(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    pi_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    config_path = tmp_path / "toktrail.toml"

    conn = create_opencode_db(opencode_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    create_pi_session_file(pi_file)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode", "pi"]
missing_source = "error"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
pi = "{_toml_path_value(pi_file)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    start_run(state_db, name="watch-test", started_at_ms=0)

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "watch",
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Watching configured harnesses" in result.output
    assert "tokens" in result.output
    assert "input" in result.output or "in=" in result.output
    assert "output" in result.output or "out=" in result.output
    assert "opencode" in result.output.lower()
    assert "rows imported" not in result.output
    assert "rows seen" not in result.output


def test_cli_watch_does_not_print_idle_duplicate_imports(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"

    conn = create_opencode_db(opencode_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "error"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    start_run(state_db, name="watch-idle", started_at_ms=0)

    sleep_calls = 0

    def sleep_then_interrupt(_interval: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", sleep_then_interrupt)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "watch",
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("+1 msgs") == 1, result.output
    assert "rows imported" not in result.output


def test_cli_watch_json_outputs_delta_events_only(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"

    conn = create_opencode_db(opencode_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "error"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    start_run(state_db, name="watch-json", started_at_ms=0)

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "watch",
            "--json",
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    assert len(events) >= 1
    assert all(event["type"] == "usage_delta" for event in events)
    assert events[0]["delta"]["total"] > 0
    by_harness = events[0]["by_harness"]
    assert len(by_harness) == 1
    assert by_harness[0]["harness"] == "opencode"
    assert by_harness[0]["input"] > 0
    assert by_harness[0]["output"] > 0
    assert by_harness[0]["cache_read"] > 0


def test_cli_watch_json_includes_cache_output_delta(
    tmp_path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    opencode_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"

    conn = create_opencode_db(opencode_db)
    payload = deepcopy(VALID_ASSISTANT)
    payload["tokens"] = {
        "input": 100,
        "output": 5,
        "reasoning": 0,
        "cache": {"read": 10, "write": 0, "output": 7},
    }
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=payload,
    )
    conn.commit()
    conn.close()

    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "error"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(opencode_db)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    start_run(state_db, name="watch-json-cache-output", started_at_ms=0)

    def interrupt_after_first_sleep(_interval: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("toktrail.cli.time.sleep", interrupt_after_first_sleep)
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "watch",
            "--json",
            "--interval",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    assert payloads
    assert payloads[0]["delta"]["cache_output"] == 7
    assert payloads[0]["by_harness"][0]["cache_output"] == 7


def create_opencode_go_source_db(path: Path) -> None:
    conn = create_opencode_db(path)
    opencode_go = deepcopy(VALID_ASSISTANT)
    opencode_go["id"] = "msg-opencode-go"
    opencode_go["providerID"] = "opencode-go"
    opencode_go["modelID"] = "opencode-go/deepseek-v4-pro"
    opencode_go["cost"] = 3.2
    opencode_go["tokens"] = {
        "input": 120,
        "output": 30,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }
    insert_message(conn, row_id="row-opencode-go", session_id="ses-1", data=opencode_go)
    conn.commit()
    conn.close()


def create_opencode_cache_analysis_source_db(path: Path) -> None:
    conn = create_opencode_db(path)
    first = _stamp_opencode_message(VALID_ASSISTANT)
    first["id"] = "msg-hit"
    first["providerID"] = "opencode-go"
    first["modelID"] = "glm-5.1"
    first["tokens"] = {
        "input": 30000,
        "output": 50,
        "reasoning": 0,
        "cache": {"read": 120000, "write": 0, "output": 0},
    }
    first["cost"] = 0.04
    second = _stamp_opencode_message(VALID_ASSISTANT)
    second["id"] = "msg-miss"
    second["providerID"] = "opencode-go"
    second["modelID"] = "glm-5.1"
    second["tokens"] = {
        "input": 150000,
        "output": 50,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0, "output": 0},
    }
    second["cost"] = 0.21
    insert_message(conn, row_id="row-1", session_id="ses-cache", data=first)
    insert_message(conn, row_id="row-2", session_id="ses-cache", data=second)
    conn.commit()
    conn.close()


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


def create_zai_source_db(path: Path, *, source_cost: float = 0.0) -> None:
    conn = create_opencode_db(path)
    zai_event = deepcopy(VALID_ASSISTANT)
    zai_event["id"] = "msg-zai"
    zai_event["providerID"] = "zai"
    zai_event["modelID"] = "zai/glm-4.5"
    zai_event["cost"] = source_cost
    zai_event["tokens"] = {
        "input": 1_000_000,
        "output": 100_000,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }
    insert_message(conn, row_id="row-zai", session_id="ses-1", data=zai_event)
    conn.commit()
    conn.close()


def write_subscriptions_config(path: Path) -> None:
    path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "UTC"
quota_cost_basis = "source"
fixed_cost_usd = 10
fixed_cost_period = "monthly"
fixed_cost_reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "weekly"
limit_usd = 50
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "monthly"
limit_usd = 200
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )


def test_cli_subscriptions_auto_refreshes_before_summarizing(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    stale = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--json",
            "--no-refresh",
            "--now-ms",
            "1700000000000",
        ],
    )
    assert stale.exit_code == 0, stale.output
    stale_payload = json.loads(stale.output)
    stale_used = sum(
        float(period["used_usd"])
        for sub in stale_payload["subscriptions"]
        for period in sub["periods"]
    )
    assert stale_used == 0.0

    refreshed = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--json",
            "--now-ms",
            "1700000000000",
        ],
    )
    assert refreshed.exit_code == 0, refreshed.output
    refreshed_payload = json.loads(refreshed.output)
    refreshed_used = sum(
        float(period["used_usd"])
        for sub in refreshed_payload["subscriptions"]
        for period in sub["periods"]
    )
    assert refreshed_used > 0.0


def test_cli_usage_summary_human_output_contains_by_provider(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "test"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(app, ["--db", str(state_db), "usage", "summary"])

    assert result.exit_code == 0, result.output
    assert "By provider" in result.output
    assert "By harness" in result.output

    provider_section = result.output.split("By provider", 1)[1].split("By harness", 1)[
        0
    ]
    harness_section = result.output.split("By harness", 1)[1].split("By model", 1)[0]
    assert "(none)" not in provider_section
    assert "(none)" not in harness_section


def test_cli_usage_today_plain_default_is_borderless(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "today", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "By provider" in result.output
    assert "By harness" in result.output
    assert "By model" in result.output
    assert "By activity" in result.output
    assert not any(ch in result.output for ch in "┏┌╭┳┬╮")


def test_cli_usage_today_rich_applies_to_table_sections(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "today", "--rich", "--no-refresh"],
    )
    _assert_rich_result_or_missing_dependency(result)
    if result.exit_code == 0:
        assert "By provider" in result.output
        assert "By harness" in result.output
        assert "By model" in result.output
        assert "By activity" in result.output


def test_cli_usage_summary_json_contains_by_provider(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "test"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(app, ["--db", str(state_db), "usage", "summary", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "by_provider" in payload
    assert payload["by_provider"][0]["provider_id"] == "anthropic"


def test_cli_subscriptions_prints_5h_window(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    write_subscriptions_config(config_path)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
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
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "OpenCode Go (opencode-go)" in result.output
    assert "Billing" in result.output
    assert "net savings" in result.output
    assert "5h" in result.output
    assert "weekly" in result.output
    assert "monthly" in result.output


def test_cli_subscriptions_provider_filter_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    write_subscriptions_config(config_path)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
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
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--provider",
            "opencode-go",
            "--timezone",
            "Europe/Berlin",
            "--json",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "generated_at_ms" in payload
    assert len(payload["subscriptions"]) == 1
    assert payload["subscriptions"][0]["subscription_id"] == "opencode-go"
    assert payload["subscriptions"][0]["usage_provider_ids"] == ["opencode-go"]
    assert payload["subscriptions"][0]["quota_cost_basis"] == "source"
    assert [period["period"] for period in payload["subscriptions"][0]["periods"]] == [
        "5h",
        "weekly",
        "monthly",
    ]
    assert "billing" in payload["subscriptions"][0]
    assert "status" in payload["subscriptions"][0]["periods"][0]
    assert "reset_mode" in payload["subscriptions"][0]["periods"][0]
    assert "reset_at" in payload["subscriptions"][0]["periods"][0]


def test_cli_subscriptions_period_filter_accepts_5h(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    write_subscriptions_config(config_path)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
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
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--period",
            "5h",
            "--json",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    periods = payload["subscriptions"][0]["periods"]
    assert [period["period"] for period in periods] == ["5h"]
    assert "billing" in payload["subscriptions"][0]


def test_cli_subscriptions_plan_id_can_cover_different_usage_provider(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "zai.db"
    config_path = tmp_path / "toktrail.toml"
    create_zai_source_db(source_db, source_cost=0.0)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"

[pricing]
[[pricing.virtual]]
provider = "zai"
model = "glm-4.5"
input_usd_per_1m = 4.0
output_usd_per_1m = 8.0

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "Europe/Berlin"
quota_cost_basis = "virtual"

[[subscriptions.windows]]
period = "5h"
limit_usd = 12
reset_mode = "first_use"
reset_at = "2023-11-01T00:00:00+00:00"
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
            "subscriptions",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Plan: Zai Coding Plan (zai-coding-plan)" in result.output
    assert "providers: zai" in result.output
    assert "active" in result.output


def test_cli_subscriptions_deduplicates_zero_cost_warnings_across_windows(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "zai.db"
    config_path = tmp_path / "toktrail.toml"
    create_zai_source_db(source_db, source_cost=0.0)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 12
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "weekly"
limit_usd = 60
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
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
            "subscriptions",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("zero cost for basis=source") == 1


def test_cli_subscriptions_prints_yearly_billing(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go-plan"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "UTC"
quota_cost_basis = "source"
fixed_cost_usd = 120
fixed_cost_period = "yearly"
fixed_cost_reset_at = "2026-01-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "monthly"
limit_usd = 200
reset_mode = "fixed"
reset_at = "2026-05-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
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
        ],
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--utc",
            "--now-ms",
            "1777802400000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "yearly" in result.output
    assert "2026-01-01..2027-01-01" in result.output


def test_cli_subscriptions_disabled_window_is_not_printed(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
enabled = false

[[subscriptions.windows]]
period = "weekly"
limit_usd = 50
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
enabled = true
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
            "subscriptions",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "weekly" in result.output
    assert "5h" not in result.output


def test_cli_subscriptions_first_use_waiting_human_output_is_clear(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "codex"
usage_providers = ["codex"]
display_name = "Codex"
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 20
reset_mode = "first_use"
reset_at = "2026-05-03T00:00:00+00:00"
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
            "subscriptions",
            "--now-ms",
            "1777802400000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "starts on first use" in result.output


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def test_cli_subscriptions_first_use_human_output_hides_reset_anchor(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "Asia/Singapore"
quota_cost_basis = "virtual"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "first_use"
reset_at = "2026-05-01T00:00:00+08:00"
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
            "subscriptions",
            "--timezone",
            "Europe/Berlin",
            "--now-ms",
            "1778000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "first_use @" not in result.output
    assert "reset_at" not in result.output
    assert "resets (Europe/Berlin)" in result.output


def test_cli_subscriptions_display_timezone_converts_first_use_window(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[pricing]
[[pricing.virtual]]
provider = "zai"
model = "glm-4.5"
input_usd_per_1m = 4.0
output_usd_per_1m = 8.0

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "Asia/Singapore"
quota_cost_basis = "virtual"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "first_use"
reset_at = "2026-05-01T00:00:00+08:00"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    conn = connect(state_db)
    try:
        insert_usage_events(
            conn,
            None,
            [
                UsageEvent(
                    harness="opencode",
                    source_session_id="ses-zai",
                    source_row_id="row-zai",
                    source_message_id="msg-zai",
                    source_dedup_key="dedup-zai",
                    global_dedup_key="global-zai",
                    fingerprint_hash="fp-zai",
                    provider_id="zai",
                    model_id="glm-4.5",
                    thinking_level=None,
                    agent="build",
                    created_ms=_ms("2026-05-05T23:37:00+08:00"),
                    completed_ms=_ms("2026-05-05T23:37:01+08:00"),
                    tokens=TokenBreakdown(input=1_000_000, output=100_000),
                    source_cost_usd=Decimal("0"),
                    raw_json=None,
                )
            ],
        )
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--timezone",
            "Europe/Berlin",
            "--now-ms",
            str(_ms("2026-05-06T00:00:00+08:00")),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Display timezone: Europe/Berlin" in result.output
    assert "plan timezone: Asia/Singapore" in result.output
    assert "2026-05-05 17:37" in result.output
    assert "2026-05-05 22:37" in result.output
    assert "2026-05-05 23:37" not in result.output


def test_cli_subscriptions_rejects_timezone_and_utc(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "subscriptions",
            "--timezone",
            "Europe/Berlin",
            "--utc",
            "--no-refresh",
        ],
    )

    assert result.exit_code != 0
    assert "Use either --timezone or --utc" in result.output


def test_cli_subscriptions_no_configured_subscriptions_is_clear(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(app, ["--db", str(state_db), "subscriptions"])

    assert result.exit_code == 0, result.output
    assert "No provider subscriptions configured." in result.output


def test_cli_subscriptions_unknown_provider_filter_is_clear(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    write_subscriptions_config(config_path)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
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
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--provider",
            "unknown-provider",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "No subscriptions matched provider unknown-provider." in result.output


def test_cli_sync_export_and_import_dry_run_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    import_db = tmp_path / "toktrail-import.db"
    source_db = tmp_path / "opencode.db"
    archive_path = tmp_path / "state.tar.gz"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    refresh_result = runner.invoke(
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
        ],
    )
    assert refresh_result.exit_code == 0, refresh_result.output

    export_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "export",
            "--out",
            str(archive_path),
            "--no-refresh",
        ],
    )
    assert export_result.exit_code == 0, export_result.output
    assert archive_path.exists()

    import_result = runner.invoke(
        app,
        [
            "--db",
            str(import_db),
            "sync",
            "import",
            str(archive_path),
            "--dry-run",
            "--json",
        ],
    )
    assert import_result.exit_code == 0, import_result.output
    payload = json.loads(import_result.output)
    assert payload["dry_run"] is True
    assert "runs_inserted" in payload
    assert "source_sessions_inserted" in payload
    assert "usage_events_inserted" in payload
    assert "usage_events_skipped" in payload
    assert "run_events_inserted" in payload
    assert "conflicts" in payload


def test_cli_usage_sessions_human_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "toktrail usage sessions" in result.output
    assert "Token usage:" in result.output
    assert "Costs:" in result.output


def test_cli_usage_sessions_last_human_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--last", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Token usage:") == 1


def test_cli_usage_sessions_limit_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--limit",
            "5",
            "--json",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "usage_sessions"
    assert isinstance(payload["sessions"], list)
    assert len(payload["sessions"]) <= 5


def test_cli_usage_sessions_breakdown(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--breakdown",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Breakdown:" in result.output


def test_cli_usage_sessions_filters_harness_and_source_session(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--harness",
            "opencode",
            "--source-session",
            "ses-1",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ses-1" in result.output


def test_cli_usage_sessions_no_refresh_uses_existing_state_only(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    # Import once
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    # --no-refresh should not import new rows
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "toktrail usage sessions" in result.output


def test_cli_usage_sessions_today_filters_to_current_day(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "today",
                    source_session_id="ses-today",
                    created_ms=int(
                        datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
                make_cli_usage_event(
                    "yesterday",
                    source_session_id="ses-yesterday",
                    created_ms=int(
                        datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
            ],
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2026, 5, 11, 12, 0, tzinfo=tz),
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--today",
            "--timezone",
            "UTC",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ses-today" in result.output
    assert "ses-yesterday" not in result.output


def test_cli_usage_sessions_yesterday_filters_to_previous_day(
    monkeypatch, tmp_path
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "today-2",
                    source_session_id="ses-today-2",
                    created_ms=int(
                        datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
                make_cli_usage_event(
                    "yesterday-2",
                    source_session_id="ses-yesterday-2",
                    created_ms=int(
                        datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
            ],
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2026, 5, 11, 12, 0, tzinfo=tz),
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--yesterday",
            "--timezone",
            "UTC",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ses-yesterday-2" in result.output
    assert "ses-today-2" not in result.output


def test_cli_usage_sessions_period_conflicts_with_since(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--today",
            "--since",
            "2026-05-11",
            "--no-refresh",
        ],
    )
    assert result.exit_code != 0
    assert "Use either a named period or --since/--until" in result.output


def test_cli_usage_sessions_table_restores_legacy_columns(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)
    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--table", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "cache_r" in result.output
    assert "unpriced" in result.output


def test_cli_usage_sessions_period_default_limit_is_unbounded(
    monkeypatch, tmp_path
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        events = [
            make_cli_usage_event(
                f"bulk-{idx}",
                source_session_id=f"ses-{idx}",
                created_ms=int(
                    datetime(2026, 5, 11, 8, idx % 60, tzinfo=timezone.utc).timestamp()
                    * 1000
                ),
                tokens=TokenBreakdown(input=1, output=1),
            )
            for idx in range(12)
        ]
        insert_usage_events(conn, None, events)
    finally:
        conn.close()

    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2026, 5, 11, 12, 0, tzinfo=tz),
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--today",
            "--timezone",
            "UTC",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Token usage:") == 12


def test_cli_usage_runs_human_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "toktrail usage runs" in result.output


def test_cli_usage_runs_rich_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "usage-runs"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    plain = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--no-refresh"],
    )
    assert plain.exit_code == 0, plain.output
    assert not any(ch in plain.output for ch in "┏┌╭")

    rich = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--rich", "--no-refresh"],
    )
    _assert_rich_result_or_missing_dependency(rich)
    if rich.exit_code == 0:
        assert "toktrail usage runs" in rich.output


def test_cli_usage_sessions_rich_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    plain = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--no-refresh"],
    )
    assert plain.exit_code == 0, plain.output
    assert not any(ch in plain.output for ch in "┏┌╭")

    rich = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--rich",
            "--table",
            "--no-refresh",
        ],
    )
    _assert_rich_result_or_missing_dependency(rich)
    if rich.exit_code == 0:
        assert "toktrail usage sessions" in rich.output


def test_cli_usage_daily_rich_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    plain = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "daily", "--no-refresh"],
    )
    assert plain.exit_code == 0, plain.output
    assert not any(ch in plain.output for ch in "┏┌╭")

    rich = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "daily", "--rich", "--no-refresh"],
    )
    _assert_rich_result_or_missing_dependency(rich)
    if rich.exit_code == 0:
        assert "toktrail usage daily" in rich.output


def test_cli_usage_runs_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--json", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "usage_runs"
    assert "runs" in payload
    assert "totals" in payload


def test_cli_usage_project_rejected(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    runner.invoke(app, ["--db", str(state_db), "init"])

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "daily", "--project", "myproj"],
    )
    assert result.exit_code != 0
    assert "No such option: --project" in _strip_ansi(result.output)
