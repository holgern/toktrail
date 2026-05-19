from __future__ import annotations

import json
import re
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from tests.helpers import (
    VALID_ASSISTANT,
    create_opencode_db,
    insert_message,
)
from toktrail.cli import app
from toktrail.models import TokenBreakdown, UsageEvent

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _toml_path_value(path: Path) -> str:
    return str(path).replace("\\", "/")


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


def create_codex_session_file_with_cwd(path: Path, cwd: str) -> None:
    future_iso = (
        datetime.fromtimestamp(_future_ms() / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    write_jsonl_rows(
        path,
        [
            {
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5.2-codex",
                    "working_directory": cwd,
                },
            },
            {
                "timestamp": future_iso,
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
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


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)
