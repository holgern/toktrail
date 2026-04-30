from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.goose import (
    _parse_created_at_ms,
    _parse_model_config,
    list_goose_sessions,
    parse_goose_sqlite,
    scan_goose_sqlite,
)
from toktrail.models import TokenBreakdown


def create_goose_db(path: Path) -> None:
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
    conn.commit()
    conn.close()


def insert_session(
    path: Path,
    *,
    session_id: str = "goose-1",
    model_config_json: str = '{"model_name":"claude-sonnet-4-20250514"}',
    provider_name: str | None = "anthropic",
    created_at: str = "2026-04-14T16:18:53Z",
    total_tokens: int | None = 100,
    input_tokens: int | None = 60,
    output_tokens: int | None = 30,
    accumulated_total_tokens: int | None = None,
    accumulated_input_tokens: int | None = None,
    accumulated_output_tokens: int | None = None,
) -> None:
    conn = sqlite3.connect(path)
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
            session_id,
            model_config_json,
            provider_name,
            created_at,
            total_tokens,
            input_tokens,
            output_tokens,
            accumulated_total_tokens,
            accumulated_input_tokens,
            accumulated_output_tokens,
        ),
    )
    conn.commit()
    conn.close()


def test_parse_model_config_valid() -> None:
    assert _parse_model_config('{"model_name":"claude-sonnet-4-20250514"}') == (
        "claude-sonnet-4-20250514"
    )


def test_parse_model_config_invalid_or_empty() -> None:
    assert _parse_model_config("not json") is None
    assert _parse_model_config('{"model_name":"  "}') is None
    assert _parse_model_config("{}") is None


def test_parse_created_at_formats() -> None:
    assert _parse_created_at_ms("2026-04-14T16:18:53Z") > 0
    assert _parse_created_at_ms("2026-04-14 16:18:53") > 0
    assert _parse_created_at_ms("2026-04-14") > 0
    assert _parse_created_at_ms("not a date") == 0


def test_parse_goose_sqlite_prefers_accumulated_tokens(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    create_goose_db(db_path)
    insert_session(
        db_path,
        total_tokens=100,
        input_tokens=60,
        output_tokens=30,
        accumulated_total_tokens=150,
        accumulated_input_tokens=90,
        accumulated_output_tokens=40,
    )

    events = parse_goose_sqlite(db_path)

    assert len(events) == 1
    event = events[0]
    assert event.harness == "goose"
    assert event.source_session_id == "goose-1"
    assert event.global_dedup_key == "goose:goose-1"
    assert event.provider_id == "anthropic"
    assert event.model_id == "claude-sonnet-4-20250514"
    assert event.tokens == TokenBreakdown(input=90, output=40, reasoning=20)
    assert event.source_cost_usd == Decimal("0.0")


def test_parse_goose_sqlite_infers_provider_from_model(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    create_goose_db(db_path)
    insert_session(
        db_path,
        model_config_json='{"model_name":"gpt-5.2"}',
        provider_name=None,
    )

    event = parse_goose_sqlite(db_path)[0]

    assert event.provider_id == "openai"


def test_parse_goose_sqlite_skips_zero_and_invalid_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    create_goose_db(db_path)
    insert_session(
        db_path,
        session_id="zero",
        total_tokens=0,
        input_tokens=0,
        output_tokens=0,
    )
    insert_session(
        db_path,
        session_id="invalid-model",
        model_config_json="not json",
    )

    scan = scan_goose_sqlite(db_path)

    assert scan.rows_seen == 2
    assert scan.rows_skipped == 2
    assert scan.events == []


def test_scan_goose_sqlite_supports_source_session_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    create_goose_db(db_path)
    insert_session(db_path, session_id="goose-1")
    insert_session(db_path, session_id="goose-2")

    scan = scan_goose_sqlite(db_path, source_session_id="goose-2")

    assert [event.source_session_id for event in scan.events] == ["goose-2"]


def test_scan_goose_sqlite_raw_json_is_optional(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    create_goose_db(db_path)
    insert_session(db_path)

    event = scan_goose_sqlite(db_path, include_raw_json=False).events[0]

    assert event.raw_json is None


def test_parse_goose_sqlite_returns_empty_for_missing_db(tmp_path: Path) -> None:
    assert parse_goose_sqlite(tmp_path / "missing.db") == []


def test_list_goose_sessions_aggregates_messages(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    create_goose_db(db_path)
    insert_session(db_path, session_id="goose-1", input_tokens=7, output_tokens=5)

    sessions = list_goose_sessions(db_path)

    assert len(sessions) == 1
    assert sessions[0].harness == "goose"
    assert sessions[0].source_session_id == "goose-1"
    assert sessions[0].assistant_message_count == 1
    assert sessions[0].tokens.input == 7
    assert sessions[0].tokens.output == 5
    assert sessions[0].tokens.reasoning == 88
