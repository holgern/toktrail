from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.vibe import (
    list_vibe_sessions,
    parse_vibe_file,
    parse_vibe_path,
    scan_vibe_path,
)


def timestamp_ms(value: str) -> int:
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    return int(datetime.fromisoformat(raw).timestamp() * 1000)


def write_vibe_session(tmp_path: Path) -> Path:
    """Write a Vibe session directory with meta.json and messages.jsonl."""
    session_id = "829fb78c-c6bd-26a6-2f1b-076ba4ade782"
    session_dir = tmp_path / f"session_20260430_062520_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Write meta.json
    meta_json = {
        "session_id": session_id,
        "start_time": "2026-04-30T06:25:20.835314+00:00",
        "end_time": "2026-04-30T06:25:33.933247+00:00",
        "environment": {"working_directory": "/home/nahrstaedt/src/odoo17/tmp2"},
        "stats": {
            "steps": 2,
            "session_prompt_tokens": 8516,
            "session_completion_tokens": 62,
            "context_tokens": 8578,
            "last_turn_prompt_tokens": 8516,
            "last_turn_completion_tokens": 62,
            "input_price_per_million": 1.5,
            "output_price_per_million": 7.5,
            "session_total_llm_tokens": 8578,
            "last_turn_total_tokens": 8578,
            "session_cost": 0.013238999999999999,
        },
        "title": "hello",
        "total_messages": 2,
        "config": {
            "active_model": "mistral-medium-3.5",
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "mistral-medium-3.5",
                    "thinking": "high",
                    "input_price": 1.5,
                    "output_price": 7.5,
                }
            ],
        },
    }
    (session_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    # Write messages.jsonl
    messages_jsonl = [
        {
            "role": "user",
            "content": "hello",
            "injected": False,
            "message_id": "b6519183-de90-40f3-8605-457628465804",
        },
        {
            "role": "assistant",
            "content": "Hello. How can I help?",
            "injected": False,
            "reasoning_content": "...",
            "reasoning_message_id": "4c94a4e4-2fa2-4bf3-8ebc-4de56762016e",
            "message_id": "a-1",
        },
    ]
    with (session_dir / "messages.jsonl").open("w", encoding="utf-8") as f:
        for msg in messages_jsonl:
            f.write(json.dumps(msg) + "\n")

    return session_dir


def test_parse_vibe_meta_session_totals(tmp_path: Path) -> None:
    session = write_vibe_session(tmp_path)
    events = parse_vibe_path(session)

    assert len(events) == 1
    event = events[0]
    assert event.harness == "vibe"
    assert event.source_session_id == "829fb78c-c6bd-26a6-2f1b-076ba4ade782"
    assert event.source_message_id == "a-1"
    assert event.provider_id == "mistral"
    assert event.model_id == "mistral-vibe-cli-latest"
    assert event.thinking_level == "high"
    assert event.tokens.input == 8516
    assert event.tokens.output == 62
    assert event.tokens.reasoning == 0
    assert event.tokens.cache_read == 0
    assert event.tokens.cache_write == 0
    assert event.source_cost_usd == Decimal("0.013238999999999999")
    assert event.created_ms == timestamp_ms("2026-04-30T06:25:20.835314+00:00")
    assert event.completed_ms == timestamp_ms("2026-04-30T06:25:33.933247+00:00")
    assert event.raw_json is not None
    assert "mistral-vibe-cli-latest" in event.raw_json


def test_parse_vibe_computes_cost_when_session_cost_missing(tmp_path: Path) -> None:
    session_id = "test-session-1"
    session_dir = tmp_path / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta_json = {
        "session_id": session_id,
        "start_time": "2026-04-30T06:25:20Z",
        "stats": {
            "session_prompt_tokens": 1000,
            "session_completion_tokens": 100,
            "input_price_per_million": 2.0,
            "output_price_per_million": 4.0,
        },
        "config": {
            "active_model": "test-model",
            "models": [],
        },
    }
    (session_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 1
    # Cost = (1000 * 2.0 / 1_000_000) + (100 * 4.0 / 1_000_000) = 0.0024
    expected = Decimal("0.0024")
    assert events[0].source_cost_usd == expected


def test_parse_vibe_uses_file_mtime_when_start_time_missing_or_invalid(
    tmp_path: Path,
) -> None:
    session_id = "test-session-2"
    session_dir = tmp_path / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta_json = {
        "session_id": session_id,
        "stats": {
            "session_prompt_tokens": 100,
            "session_completion_tokens": 10,
        },
        "config": {
            "active_model": "test-model",
            "models": [],
        },
    }
    meta_path = session_dir / "meta.json"
    meta_path.write_text(json.dumps(meta_json), encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 1
    assert events[0].created_ms > 0
    assert events[0].completed_ms is None


def test_parse_vibe_skips_invalid_json(tmp_path: Path) -> None:
    session_dir = tmp_path / "session_broken"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "meta.json").write_text("{ invalid json", encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 0


def test_parse_vibe_skips_zero_token_sessions(tmp_path: Path) -> None:
    session_id = "test-session-3"
    session_dir = tmp_path / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta_json = {
        "session_id": session_id,
        "start_time": "2026-04-30T06:25:20Z",
        "stats": {
            "session_prompt_tokens": 0,
            "session_completion_tokens": 0,
        },
        "config": {
            "active_model": "test-model",
            "models": [],
        },
    }
    (session_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 0


def test_parse_vibe_clamps_negative_tokens_and_costs(tmp_path: Path) -> None:
    session_id = "test-session-4"
    session_dir = tmp_path / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta_json = {
        "session_id": session_id,
        "start_time": "2026-04-30T06:25:20Z",
        "stats": {
            "session_prompt_tokens": -100,
            "session_completion_tokens": -10,
            "session_cost": -0.5,
        },
        "config": {
            "active_model": "test-model",
            "models": [],
        },
    }
    (session_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 0


def test_parse_vibe_raw_json_is_optional(tmp_path: Path) -> None:
    session = write_vibe_session(tmp_path)

    events_with_raw = scan_vibe_path(session, include_raw_json=True).events
    assert len(events_with_raw) == 1
    assert events_with_raw[0].raw_json is not None

    events_without_raw = scan_vibe_path(session, include_raw_json=False).events
    assert len(events_without_raw) == 1
    assert events_without_raw[0].raw_json is None


def test_scan_vibe_path_reads_nested_session_dirs_and_filters(tmp_path: Path) -> None:
    # Create multiple sessions
    write_vibe_session(tmp_path)
    session2_id = "test-session-5"
    session2_dir = tmp_path / f"session_{session2_id}"
    session2_dir.mkdir(parents=True, exist_ok=True)
    meta_json = {
        "session_id": session2_id,
        "start_time": "2026-04-30T06:25:20Z",
        "stats": {
            "session_prompt_tokens": 50,
            "session_completion_tokens": 5,
        },
        "config": {"active_model": "test-model", "models": []},
    }
    (session2_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    # Scan all
    scan_all = scan_vibe_path(tmp_path)
    assert scan_all.files_seen == 2
    assert scan_all.rows_seen == 2
    assert scan_all.rows_skipped == 0
    assert len(scan_all.events) == 2

    # Scan filtered
    session1_id = "829fb78c-c6bd-26a6-2f1b-076ba4ade782"
    scan_filtered = scan_vibe_path(tmp_path, source_session_id=session1_id)
    assert len(scan_filtered.events) == 1
    assert scan_filtered.events[0].source_session_id == session1_id
    assert scan_filtered.rows_skipped == 1


def test_scan_vibe_path_accepts_meta_json_file(tmp_path: Path) -> None:
    session = write_vibe_session(tmp_path)
    meta_file = session / "meta.json"

    events = parse_vibe_file(meta_file)
    assert len(events) == 1
    assert events[0].source_session_id == "829fb78c-c6bd-26a6-2f1b-076ba4ade782"


def test_list_vibe_sessions_aggregates_sessions(tmp_path: Path) -> None:
    write_vibe_session(tmp_path)

    sessions = list_vibe_sessions(tmp_path)
    assert len(sessions) == 1
    summary = sessions[0]
    assert summary.source_session_id == "829fb78c-c6bd-26a6-2f1b-076ba4ade782"
    assert summary.assistant_message_count == 1
    assert summary.models == ("mistral-vibe-cli-latest",)
    assert summary.providers == ("mistral",)
    assert summary.tokens.input == 8516
    assert summary.tokens.output == 62


def test_vibe_model_identity_uses_provider_record_for_local_model(
    tmp_path: Path,
) -> None:
    session_id = "test-session-llamacpp"
    session_dir = tmp_path / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta_json = {
        "session_id": session_id,
        "start_time": "2026-04-30T06:25:20Z",
        "stats": {
            "session_prompt_tokens": 100,
            "session_completion_tokens": 10,
        },
        "config": {
            "active_model": "local-model",
            "models": [
                {
                    "name": "local-model",
                    "provider": "llamacpp",
                    "alias": "local-model",
                }
            ],
        },
    }
    (session_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 1
    assert events[0].provider_id == "llamacpp"
    assert events[0].model_id == "local-model"


def test_vibe_completed_ms_is_none_when_end_time_before_start_time(
    tmp_path: Path,
) -> None:
    session_id = "test-session-6"
    session_dir = tmp_path / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta_json = {
        "session_id": session_id,
        "start_time": "2026-04-30T06:25:33Z",
        "end_time": "2026-04-30T06:25:20Z",
        "stats": {
            "session_prompt_tokens": 100,
            "session_completion_tokens": 10,
        },
        "config": {"active_model": "test-model", "models": []},
    }
    (session_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 1
    assert events[0].completed_ms is None


def test_vibe_missing_stats_is_skipped(tmp_path: Path) -> None:
    session_id = "test-session-7"
    session_dir = tmp_path / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta_json = {
        "session_id": session_id,
        "start_time": "2026-04-30T06:25:20Z",
        "config": {"active_model": "test-model", "models": []},
    }
    (session_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 0


def test_vibe_dedup_key_is_stable(tmp_path: Path) -> None:
    session = write_vibe_session(tmp_path)
    events = parse_vibe_path(session)

    assert len(events) == 1
    event = events[0]
    assert event.source_dedup_key == "session:829fb78c-c6bd-26a6-2f1b-076ba4ade782"
    assert event.global_dedup_key == "vibe:session:829fb78c-c6bd-26a6-2f1b-076ba4ade782"


def test_vibe_uses_session_id_as_fallback_when_meta_lacks_session_id(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "session_fallback"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta_json = {
        "start_time": "2026-04-30T06:25:20Z",
        "stats": {
            "session_prompt_tokens": 100,
            "session_completion_tokens": 10,
        },
        "config": {"active_model": "test-model", "models": []},
    }
    (session_dir / "meta.json").write_text(json.dumps(meta_json), encoding="utf-8")

    events = parse_vibe_path(session_dir)
    assert len(events) == 1
    assert events[0].source_session_id == "session_fallback"
