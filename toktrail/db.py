from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from time import time

from toktrail.models import TokenBreakdown, TrackingSession, UsageEvent
from toktrail.reporting import (
    AgentSummaryRow,
    HarnessSummaryRow,
    ModelSummaryRow,
    SessionTotals,
    TrackingSessionReport,
)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class InsertUsageResult:
    rows_inserted: int


def _now_ms() -> int:
    return int(time() * 1000)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current_version_row = conn.execute("PRAGMA user_version").fetchone()
    if current_version_row is None:
        msg = "Could not read SQLite user_version."
        raise ValueError(msg)
    current_version = _required_int(current_version_row[0])
    if current_version > SCHEMA_VERSION:
        msg = f"Unsupported schema version: {current_version}"
        raise ValueError(msg)
    if current_version == SCHEMA_VERSION:
        return

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tracking_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            started_at_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tracking_sessions_started
        ON tracking_sessions(started_at_ms);

        CREATE TABLE IF NOT EXISTS harness_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_session_id INTEGER NOT NULL
                REFERENCES tracking_sessions(id) ON DELETE CASCADE,
            harness TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            first_seen_ms INTEGER,
            last_seen_ms INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            UNIQUE(tracking_session_id, harness, source_session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_harness_sessions_lookup
        ON harness_sessions(harness, source_session_id);

        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_session_id INTEGER
                REFERENCES tracking_sessions(id) ON DELETE SET NULL,
            harness_session_id INTEGER
                REFERENCES harness_sessions(id) ON DELETE SET NULL,
            harness TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            source_row_id TEXT,
            source_message_id TEXT,
            source_dedup_key TEXT,
            global_dedup_key TEXT,
            fingerprint_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'assistant',
            provider_id TEXT NOT NULL DEFAULT 'unknown',
            model_id TEXT NOT NULL,
            agent TEXT,
            created_ms INTEGER NOT NULL,
            completed_ms INTEGER,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            raw_json TEXT,
            imported_at_ms INTEGER NOT NULL,
            UNIQUE(harness, global_dedup_key)
        );

        CREATE INDEX IF NOT EXISTS idx_usage_events_tracking_session
        ON usage_events(tracking_session_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_harness_session
        ON usage_events(harness, source_session_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_fingerprint
        ON usage_events(harness, fingerprint_hash);

        CREATE INDEX IF NOT EXISTS idx_usage_events_model
        ON usage_events(provider_id, model_id);
        """
    )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def create_tracking_session(conn: sqlite3.Connection, name: str | None) -> int:
    active_session_id = get_active_tracking_session(conn)
    if active_session_id is not None:
        msg = f"Tracking session {active_session_id} is already active."
        raise ValueError(msg)
    now_ms = _now_ms()
    cursor = conn.execute(
        """
        INSERT INTO tracking_sessions (
            name,
            started_at_ms,
            created_at_ms,
            updated_at_ms
        )
        VALUES (?, ?, ?, ?)
        """,
        (name, now_ms, now_ms, now_ms),
    )
    conn.commit()
    return _required_lastrowid(cursor.lastrowid)


def end_tracking_session(conn: sqlite3.Connection, session_id: int) -> None:
    now_ms = _now_ms()
    cursor = conn.execute(
        """
        UPDATE tracking_sessions
        SET ended_at_ms = COALESCE(ended_at_ms, ?), updated_at_ms = ?
        WHERE id = ?
        """,
        (now_ms, now_ms, session_id),
    )
    if cursor.rowcount == 0:
        msg = f"Tracking session not found: {session_id}"
        raise ValueError(msg)
    conn.commit()


def get_active_tracking_session(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT id
        FROM tracking_sessions
        WHERE ended_at_ms IS NULL
        ORDER BY started_at_ms DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return _required_int(row["id"])


def get_tracking_session(
    conn: sqlite3.Connection, session_id: int
) -> TrackingSession | None:
    row = conn.execute(
        """
        SELECT id, name, started_at_ms, ended_at_ms
        FROM tracking_sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _tracking_session_from_row(row)


def list_tracking_sessions(conn: sqlite3.Connection) -> list[TrackingSession]:
    rows = conn.execute(
        """
        SELECT id, name, started_at_ms, ended_at_ms
        FROM tracking_sessions
        ORDER BY started_at_ms DESC, id DESC
        """
    ).fetchall()
    return [_tracking_session_from_row(row) for row in rows]


def attach_harness_session(
    conn: sqlite3.Connection,
    tracking_session_id: int,
    harness: str,
    source_session_id: str,
    *,
    first_seen_ms: int | None,
    last_seen_ms: int | None,
) -> int:
    now_ms = _now_ms()
    existing = conn.execute(
        """
        SELECT id, first_seen_ms, last_seen_ms
        FROM harness_sessions
        WHERE tracking_session_id = ? AND harness = ? AND source_session_id = ?
        """,
        (tracking_session_id, harness, source_session_id),
    ).fetchone()
    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO harness_sessions (
                tracking_session_id,
                harness,
                source_session_id,
                first_seen_ms,
                last_seen_ms,
                created_at_ms,
                updated_at_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tracking_session_id,
                harness,
                source_session_id,
                first_seen_ms,
                last_seen_ms,
                now_ms,
                now_ms,
            ),
        )
        return _required_lastrowid(cursor.lastrowid)

    existing_first = (
        _required_int(existing["first_seen_ms"])
        if existing["first_seen_ms"] is not None
        else None
    )
    existing_last = (
        _required_int(existing["last_seen_ms"])
        if existing["last_seen_ms"] is not None
        else None
    )
    merged_first = _min_optional_int(existing_first, first_seen_ms)
    merged_last = _max_optional_int(existing_last, last_seen_ms)
    conn.execute(
        """
        UPDATE harness_sessions
        SET first_seen_ms = ?, last_seen_ms = ?, updated_at_ms = ?
        WHERE id = ?
        """,
        (merged_first, merged_last, now_ms, _required_int(existing["id"])),
    )
    return _required_int(existing["id"])


def insert_usage_events(
    conn: sqlite3.Connection,
    tracking_session_id: int,
    events: list[UsageEvent],
    *,
    since_ms: int | None = None,
) -> InsertUsageResult:
    filtered_events = [
        event for event in events if since_ms is None or event.created_ms >= since_ms
    ]
    harness_session_ids: dict[tuple[str, str], int] = {}
    rows_inserted = 0
    imported_at_ms = _now_ms()

    with conn:
        grouped_ranges = _group_event_ranges(filtered_events)
        for (
            harness,
            source_session_id,
        ), (
            first_seen_ms,
            last_seen_ms,
        ) in grouped_ranges.items():
            harness_session_ids[(harness, source_session_id)] = (
                attach_harness_session(
                    conn,
                    tracking_session_id,
                    harness,
                    source_session_id,
                    first_seen_ms=first_seen_ms,
                    last_seen_ms=last_seen_ms,
                )
            )

        for event in filtered_events:
            harness_session_id = harness_session_ids[
                (event.harness, event.source_session_id)
            ]
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO usage_events (
                    tracking_session_id,
                    harness_session_id,
                    harness,
                    source_session_id,
                    source_row_id,
                    source_message_id,
                    source_dedup_key,
                    global_dedup_key,
                    fingerprint_hash,
                    role,
                    provider_id,
                    model_id,
                    agent,
                    created_ms,
                    completed_ms,
                    input_tokens,
                    output_tokens,
                    reasoning_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    cost_usd,
                    raw_json,
                    imported_at_ms
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, 'assistant',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    tracking_session_id,
                    harness_session_id,
                    event.harness,
                    event.source_session_id,
                    event.source_row_id,
                    event.source_message_id,
                    event.source_dedup_key,
                    event.global_dedup_key,
                    event.fingerprint_hash,
                    event.provider_id,
                    event.model_id,
                    event.agent,
                    event.created_ms,
                    event.completed_ms,
                    event.tokens.input,
                    event.tokens.output,
                    event.tokens.reasoning,
                    event.tokens.cache_read,
                    event.tokens.cache_write,
                    event.cost_usd,
                    event.raw_json,
                    imported_at_ms,
                ),
            )
            rows_inserted += cursor.rowcount

    return InsertUsageResult(rows_inserted=rows_inserted)


def summarize_tracking_session(
    conn: sqlite3.Connection, session_id: int
) -> TrackingSessionReport:
    session = get_tracking_session(conn, session_id)
    if session is None:
        msg = f"Tracking session not found: {session_id}"
        raise ValueError(msg)

    totals_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
            COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
            COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
            COALESCE(SUM(cost_usd), 0.0) AS cost_usd
        FROM usage_events
        WHERE tracking_session_id = ?
        """,
        (session_id,),
    ).fetchone()

    by_harness_rows = conn.execute(
        """
        SELECT
            harness,
            COUNT(*) AS message_count,
            SUM(
                input_tokens + output_tokens + reasoning_tokens
                + cache_read_tokens + cache_write_tokens
            ) AS total_tokens,
            SUM(cost_usd) AS cost_usd
        FROM usage_events
        WHERE tracking_session_id = ?
        GROUP BY harness
        ORDER BY cost_usd DESC, total_tokens DESC
        """,
        (session_id,),
    ).fetchall()
    by_model_rows = conn.execute(
        """
        SELECT
            provider_id,
            model_id,
            COUNT(*) AS message_count,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens,
            SUM(reasoning_tokens) AS reasoning_tokens,
            SUM(cache_read_tokens) AS cache_read_tokens,
            SUM(cache_write_tokens) AS cache_write_tokens,
            SUM(cost_usd) AS cost_usd
        FROM usage_events
        WHERE tracking_session_id = ?
        GROUP BY provider_id, model_id
        ORDER BY cost_usd DESC, message_count DESC
        """,
        (session_id,),
    ).fetchall()
    by_agent_rows = conn.execute(
        """
        SELECT
            COALESCE(agent, 'unknown') AS agent,
            COUNT(*) AS message_count,
            SUM(
                input_tokens + output_tokens + reasoning_tokens
                + cache_read_tokens + cache_write_tokens
            ) AS total_tokens,
            SUM(cost_usd) AS cost_usd
        FROM usage_events
        WHERE tracking_session_id = ?
        GROUP BY COALESCE(agent, 'unknown')
        ORDER BY cost_usd DESC, total_tokens DESC
        """,
        (session_id,),
    ).fetchall()

    return TrackingSessionReport(
        session=session,
        totals=SessionTotals(
            tokens=_row_tokens(totals_row),
            cost_usd=float(totals_row["cost_usd"]),
        ),
        by_harness=[
            HarnessSummaryRow(
                harness=str(row["harness"]),
                message_count=_required_int(row["message_count"]),
                total_tokens=_required_int(row["total_tokens"]),
                cost_usd=_required_float(row["cost_usd"]),
            )
            for row in by_harness_rows
        ],
        by_model=[
            ModelSummaryRow(
                provider_id=str(row["provider_id"]),
                model_id=str(row["model_id"]),
                message_count=_required_int(row["message_count"]),
                tokens=_row_tokens(row),
                cost_usd=_required_float(row["cost_usd"]),
            )
            for row in by_model_rows
        ],
        by_agent=[
            AgentSummaryRow(
                agent=str(row["agent"]),
                message_count=_required_int(row["message_count"]),
                total_tokens=_required_int(row["total_tokens"]),
                cost_usd=_required_float(row["cost_usd"]),
            )
            for row in by_agent_rows
        ],
    )


def _tracking_session_from_row(row: sqlite3.Row) -> TrackingSession:
    return TrackingSession(
        id=_required_int(row["id"]),
        name=str(row["name"]) if row["name"] is not None else None,
        started_at_ms=_required_int(row["started_at_ms"]),
        ended_at_ms=_optional_int(row["ended_at_ms"]),
    )


def _group_event_ranges(
    events: list[UsageEvent],
) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    grouped: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for event in events:
        key = (event.harness, event.source_session_id)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = (event.created_ms, event.created_ms)
            continue
        grouped[key] = (
            _min_optional_int(existing[0], event.created_ms),
            _max_optional_int(existing[1], event.created_ms),
        )
    return grouped


def _min_optional_int(left: int | None, right: int | None) -> int | None:
    values = [value for value in (left, right) if value is not None]
    if not values:
        return None
    return min(values)


def _max_optional_int(left: int | None, right: int | None) -> int | None:
    values = [value for value in (left, right) if value is not None]
    if not values:
        return None
    return max(values)


def _row_tokens(row: sqlite3.Row) -> TokenBreakdown:
    return TokenBreakdown(
        input=_required_int(row["input_tokens"]),
        output=_required_int(row["output_tokens"]),
        reasoning=_required_int(row["reasoning_tokens"]),
        cache_read=_required_int(row["cache_read_tokens"]),
        cache_write=_required_int(row["cache_write_tokens"]),
    )


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"Expected int value, got {value!r}"
        raise TypeError(msg)
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _required_int(value)


def _required_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"Expected numeric value, got {value!r}"
        raise TypeError(msg)
    return float(value)


def _required_lastrowid(value: int | None) -> int:
    if value is None:
        msg = "SQLite insert did not return a row id."
        raise TypeError(msg)
    return value
