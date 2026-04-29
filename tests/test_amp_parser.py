from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from toktrail.adapters.amp import list_amp_sessions, parse_amp_file, scan_amp_path
from toktrail.models import TokenBreakdown


def write_amp_thread(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def timestamp_ms(value: str) -> int:
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    return int(datetime.fromisoformat(raw).timestamp() * 1000)


def create_amp_source(path: Path) -> Path:
    file_path = path / "thread-1.json" if path.suffix == "" else path
    return write_amp_thread(
        file_path,
        {
            "id": file_path.stem,
            "created": timestamp_ms("2026-04-08T12:00:00Z"),
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
        },
    )


def test_parse_amp_reconciles_partial_ledger_with_message_usage(tmp_path: Path) -> None:
    created_ms = timestamp_ms("2026-04-08T12:00:00Z")
    path = write_amp_thread(
        tmp_path / "thread-1.json",
        {
            "id": "thread-1",
            "created": created_ms,
            "usageLedger": {
                "events": [
                    {
                        "timestamp": "2026-04-08T12:00:10Z",
                        "model": "claude-sonnet-4-0",
                        "credits": 0.75,
                        "tokens": {
                            "input": 100,
                            "output": 20,
                            "cacheReadInputTokens": 30,
                            "cacheCreationInputTokens": 40,
                        },
                        "toMessageId": 1,
                    }
                ]
            },
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
                    },
                },
                {
                    "role": "assistant",
                    "messageId": 2,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": 50,
                        "outputTokens": 10,
                    },
                },
            ],
        },
    )

    events = parse_amp_file(path)

    assert len(events) == 2
    assert events[0].created_ms == created_ms + 2000
    assert events[0].source_dedup_key == "message:2"
    assert events[1].created_ms == timestamp_ms("2026-04-08T12:00:10Z")
    assert events[1].source_dedup_key == "message:1"


def test_parse_amp_does_not_double_count_full_ledger(tmp_path: Path) -> None:
    path = write_amp_thread(
        tmp_path / "thread-1.json",
        {
            "id": "thread-1",
            "created": timestamp_ms("2026-04-08T12:00:00Z"),
            "usageLedger": {
                "events": [
                    {
                        "timestamp": "2026-04-08T12:00:01Z",
                        "model": "claude-sonnet-4-0",
                        "tokens": {"input": 100, "output": 20},
                        "toMessageId": 1,
                    },
                    {
                        "timestamp": "2026-04-08T12:00:02Z",
                        "model": "claude-sonnet-4-0",
                        "tokens": {"input": 200, "output": 40},
                        "toMessageId": 2,
                    },
                ]
            },
            "messages": [
                {
                    "role": "assistant",
                    "messageId": 1,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": 100,
                        "outputTokens": 20,
                    },
                },
                {
                    "role": "assistant",
                    "messageId": 2,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": 200,
                        "outputTokens": 40,
                    },
                },
            ],
        },
    )

    events = parse_amp_file(path)

    assert len(events) == 2
    assert [event.tokens.input for event in events] == [100, 200]


def test_parse_amp_prefers_message_id_match_over_token_heuristic(
    tmp_path: Path,
) -> None:
    path = write_amp_thread(
        tmp_path / "thread-1.json",
        {
            "id": "thread-1",
            "created": timestamp_ms("2026-04-08T12:00:00Z"),
            "usageLedger": {
                "events": [
                    {
                        "timestamp": "2026-04-08T12:00:01Z",
                        "model": "claude-sonnet-4-0",
                        "tokens": {"input": 100, "output": 20},
                        "toMessageId": 2,
                    },
                    {
                        "timestamp": "2026-04-08T12:00:02Z",
                        "model": "claude-sonnet-4-0",
                        "tokens": {"input": 100, "output": 20},
                        "toMessageId": 1,
                    },
                ]
            },
            "messages": [
                {
                    "role": "assistant",
                    "messageId": 1,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": 100,
                        "outputTokens": 20,
                    },
                },
                {
                    "role": "assistant",
                    "messageId": 2,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": 100,
                        "outputTokens": 20,
                    },
                },
            ],
        },
    )

    events = parse_amp_file(path)

    assert [(event.source_message_id, event.created_ms) for event in events] == [
        ("2", timestamp_ms("2026-04-08T12:00:01Z")),
        ("1", timestamp_ms("2026-04-08T12:00:02Z")),
    ]


def test_parse_amp_prefers_message_timestamp_when_ledger_timestamp_missing(
    tmp_path: Path,
) -> None:
    created_ms = timestamp_ms("2026-04-08T12:00:00Z")
    path = write_amp_thread(
        tmp_path / "thread-1.json",
        {
            "id": "thread-1",
            "created": created_ms,
            "usageLedger": {
                "events": [
                    {
                        "model": "claude-sonnet-4-0",
                        "credits": 0,
                        "tokens": {"input": 100, "output": 20},
                        "toMessageId": 7,
                    }
                ]
            },
            "messages": [
                {
                    "role": "assistant",
                    "messageId": 7,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": 100,
                        "outputTokens": 20,
                        "credits": 0.5,
                    },
                }
            ],
        },
    )

    event = parse_amp_file(path)[0]

    assert event.created_ms == created_ms + 7000
    assert event.cost_usd == 0.5


def test_parse_amp_uses_file_mtime_when_thread_created_missing(tmp_path: Path) -> None:
    path = write_amp_thread(
        tmp_path / "thread-1.json",
        {
            "id": "thread-1",
            "messages": [
                {
                    "role": "assistant",
                    "messageId": 5,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": 100,
                        "outputTokens": 20,
                    },
                }
            ],
        },
    )
    os.utime(path, (100, 100))

    event = parse_amp_file(path)[0]

    assert event.created_ms == 105000


def test_parse_amp_skips_non_assistant_and_missing_usage(tmp_path: Path) -> None:
    path = write_amp_thread(
        tmp_path / "thread-1.json",
        {
            "id": "thread-1",
            "created": timestamp_ms("2026-04-08T12:00:00Z"),
            "messages": [
                {"role": "user", "messageId": 1, "usage": {"model": "x"}},
                {"role": "assistant", "messageId": 2},
                {"role": "assistant", "messageId": 3, "usage": {"inputTokens": 1}},
            ],
        },
    )

    assert parse_amp_file(path) == []


def test_parse_amp_clamps_negative_usage(tmp_path: Path) -> None:
    path = write_amp_thread(
        tmp_path / "thread-1.json",
        {
            "id": "thread-1",
            "created": timestamp_ms("2026-04-08T12:00:00Z"),
            "messages": [
                {
                    "role": "assistant",
                    "messageId": 1,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": -1,
                        "outputTokens": 10,
                        "cacheReadInputTokens": -2,
                        "cacheCreationInputTokens": 3,
                        "credits": -0.75,
                    },
                }
            ],
        },
    )

    event = parse_amp_file(path)[0]

    assert event.tokens == TokenBreakdown(output=10, cache_write=3)
    assert event.cost_usd == 0.0


def test_parse_amp_keeps_cache_only_message(tmp_path: Path) -> None:
    path = write_amp_thread(
        tmp_path / "thread-1.json",
        {
            "id": "thread-1",
            "created": timestamp_ms("2026-04-08T12:00:00Z"),
            "messages": [
                {
                    "role": "assistant",
                    "messageId": 1,
                    "usage": {
                        "model": "claude-sonnet-4-0",
                        "inputTokens": 0,
                        "outputTokens": 0,
                        "cacheReadInputTokens": 50,
                    },
                }
            ],
        },
    )

    event = parse_amp_file(path)[0]

    assert event.tokens.total == 50
    assert event.tokens.cache_read == 50


def test_scan_amp_path_reads_nested_json_and_filters(tmp_path: Path) -> None:
    create_amp_source(tmp_path / "thread-1.json")
    create_amp_source(tmp_path / "nested" / "thread-2.json")

    scan = scan_amp_path(tmp_path, source_session_id="thread-2")

    assert scan.files_seen == 2
    assert scan.rows_seen == 2
    assert scan.rows_skipped == 1
    assert [event.source_session_id for event in scan.events] == ["thread-2"]


def test_scan_amp_raw_json_is_optional(tmp_path: Path) -> None:
    path = create_amp_source(tmp_path / "thread-1.json")

    event = scan_amp_path(path, include_raw_json=False).events[0]

    assert event.raw_json is None


def test_parse_amp_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert parse_amp_file(tmp_path / "missing.json") == []


def test_list_amp_sessions_aggregates_messages(tmp_path: Path) -> None:
    create_amp_source(tmp_path / "thread-1.json")

    sessions = list_amp_sessions(tmp_path)

    assert len(sessions) == 1
    assert sessions[0].harness == "amp"
    assert sessions[0].source_session_id == "thread-1"
    assert sessions[0].tokens.total == 190
