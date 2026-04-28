from __future__ import annotations

from toktrail.db import (
    connect,
    create_tracking_session,
    end_tracking_session,
    get_active_tracking_session,
    insert_usage_events,
    migrate,
    summarize_tracking_session,
)
from toktrail.models import TokenBreakdown, UsageEvent


def make_usage_event(
    *,
    dedup_suffix: str,
    source_session_id: str = "ses-1",
    cost_usd: float = 0.25,
    tokens: TokenBreakdown | None = None,
) -> UsageEvent:
    token_breakdown = tokens or TokenBreakdown(
        input=10,
        output=5,
        reasoning=1,
        cache_read=2,
        cache_write=3,
    )
    return UsageEvent(
        harness="opencode",
        source_session_id=source_session_id,
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"msg-{dedup_suffix}",
        global_dedup_key=f"opencode:msg-{dedup_suffix}",
        fingerprint_hash=f"fingerprint-{dedup_suffix}",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
        agent="build",
        created_ms=1700000000000 + int(dedup_suffix[-1]) * 100,
        completed_ms=1700000000100 + int(dedup_suffix[-1]) * 100,
        tokens=token_breakdown,
        cost_usd=cost_usd,
        raw_json="{}",
    )


def test_migrate_creates_tables_and_is_idempotent(tmp_path) -> None:
    conn = connect(tmp_path / "toktrail.db")

    migrate(conn)
    migrate(conn)

    table_names = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])

    assert {"tracking_sessions", "harness_sessions", "usage_events"} <= table_names
    assert user_version == 1


def test_create_tracking_session_and_end_session(tmp_path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)

    session_id = create_tracking_session(conn, "test")

    assert get_active_tracking_session(conn) == session_id

    end_tracking_session(conn, session_id)

    assert get_active_tracking_session(conn) is None


def test_insert_usage_events_attaches_multiple_source_sessions(tmp_path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "test")

    first = make_usage_event(dedup_suffix="1", source_session_id="ses-1")
    second = make_usage_event(dedup_suffix="2", source_session_id="ses-2")

    insert_usage_events(conn, session_id, [first, second])

    harness_session_count = int(
        conn.execute("SELECT COUNT(*) FROM harness_sessions").fetchone()[0]
    )
    assert harness_session_count == 2


def test_insert_usage_events_is_idempotent_and_aggregates_correctly(tmp_path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "test")

    first = make_usage_event(dedup_suffix="1", cost_usd=0.25)
    second = make_usage_event(
        dedup_suffix="2",
        cost_usd=0.50,
        tokens=TokenBreakdown(
            input=20,
            output=10,
            reasoning=2,
            cache_read=4,
            cache_write=6,
        ),
    )

    first_insert = insert_usage_events(conn, session_id, [first, second])
    second_insert = insert_usage_events(conn, session_id, [first, second])
    report = summarize_tracking_session(conn, session_id)

    assert first_insert.rows_inserted == 2
    assert second_insert.rows_inserted == 0
    assert report.totals.tokens.input == 30
    assert report.totals.tokens.output == 15
    assert report.totals.tokens.reasoning == 3
    assert report.totals.tokens.cache_read == 6
    assert report.totals.tokens.cache_write == 9
    assert report.totals.tokens.total == 63
    assert report.totals.cost_usd == 0.75
    assert report.by_harness[0].total_tokens == 63
    assert report.by_model[0].model_id == "claude-sonnet-4"
    assert report.by_agent[0].agent == "build"
