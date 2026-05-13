# ruff: noqa: E501

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.harnessbridge import (
    parse_harnessbridge_file,
    scan_harnessbridge_file,
    scan_harnessbridge_path,
)


def write_harnessbridge_rows(path: Path, rows: list[object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized_lines: list[str] = []
    for row in rows:
        if isinstance(row, str):
            serialized_lines.append(row)
        else:
            serialized_lines.append(json.dumps(row))
    path.write_text("\n".join(serialized_lines) + "\n", encoding="utf-8")
    return path


def test_parse_harnessbridge_primary_usage_rows(tmp_path) -> None:
    session_file = write_harnessbridge_rows(
        tmp_path / "session.jsonl",
        [
            {
                "type": "session",
                "schema": "harnessbridge.session.v1",
                "id": "hb-session-1",
                "harness": "codex",
                "accounting": "primary",
                "started_ms": 1_778_682_000_000,
            },
            {
                "type": "usage",
                "id": "evt-pi",
                "harness": "pi",
                "provider_id": "anthropic",
                "model_id": "claude-sonnet-4",
                "source_message_id": "turn-1",
                "thinking_level": "High",
                "created_ms": 1_778_682_001_000,
                "tokens": {"input": 10, "output": 5, "cacheRead": 2},
                "source_cost_usd": "0.10",
                "raw": {"native_usage": {"input": 10, "output": 5}},
            },
            {
                "type": "usage",
                "id": "evt-codex",
                "harness": "codex",
                "provider_id": "openai",
                "model_id": "gpt-5.1",
                "agent": "Headless",
                "created_at": "2026-05-13T14:20:05Z",
                "completed_at": "2026-05-13T14:20:06Z",
                "tokens": {"input": 20, "output": 3, "reasoning": 1},
                "cost": {"total": "0.20"},
            },
            {
                "type": "usage",
                "id": "evt-copilot",
                "harness": "copilot",
                "provider_id": "github-copilot",
                "model_id": "gpt-5",
                "created_ms": 1_778_682_003_000,
                "tokens": {"input": 30, "output": 4, "cache_write": 6},
                "source_cost_usd": "0.30",
            },
            {
                "type": "usage",
                "id": "evt-opencode",
                "harness": "opencode",
                "provider_id": "openrouter",
                "model_id": "qwen-3",
                "created_ms": 1_778_682_004_000,
                "tokens": {"input": 40, "output": 6, "cacheOutput": 7},
                "raw": {"native_usage": {"cost": {"total": "0.40"}}},
            },
        ],
    )

    events = parse_harnessbridge_file(session_file)

    assert len(events) == 4
    assert [event.harness for event in events] == [
        "pi",
        "codex",
        "copilot",
        "opencode",
    ]
    assert events[0].source_session_id == "hb-session-1"
    assert events[0].provider_id == "anthropic"
    assert events[0].thinking_level == "high"
    assert events[0].tokens.input == 10
    assert events[0].tokens.output == 5
    assert events[0].tokens.cache_read == 2
    assert events[0].source_cost_usd == Decimal("0.10")
    assert events[1].agent == "headless"
    assert events[1].source_cost_usd == Decimal("0.20")
    assert events[2].tokens.cache_write == 6
    assert events[3].tokens.cache_output == 7
    assert events[3].source_cost_usd == Decimal("0.40")
    assert all(event.raw_json is not None for event in events)


def test_scan_harnessbridge_skips_mirror_rows_by_default(tmp_path) -> None:
    session_file = write_harnessbridge_rows(
        tmp_path / "mirror.jsonl",
        [
            {
                "type": "session",
                "id": "hb-session-1",
                "accounting": "primary",
                "started_ms": 1_778_682_000_000,
            },
            {
                "type": "usage",
                "id": "evt-mirror",
                "harness": "codex",
                "accounting": "mirror",
                "provider_id": "openai",
                "model_id": "gpt-5",
                "created_ms": 1_778_682_001_000,
                "tokens": {"input": 10, "output": 1},
            },
        ],
    )

    scan = scan_harnessbridge_file(session_file)

    assert scan.events == []
    assert scan.rows_seen == 2
    assert scan.rows_skipped == 1


def test_scan_harnessbridge_tolerates_malformed_rows(tmp_path) -> None:
    session_file = write_harnessbridge_rows(
        tmp_path / "malformed.jsonl",
        [
            "",
            "not-json",
            {"type": "note", "message": "skip me"},
            {
                "type": "usage",
                "id": "evt-zero",
                "harness": "pi",
                "provider_id": "anthropic",
                "model_id": "claude-sonnet-4",
                "created_ms": 1_778_682_001_000,
                "tokens": {"input": 0, "output": 0},
            },
            {
                "type": "usage",
                "id": "evt-valid",
                "harness": "pi",
                "provider_id": "anthropic",
                "model_id": "claude-sonnet-4",
                "created_ms": 1_778_682_002_000,
                "tokens": {"input": 5, "output": 2},
            },
        ],
    )

    scan = scan_harnessbridge_file(session_file)

    assert len(scan.events) == 1
    assert scan.events[0].source_message_id is None
    assert scan.rows_seen == 4
    assert scan.rows_skipped == 3


def test_scan_harnessbridge_supports_source_session_id_filter(tmp_path) -> None:
    session_file = write_harnessbridge_rows(
        tmp_path / "filter.jsonl",
        [
            {
                "type": "usage",
                "id": "evt-1",
                "harness": "pi",
                "provider_id": "anthropic",
                "model_id": "claude-sonnet-4",
                "source_session_id": "hb-session-1",
                "created_ms": 1_778_682_001_000,
                "tokens": {"input": 5, "output": 2},
            },
            {
                "type": "usage",
                "id": "evt-2",
                "harness": "pi",
                "provider_id": "anthropic",
                "model_id": "claude-sonnet-4",
                "source_session_id": "hb-session-2",
                "created_ms": 1_778_682_002_000,
                "tokens": {"input": 7, "output": 3},
            },
        ],
    )

    scan = scan_harnessbridge_file(
        session_file,
        source_session_id="hb-session-2",
    )

    assert len(scan.events) == 1
    assert scan.events[0].source_session_id == "hb-session-2"
    assert scan.rows_skipped == 1


def test_scan_harnessbridge_supports_since_ms(tmp_path) -> None:
    session_file = write_harnessbridge_rows(
        tmp_path / "since.jsonl",
        [
            {
                "type": "usage",
                "id": "evt-old",
                "harness": "copilot",
                "provider_id": "github-copilot",
                "model_id": "gpt-5",
                "created_ms": 1_778_682_001_000,
                "tokens": {"input": 5, "output": 2},
            },
            {
                "type": "usage",
                "id": "evt-new",
                "harness": "copilot",
                "provider_id": "github-copilot",
                "model_id": "gpt-5",
                "created_ms": 1_778_682_003_000,
                "tokens": {"input": 7, "output": 3},
            },
        ],
    )

    scan = scan_harnessbridge_file(session_file, since_ms=1_778_682_002_000)

    assert len(scan.events) == 1
    assert scan.events[0].source_dedup_key == "evt-new"
    assert scan.rows_skipped == 1


def test_scan_harnessbridge_path_supports_directory_scans(tmp_path) -> None:
    write_harnessbridge_rows(
        tmp_path / "pi" / "a.jsonl",
        [
            {
                "type": "usage",
                "id": "evt-a",
                "harness": "pi",
                "provider_id": "anthropic",
                "model_id": "claude-sonnet-4",
                "created_ms": 1_778_682_001_000,
                "tokens": {"input": 5, "output": 2},
            }
        ],
    )
    write_harnessbridge_rows(
        tmp_path / "codex" / "b.jsonl",
        [
            {
                "type": "usage",
                "id": "evt-b",
                "harness": "codex",
                "provider_id": "openai",
                "model_id": "gpt-5.1",
                "created_ms": 1_778_682_002_000,
                "tokens": {"input": 7, "output": 3},
            }
        ],
    )

    scan = scan_harnessbridge_path(tmp_path)

    assert scan.files_seen == 2
    assert len(scan.events) == 2
    assert {event.harness for event in scan.events} == {"pi", "codex"}
