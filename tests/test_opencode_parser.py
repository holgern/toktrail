from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal

from tests.helpers import VALID_ASSISTANT, create_opencode_db, insert_message
from toktrail.adapters.opencode import (
    list_opencode_sessions,
    parse_opencode_row,
    parse_opencode_sqlite,
)


def test_parse_valid_assistant_message(tmp_path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = create_opencode_db(db_path)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    events = parse_opencode_sqlite(db_path)

    assert len(events) == 1
    event = events[0]
    assert event.harness == "opencode"
    assert event.source_session_id == "ses-1"
    assert event.source_message_id == "msg_123"
    assert event.source_dedup_key == "msg_123"
    assert event.provider_id == "anthropic"
    assert event.model_id == "claude-sonnet-4"
    assert event.agent == "build"
    assert event.tokens.total == 1500
    assert event.source_cost_usd == Decimal("0.05")


def test_parse_skips_non_importable_rows(tmp_path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = create_opencode_db(db_path)

    user_message = deepcopy(VALID_ASSISTANT)
    user_message["role"] = "user"
    insert_message(conn, row_id="row-user", session_id="ses-1", data=user_message)

    no_tokens = deepcopy(VALID_ASSISTANT)
    no_tokens.pop("tokens")
    insert_message(conn, row_id="row-no-tokens", session_id="ses-1", data=no_tokens)

    no_model = deepcopy(VALID_ASSISTANT)
    no_model.pop("modelID")
    insert_message(conn, row_id="row-no-model", session_id="ses-1", data=no_model)

    conn.execute(
        "INSERT INTO message (id, session_id, data) VALUES (?, ?, ?)",
        ("row-bad-json", "ses-1", "{"),
    )
    conn.commit()
    conn.close()

    assert parse_opencode_sqlite(db_path) == []


def test_parse_defaults_and_clamps_values() -> None:
    payload = deepcopy(VALID_ASSISTANT)
    payload.pop("providerID")
    payload.pop("cost")
    payload["tokens"] = {
        "input": -10,
        "output": 2,
        "reasoning": -3,
        "cache": {"read": -4, "write": 5},
    }

    event = parse_opencode_row("row-1", "ses-1", json.dumps(payload))

    assert event is not None
    assert event.provider_id == "unknown"
    assert event.source_cost_usd == Decimal("0.0")
    assert event.tokens.input == 0
    assert event.tokens.output == 2
    assert event.tokens.reasoning == 0
    assert event.tokens.cache_read == 0
    assert event.tokens.cache_write == 5


def test_parse_prefers_mode_over_agent_and_normalizes() -> None:
    payload = deepcopy(VALID_ASSISTANT)
    payload["mode"] = " PLAN "
    payload["agent"] = "OmO"

    event = parse_opencode_row("row-1", "ses-1", json.dumps(payload))

    assert event is not None
    assert event.agent == "plan"


def test_parse_falls_back_to_row_id_for_dedup_key() -> None:
    payload = deepcopy(VALID_ASSISTANT)
    payload.pop("id")

    event = parse_opencode_row("row-9", "ses-1", json.dumps(payload))

    assert event is not None
    assert event.source_message_id is None
    assert event.source_dedup_key == "row-9"
    assert event.global_dedup_key == "opencode:row-9"


def test_parse_deduplicates_fork_history_rows(tmp_path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = create_opencode_db(db_path)

    duplicate = deepcopy(VALID_ASSISTANT)
    duplicate.pop("id")
    insert_message(conn, row_id="row-1", session_id="ses-1", data=duplicate)
    insert_message(
        conn,
        row_id="row-2",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    events = parse_opencode_sqlite(db_path)

    assert len(events) == 1
    assert events[0].source_message_id == "msg_123"
    assert events[0].source_dedup_key == "msg_123"


def test_parse_preserves_same_timestamp_rows_with_different_tokens(tmp_path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = create_opencode_db(db_path)
    first = deepcopy(VALID_ASSISTANT)
    second = deepcopy(VALID_ASSISTANT)
    second["id"] = "msg_999"
    second["tokens"] = {
        "input": 1,
        "output": 2,
        "reasoning": 3,
        "cache": {"read": 4, "write": 5},
    }
    insert_message(conn, row_id="row-1", session_id="ses-1", data=first)
    insert_message(conn, row_id="row-2", session_id="ses-1", data=second)
    conn.commit()
    conn.close()

    events = parse_opencode_sqlite(db_path)

    assert len(events) == 2
    assert {event.source_message_id for event in events} == {"msg_123", "msg_999"}


def test_parse_extracts_thinking_level_from_payload_and_request() -> None:
    payload = deepcopy(VALID_ASSISTANT)
    payload["thinkingLevel"] = " High "

    event = parse_opencode_row("row-1", "ses-1", json.dumps(payload))

    assert event is not None
    assert event.thinking_level == "high"

    nested_payload = deepcopy(VALID_ASSISTANT)
    nested_payload["request"] = {"reasoning_effort": "medium"}

    nested_event = parse_opencode_row("row-2", "ses-1", json.dumps(nested_payload))

    assert nested_event is not None
    assert nested_event.thinking_level == "medium"


def test_parse_returns_empty_list_for_missing_db(tmp_path) -> None:
    missing_db = tmp_path / "missing.db"

    assert parse_opencode_sqlite(missing_db) == []


def test_list_opencode_sessions_aggregates_messages(tmp_path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = create_opencode_db(db_path)
    first = deepcopy(VALID_ASSISTANT)
    second = deepcopy(VALID_ASSISTANT)
    second["id"] = "msg_999"
    second["tokens"] = {
        "input": 1,
        "output": 2,
        "reasoning": 3,
        "cache": {"read": 4, "write": 5},
    }
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=first,
    )
    insert_message(
        conn,
        row_id="row-2",
        session_id="ses-1",
        data=second,
    )
    conn.commit()
    conn.close()

    summaries = list_opencode_sessions(db_path)

    assert len(summaries) == 1
    assert summaries[0].source_session_id == "ses-1"
    assert summaries[0].assistant_message_count == 2
    assert summaries[0].tokens.total == 1503


def test_parse_opencode_go_preserves_source_cost_and_cache_read(tmp_path) -> None:
    db_path = tmp_path / "opencode.db"
    conn = create_opencode_db(db_path)
    high = deepcopy(VALID_ASSISTANT)
    high["id"] = "msg-high"
    high["providerID"] = "opencode-go"
    high["modelID"] = "glm-5.1"
    high["tokens"] = {
        "input": 150435,
        "output": 45,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0, "output": 0},
    }
    high["cost"] = 0.2107
    low = deepcopy(VALID_ASSISTANT)
    low["id"] = "msg-low"
    low["providerID"] = "opencode-go"
    low["modelID"] = "glm-5.1"
    low["tokens"] = {
        "input": 20000,
        "output": 90,
        "reasoning": 0,
        "cache": {"read": 130336, "write": 0, "output": 0},
    }
    low["cost"] = 0.0395
    insert_message(conn, row_id="1", session_id="ses-cache", data=high)
    insert_message(conn, row_id="2", session_id="ses-cache", data=low)
    conn.commit()
    conn.close()

    events = parse_opencode_sqlite(db_path)

    assert len(events) == 2
    by_message = {event.source_message_id: event for event in events}
    assert by_message["msg-high"].source_cost_usd == Decimal("0.2107")
    assert by_message["msg-high"].tokens.cache_read == 0
    assert by_message["msg-low"].source_cost_usd == Decimal("0.0395")
    assert by_message["msg-low"].tokens.cache_read == 130336


def test_parse_opencode_go_keeps_high_cost_uncached_call() -> None:
    payload = deepcopy(VALID_ASSISTANT)
    payload["id"] = "msg-high"
    payload["providerID"] = "opencode-go"
    payload["modelID"] = "glm-5.1"
    payload["tokens"] = {
        "input": 149000,
        "output": 50,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0, "output": 0},
    }
    payload["cost"] = 0.2096

    event = parse_opencode_row("row-high", "ses-cache", json.dumps(payload))

    assert event is not None
    assert event.source_cost_usd == Decimal("0.2096")
    assert event.tokens.cache_read == 0


def test_parse_opencode_go_keeps_low_cost_cached_call() -> None:
    payload = deepcopy(VALID_ASSISTANT)
    payload["id"] = "msg-low"
    payload["providerID"] = "opencode-go"
    payload["modelID"] = "glm-5.1"
    payload["tokens"] = {
        "input": 18000,
        "output": 60,
        "reasoning": 0,
        "cache": {"read": 132000, "write": 0, "output": 0},
    }
    payload["cost"] = 0.039

    event = parse_opencode_row("row-low", "ses-cache", json.dumps(payload))

    assert event is not None
    assert event.source_cost_usd == Decimal("0.039")
    assert event.tokens.cache_read == 132000


def test_parse_opencode_go_cache_output_is_preserved() -> None:
    payload = deepcopy(VALID_ASSISTANT)
    payload["id"] = "msg-cache-output"
    payload["tokens"] = {
        "input": 10,
        "output": 20,
        "reasoning": 0,
        "cache": {"read": 1, "write": 2, "output": 3},
    }

    event = parse_opencode_row("row-cache-output", "ses-cache", json.dumps(payload))

    assert event is not None
    assert event.tokens.cache_output == 3
