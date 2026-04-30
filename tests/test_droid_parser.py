from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.droid import (
    _default_model_from_provider,
    _extract_model_from_jsonl,
    _normalize_model_name,
    list_droid_sessions,
    parse_droid_file,
    scan_droid_path,
)
from toktrail.models import TokenBreakdown


def write_droid_settings(
    path: Path,
    *,
    model: str | None = "custom:Claude-Opus-4.5-Thinking-[Anthropic]-0",
    provider_lock: str | None = "anthropic",
    provider_lock_timestamp: str | None = "2024-12-26T12:00:00Z",
    token_usage: dict[str, object] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    if model is not None:
        payload["model"] = model
    if provider_lock is not None:
        payload["providerLock"] = provider_lock
    if provider_lock_timestamp is not None:
        payload["providerLockTimestamp"] = provider_lock_timestamp
    if token_usage is None:
        token_usage = {
            "inputTokens": 1234,
            "outputTokens": 567,
            "cacheCreationTokens": 89,
            "cacheReadTokens": 12,
            "thinkingTokens": 34,
        }
    if token_usage is not None:
        payload["tokenUsage"] = token_usage
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_normalize_model_name_custom_prefix() -> None:
    assert _normalize_model_name("custom:Claude-Opus-4.5-Thinking-[Anthropic]-0") == (
        "claude-opus-4-5-thinking-0"
    )


def test_normalize_model_name_simple() -> None:
    assert _normalize_model_name("gemini-2.5-pro") == "gemini-2-5-pro"


def test_normalize_model_name_brackets() -> None:
    assert _normalize_model_name("Claude-Sonnet-4-[Anthropic]") == "claude-sonnet-4"


def test_default_model_from_provider() -> None:
    assert _default_model_from_provider("anthropic") == "claude-unknown"
    assert _default_model_from_provider("openai") == "gpt-unknown"
    assert _default_model_from_provider("google") == "gemini-unknown"
    assert _default_model_from_provider("xai") == "grok-unknown"
    assert _default_model_from_provider("custom") == "custom-unknown"


def test_extract_model_from_jsonl_scans_model_marker(tmp_path: Path) -> None:
    path = tmp_path / "session-1.jsonl"
    path.write_text(
        '{"type":"system-reminder","content":"Model: Claude-Opus-4.5 [Anthropic]"}\n',
        encoding="utf-8",
    )

    assert _extract_model_from_jsonl(path) == "claude-opus-4-5"


def test_parse_droid_settings_structure(tmp_path: Path) -> None:
    path = write_droid_settings(tmp_path / "session-1.settings.json")

    events = parse_droid_file(path)

    assert len(events) == 1
    event = events[0]
    assert event.harness == "droid"
    assert event.source_session_id == "session-1"
    assert event.source_row_id == str(path)
    assert event.source_dedup_key == "session-1"
    assert event.global_dedup_key == "droid:session-1"
    assert event.provider_id == "anthropic"
    assert event.model_id == "claude-opus-4-5-thinking-0"
    assert event.tokens == TokenBreakdown(
        input=1234,
        output=567,
        reasoning=34,
        cache_read=12,
        cache_write=89,
    )
    assert event.created_ms == 1735214400000
    assert event.completed_ms is None
    assert event.source_cost_usd == Decimal("0.0")
    assert event.raw_json is not None


def test_parse_droid_infers_provider_from_model(tmp_path: Path) -> None:
    path = write_droid_settings(
        tmp_path / "session-1.settings.json",
        model="gpt-5.2",
        provider_lock=None,
    )

    event = parse_droid_file(path)[0]

    assert event.provider_id == "openai"


def test_parse_droid_extracts_model_from_jsonl_when_missing(tmp_path: Path) -> None:
    settings_path = write_droid_settings(
        tmp_path / "session-1.settings.json",
        model=None,
        provider_lock="anthropic",
    )
    (tmp_path / "session-1.jsonl").write_text(
        json.dumps(
            {
                "type": "system-reminder",
                "content": "Model: Claude-Opus-4.5-Thinking [Anthropic]",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    event = parse_droid_file(settings_path)[0]

    assert event.model_id == "claude-opus-4-5-thinking"


def test_parse_droid_defaults_model_from_provider_when_missing(tmp_path: Path) -> None:
    path = write_droid_settings(
        tmp_path / "session-1.settings.json",
        model=None,
        provider_lock="google",
    )

    event = parse_droid_file(path)[0]

    assert event.model_id == "gemini-unknown"


def test_parse_droid_skips_missing_usage_zero_and_invalid(tmp_path: Path) -> None:
    no_usage = tmp_path / "no-usage.settings.json"
    no_usage.write_text('{"model":"gpt-5.2"}', encoding="utf-8")
    zero = write_droid_settings(
        tmp_path / "zero.settings.json",
        token_usage={
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 0,
            "thinkingTokens": 0,
        },
    )
    invalid = tmp_path / "invalid.settings.json"
    invalid.write_text("not json", encoding="utf-8")

    assert parse_droid_file(no_usage) == []
    assert parse_droid_file(zero) == []
    assert parse_droid_file(invalid) == []


def test_parse_droid_clamps_negative_usage(tmp_path: Path) -> None:
    path = write_droid_settings(
        tmp_path / "session-1.settings.json",
        token_usage={
            "inputTokens": -1,
            "outputTokens": 10,
            "cacheCreationTokens": -2,
            "cacheReadTokens": 3,
            "thinkingTokens": -4,
        },
    )

    event = parse_droid_file(path)[0]

    assert event.tokens == TokenBreakdown(output=10, cache_read=3)


def test_scan_droid_path_reads_nested_settings_and_filters(tmp_path: Path) -> None:
    write_droid_settings(tmp_path / "a.settings.json")
    write_droid_settings(tmp_path / "nested" / "b.settings.json")

    scan = scan_droid_path(tmp_path, source_session_id="b")

    assert scan.files_seen == 2
    assert scan.rows_seen == 2
    assert scan.rows_skipped == 1
    assert [event.source_session_id for event in scan.events] == ["b"]


def test_scan_droid_raw_json_is_optional(tmp_path: Path) -> None:
    path = write_droid_settings(tmp_path / "session-1.settings.json")

    event = scan_droid_path(path, include_raw_json=False).events[0]

    assert event.raw_json is None


def test_parse_droid_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert parse_droid_file(tmp_path / "missing.settings.json") == []


def test_list_droid_sessions_aggregates_messages(tmp_path: Path) -> None:
    write_droid_settings(tmp_path / "session-1.settings.json")

    sessions = list_droid_sessions(tmp_path)

    assert len(sessions) == 1
    assert sessions[0].harness == "droid"
    assert sessions[0].source_session_id == "session-1"
    assert sessions[0].assistant_message_count == 1
    assert sessions[0].tokens.input == 1234
    assert sessions[0].tokens.output == 567
    assert sessions[0].tokens.reasoning == 34
    assert sessions[0].tokens.cache_read == 12
    assert sessions[0].tokens.cache_write == 89
