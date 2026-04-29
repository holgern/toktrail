from __future__ import annotations

from toktrail.db import (
    connect,
    create_tracking_session,
    end_tracking_session,
    get_active_tracking_session,
    insert_usage_events,
    migrate,
    summarize_tracking_session,
    summarize_usage,
)
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import UsageReportFilter


def make_usage_event(
    *,
    dedup_suffix: str,
    source_session_id: str = "ses-1",
    cost_usd: float = 0.25,
    tokens: TokenBreakdown | None = None,
    harness: str = "opencode",
    provider_id: str = "anthropic",
    model_id: str = "claude-sonnet-4",
    thinking_level: str | None = None,
    agent: str | None = "build",
) -> UsageEvent:
    token_breakdown = tokens or TokenBreakdown(
        input=10,
        output=5,
        reasoning=1,
        cache_read=2,
        cache_write=3,
    )
    return UsageEvent(
        harness=harness,
        source_session_id=source_session_id,
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"msg-{dedup_suffix}",
        global_dedup_key=f"{harness}:msg-{dedup_suffix}",
        fingerprint_hash=f"fingerprint-{dedup_suffix}",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=thinking_level,
        agent=agent,
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

    assert {
        "tracking_sessions",
        "harness_sessions",
        "usage_events",
        "tracking_session_events",
    } <= table_names
    assert user_version == 2


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
    assert report.totals.source_cost_usd == 0.75
    assert report.totals.actual_cost_usd == 0.75
    assert report.totals.virtual_cost_usd == 0.0
    assert report.totals.savings_usd == -0.75
    assert report.totals.unpriced_count == 1
    assert report.by_harness[0].total_tokens == 63
    assert report.by_harness[0].source_cost_usd == 0.75
    assert report.by_harness[0].actual_cost_usd == 0.75
    assert report.by_model[0].model_id == "claude-sonnet-4"
    assert report.by_model[0].source_cost_usd == 0.75
    assert report.by_model[0].actual_cost_usd == 0.75
    assert report.by_agent[0].agent == "build"
    assert report.by_agent[0].source_cost_usd == 0.75
    assert report.by_agent[0].actual_cost_usd == 0.75


def test_summarize_usage_applies_filters_and_echoes_them(tmp_path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "test")

    insert_usage_events(
        conn,
        session_id,
        [
            make_usage_event(
                dedup_suffix="1",
                harness="pi",
                source_session_id="pi-1",
                cost_usd=0.1,
                tokens=TokenBreakdown(input=100, output=5),
                provider_id="anthropic",
                model_id="claude-sonnet-4",
                thinking_level="high",
                agent="plan",
            ),
            make_usage_event(
                dedup_suffix="2",
                harness="pi",
                source_session_id="pi-2",
                cost_usd=0.2,
                tokens=TokenBreakdown(input=50, cache_read=10),
                provider_id="anthropic",
                model_id="claude-sonnet-4",
                thinking_level="low",
                agent=None,
            ),
            make_usage_event(
                dedup_suffix="3",
                harness="copilot",
                source_session_id="conv-1",
                cost_usd=0.0,
                tokens=TokenBreakdown(input=7, output=9),
                provider_id="github-copilot",
                model_id="gpt-5",
                agent=None,
            ),
        ],
    )

    report = summarize_usage(
        conn,
        UsageReportFilter(
            tracking_session_id=session_id,
            harness="pi",
            source_session_id="pi-1",
            provider_id="anthropic",
            model_id="claude-sonnet-4",
            thinking_level="high",
            agent="plan",
        ),
    )

    assert report.filters.as_dict() == {
        "harness": "pi",
        "source_session_id": "pi-1",
        "provider_id": "anthropic",
        "model_id": "claude-sonnet-4",
        "thinking_level": "high",
        "agent": "plan",
    }
    assert report.totals.tokens.input == 100
    assert report.totals.tokens.output == 5
    assert report.totals.tokens.total == 105
    assert report.totals.source_cost_usd == 0.1
    assert report.totals.actual_cost_usd == 0.0
    assert report.totals.virtual_cost_usd == 0.0
    assert report.totals.unpriced_count == 1
    assert report.by_harness[0].harness == "pi"
    assert report.by_harness[0].source_cost_usd == 0.1
    assert report.by_harness[0].actual_cost_usd == 0.0
    assert report.by_model[0].model_id == "claude-sonnet-4"
    assert report.by_model[0].thinking_level == "high"
    assert report.by_model[0].source_cost_usd == 0.1
    assert report.by_model[0].actual_cost_usd == 0.0
    assert report.by_agent[0].agent == "plan"
    assert report.by_agent[0].source_cost_usd == 0.1
    assert report.by_agent[0].actual_cost_usd == 0.0


def test_summarize_usage_supports_unscoped_period_ranges(tmp_path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    first = make_usage_event(dedup_suffix="1", cost_usd=0.1)
    second = make_usage_event(dedup_suffix="2", cost_usd=0.2)

    insert_usage_events(conn, None, [first, second])
    report = summarize_usage(
        conn,
        UsageReportFilter(
            since_ms=first.created_ms,
            until_ms=second.created_ms,
        ),
    )

    assert report.session is None
    assert report.totals.tokens.total == first.tokens.total
    assert report.totals.source_cost_usd == first.source_cost_usd


def test_summarize_usage_can_split_and_collapse_thinking_levels(tmp_path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "thinking")

    insert_usage_events(
        conn,
        session_id,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="openai",
                model_id="gpt-5.4",
                thinking_level="high",
                tokens=TokenBreakdown(input=10, output=5),
                cost_usd=0.0,
            ),
            make_usage_event(
                dedup_suffix="2",
                provider_id="openai",
                model_id="gpt-5.4",
                thinking_level="low",
                tokens=TokenBreakdown(input=20, output=7),
                cost_usd=0.0,
            ),
        ],
    )

    split_report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id),
    )
    collapsed_report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id, split_thinking=False),
    )

    assert [
        (row.provider_id, row.model_id, row.thinking_level, row.total_tokens)
        for row in split_report.by_model
    ] == [
        ("openai", "gpt-5.4", "high", 15),
        ("openai", "gpt-5.4", "low", 27),
    ]
    assert [
        (row.provider_id, row.model_id, row.thinking_level, row.total_tokens)
        for row in collapsed_report.by_model
    ] == [("openai", "gpt-5.4", None, 42)]
    assert split_report.totals.tokens.total == collapsed_report.totals.tokens.total
    assert (
        split_report.totals.actual_cost_usd
        == collapsed_report.totals.actual_cost_usd
    )


def test_session_report_uses_tracking_session_events_for_membership(
    tmp_path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    first_session_id = create_tracking_session(conn, "first")
    event = make_usage_event(dedup_suffix="1", cost_usd=0.0)

    first_insert = insert_usage_events(conn, first_session_id, [event])
    end_tracking_session(conn, first_session_id)
    second_session_id = create_tracking_session(conn, "second")
    second_insert = insert_usage_events(conn, second_session_id, [event])
    second_report = summarize_tracking_session(conn, second_session_id)

    assert first_insert.rows_inserted == 1
    assert first_insert.rows_linked == 1
    assert second_insert.rows_inserted == 0
    assert second_insert.rows_linked == 1
    assert second_report.session.id == second_session_id
    assert second_report.totals.tokens.total == event.tokens.total
