from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from time import time

from toktrail.config import (
    ActualCostRule,
    CostingConfig,
    Price,
    SubscriptionConfig,
    SubscriptionWindowConfig,
)
from toktrail.db import (
    connect,
    create_tracking_session,
    end_tracking_session,
    get_active_tracking_session,
    insert_usage_events,
    migrate,
    summarize_subscription_usage,
    summarize_tracking_session,
    summarize_usage,
    summarize_usage_series,
)
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.periods import resolve_fixed_subscription_window
from toktrail.reporting import UsageReportFilter, UsageSeriesFilter


def make_price(
    *,
    provider: str = "openai",
    model: str = "gpt-5-mini",
    input_usd_per_1m: float = 1.0,
    output_usd_per_1m: float = 2.0,
) -> Price:
    return Price(
        provider=provider,
        model=model,
        aliases=(),
        input_usd_per_1m=input_usd_per_1m,
        cached_input_usd_per_1m=None,
        cache_write_usd_per_1m=None,
        output_usd_per_1m=output_usd_per_1m,
        reasoning_usd_per_1m=None,
    )


def make_usage_event(
    *,
    dedup_suffix: str,
    source_session_id: str = "ses-1",
    source_cost_usd: float | Decimal = 0.25,
    tokens: TokenBreakdown | None = None,
    harness: str = "opencode",
    provider_id: str = "anthropic",
    model_id: str = "claude-sonnet-4",
    thinking_level: str | None = None,
    agent: str | None = "build",
    created_ms: int | None = None,
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
        created_ms=created_ms
        if created_ms is not None
        else int(time() * 1000) + int(dedup_suffix[-1]) * 100,
        completed_ms=(created_ms + 100)
        if created_ms is not None
        else int(time() * 1000) + int(dedup_suffix[-1]) * 100 + 100,
        tokens=token_breakdown,
        source_cost_usd=Decimal(str(source_cost_usd))
        if not isinstance(source_cost_usd, Decimal)
        else source_cost_usd,
        raw_json="{}",
    )


def test_migrate_creates_tables_and_is_idempotent(tmp_path: Path) -> None:
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
        "runs",
        "source_sessions",
        "usage_events",
        "run_events",
        "state_metadata",
    } <= table_names
    assert user_version == 5


def test_source_costs_are_stored_and_aggregated_as_exact_decimals(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "test")

    insert_usage_events(
        conn,
        session_id,
        [
            make_usage_event(dedup_suffix="1", source_cost_usd=Decimal("0.10")),
            make_usage_event(dedup_suffix="2", source_cost_usd=Decimal("0.20")),
        ],
    )

    stored_costs = [
        row["source_cost_usd"]
        for row in conn.execute(
            "SELECT source_cost_usd FROM usage_events ORDER BY id"
        ).fetchall()
    ]
    report = summarize_tracking_session(conn, session_id)

    assert stored_costs == ["0.10", "0.20"]
    assert report.totals.source_cost_usd == Decimal("0.30")


def test_create_tracking_session_and_end_session(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)

    session_id = create_tracking_session(conn, "test")

    assert get_active_tracking_session(conn) == session_id

    end_tracking_session(conn, session_id)

    assert get_active_tracking_session(conn) is None


def test_tracking_session_has_sync_id_and_origin_machine_id(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)

    session_id = create_tracking_session(conn, "test")
    row = conn.execute(
        "SELECT sync_id, origin_machine_id FROM runs WHERE id = ?",
        (session_id,),
    ).fetchone()

    assert row is not None
    assert row["sync_id"]
    assert row["origin_machine_id"]


def test_insert_usage_events_attaches_multiple_source_sessions(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "test")

    first = make_usage_event(dedup_suffix="1", source_session_id="ses-1")
    second = make_usage_event(dedup_suffix="2", source_session_id="ses-2")

    insert_usage_events(conn, session_id, [first, second])

    harness_session_count = int(
        conn.execute("SELECT COUNT(*) FROM source_sessions").fetchone()[0]
    )
    assert harness_session_count == 2


def test_insert_usage_events_is_idempotent_and_aggregates_correctly(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "test")

    first = make_usage_event(dedup_suffix="1", source_cost_usd=0.25)
    second = make_usage_event(
        dedup_suffix="2",
        source_cost_usd=0.50,
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
    assert report.totals.source_cost_usd == Decimal("0.75")
    assert report.totals.actual_cost_usd == 0.75
    assert report.totals.virtual_cost_usd == 0.0
    assert report.totals.savings_usd == -0.75
    assert report.totals.unpriced_count == 1
    assert report.by_harness[0].total_tokens == 63
    assert report.by_harness[0].source_cost_usd == Decimal("0.75")
    assert report.by_harness[0].actual_cost_usd == 0.75
    assert report.by_model[0].model_id == "claude-sonnet-4"
    assert report.by_model[0].source_cost_usd == Decimal("0.75")
    assert report.by_model[0].actual_cost_usd == 0.75
    assert report.by_activity[0].agent == "build"
    assert report.by_activity[0].source_cost_usd == Decimal("0.75")
    assert report.by_activity[0].actual_cost_usd == 0.75


def test_summarize_usage_applies_filters_and_echoes_them(tmp_path: Path) -> None:
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
                source_cost_usd=0.1,
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
                source_cost_usd=0.2,
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
                source_cost_usd=0.0,
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
            split_thinking=True,
        ),
    )

    assert report.filters.harness == "pi"
    assert report.filters.source_session_id == "pi-1"
    assert report.filters.provider_id == "anthropic"
    assert report.filters.model_id == "claude-sonnet-4"
    assert report.filters.thinking_level == "high"
    assert report.filters.agent == "plan"
    assert report.filters.split_thinking is True
    assert isinstance(report.filters.since_ms, int)
    assert report.session is not None
    assert report.filters.since_ms == report.session.started_at_ms
    assert report.totals.tokens.input == 100
    assert report.totals.tokens.output == 5
    assert report.totals.tokens.total == 105
    assert report.totals.source_cost_usd == Decimal("0.1")
    assert report.totals.actual_cost_usd == 0.0
    assert report.totals.virtual_cost_usd == 0.0
    assert report.totals.unpriced_count == 1
    assert report.by_harness[0].harness == "pi"
    assert report.by_harness[0].source_cost_usd == Decimal("0.1")
    assert report.by_harness[0].actual_cost_usd == 0.0
    assert report.by_model[0].model_id == "claude-sonnet-4"
    assert report.by_model[0].thinking_level == "high"
    assert report.by_model[0].source_cost_usd == Decimal("0.1")
    assert report.by_model[0].actual_cost_usd == 0.0
    assert report.by_activity[0].agent == "plan"
    assert report.by_activity[0].source_cost_usd == Decimal("0.1")
    assert report.by_activity[0].actual_cost_usd == 0.0


def test_summarize_usage_supports_unscoped_period_ranges(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    first = make_usage_event(dedup_suffix="1", source_cost_usd=0.1)
    second = make_usage_event(dedup_suffix="2", source_cost_usd=0.2)

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


def test_summarize_usage_series_daily_buckets_and_breakdown(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    day1 = 1748131200000
    day2 = 1748217600000

    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                model_id="claude-sonnet-4",
                source_session_id="session-a",
                created_ms=day1,
                tokens=TokenBreakdown(input=10, output=2),
            ),
            make_usage_event(
                dedup_suffix="2",
                model_id="gpt-5.1",
                source_session_id="session-b",
                created_ms=day2,
                tokens=TokenBreakdown(input=20, output=3),
            ),
        ],
    )

    report = summarize_usage_series(
        conn,
        UsageSeriesFilter(
            granularity="daily",
            breakdown=True,
            since_ms=day1,
            until_ms=day2 + 86_400_000,
        ),
    )

    assert [bucket.key for bucket in report.buckets] == ["2025-05-26", "2025-05-25"]
    assert report.totals.tokens.total == 35
    assert report.buckets[0].by_model[0].model_id == "gpt-5.1"
    assert report.buckets[1].by_model[0].model_id == "claude-sonnet-4"


def test_summarize_usage_series_weekly_monthly_instances_project(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    day1 = 1748131200000
    day2 = 1748649600000

    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                source_session_id="project-a",
                created_ms=day1,
                tokens=TokenBreakdown(input=10),
            ),
            make_usage_event(
                dedup_suffix="2",
                source_session_id="project-b",
                created_ms=day2,
                tokens=TokenBreakdown(input=20),
            ),
        ],
    )

    weekly = summarize_usage_series(
        conn,
        UsageSeriesFilter(granularity="weekly", order="asc"),
    )
    monthly = summarize_usage_series(conn, UsageSeriesFilter(granularity="monthly"))
    instances = summarize_usage_series(
        conn,
        UsageSeriesFilter(granularity="daily", instances=True, order="asc"),
    )

    assert [bucket.key for bucket in weekly.buckets] == ["2025-05-19", "2025-05-26"]
    assert [bucket.key for bucket in monthly.buckets] == ["2025-05"]
    assert [instance.instance_key for instance in instances.instances] == [
        "opencode/project-a",
        "opencode/project-b",
    ]


def test_summarize_usage_can_split_and_collapse_thinking_levels(tmp_path: Path) -> None:
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
                source_cost_usd=0.0,
            ),
            make_usage_event(
                dedup_suffix="2",
                provider_id="openai",
                model_id="gpt-5.4",
                thinking_level="low",
                tokens=TokenBreakdown(input=20, output=7),
                source_cost_usd=0.0,
            ),
        ],
    )

    split_report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id, split_thinking=True),
    )
    collapsed_report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id),
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
        split_report.totals.actual_cost_usd == collapsed_report.totals.actual_cost_usd
    )


def test_session_report_uses_tracking_session_events_for_membership(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    first_session_id = create_tracking_session(conn, "first")
    event = make_usage_event(dedup_suffix="1", source_cost_usd=0.0)

    first_insert = insert_usage_events(conn, first_session_id, [event])
    end_tracking_session(conn, first_session_id)
    second_session_id = create_tracking_session(conn, "second")
    second_insert = insert_usage_events(conn, second_session_id, [event])
    second_report = summarize_tracking_session(conn, second_session_id)

    assert first_insert.rows_inserted == 1
    assert first_insert.rows_linked == 1
    assert second_insert.rows_inserted == 0
    assert second_insert.rows_linked == 1
    assert second_report.session is not None
    assert second_report.session.id == second_session_id
    assert second_report.totals.tokens.total == event.tokens.total


def test_summarize_usage_exposes_unconfigured_models(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "pricing")
    insert_usage_events(
        conn,
        session_id,
        [
            make_usage_event(
                dedup_suffix="1",
                harness="copilot",
                provider_id="github-copilot",
                model_id="gpt-5.4",
                thinking_level="high",
                tokens=TokenBreakdown(input=100, output=20, cache_read=50),
                source_cost_usd=0.0,
            )
        ],
    )

    report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id, split_thinking=True),
        costing_config=CostingConfig(
            default_actual_mode="zero",
            default_virtual_mode="pricing",
        ),
    )

    assert [row.as_dict() for row in report.unconfigured_models] == [
        {
            "required": ["virtual"],
            "harness": "copilot",
            "provider_id": "github-copilot",
            "model_id": "gpt-5.4",
            "thinking_level": "high",
            "message_count": 1,
            "input": 100,
            "output": 20,
            "reasoning": 0,
            "cache_read": 50,
            "cache_write": 0,
            "cache_output": 0,
            "total": 170,
        }
    ]


def test_summarize_usage_unconfigured_models_distinguish_harness_actual_rules(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "pricing")
    insert_usage_events(
        conn,
        session_id,
        [
            make_usage_event(
                dedup_suffix="1",
                harness="opencode",
                provider_id="openai-codex",
                model_id="gpt-5.4",
                tokens=TokenBreakdown(input=40, output=10),
                source_cost_usd=0.0,
            ),
            make_usage_event(
                dedup_suffix="2",
                harness="copilot",
                provider_id="openai-codex",
                model_id="gpt-5.4",
                tokens=TokenBreakdown(input=20, output=5),
                source_cost_usd=0.0,
            ),
        ],
    )

    report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id),
        costing_config=CostingConfig(
            default_actual_mode="zero",
            default_virtual_mode="zero",
            actual_rules=(
                ActualCostRule(
                    harness="opencode",
                    provider="openai-codex",
                    model="gpt-5.4",
                    mode="pricing",
                ),
            ),
            actual_prices=(make_price(provider="openai", model="gpt-5.4"),),
        ),
    )

    assert [
        (row.harness, row.required, row.total_tokens)
        for row in report.unconfigured_models
    ] == [("opencode", ("actual",), 50)]


def test_summarize_usage_unconfigured_models_collapse_thinking_when_requested(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "thinking")
    insert_usage_events(
        conn,
        session_id,
        [
            make_usage_event(
                dedup_suffix="1",
                harness="copilot",
                provider_id="github-copilot",
                model_id="gpt-5.4",
                thinking_level="high",
                tokens=TokenBreakdown(input=10, output=5),
                source_cost_usd=0.0,
            ),
            make_usage_event(
                dedup_suffix="2",
                harness="copilot",
                provider_id="github-copilot",
                model_id="gpt-5.4",
                thinking_level="low",
                tokens=TokenBreakdown(input=20, output=7),
                source_cost_usd=0.0,
            ),
        ],
    )

    split_report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id, split_thinking=True),
        costing_config=CostingConfig(
            default_actual_mode="zero",
            default_virtual_mode="pricing",
        ),
    )
    collapsed_report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id),
        costing_config=CostingConfig(
            default_actual_mode="zero",
            default_virtual_mode="pricing",
        ),
    )

    assert [
        (row.thinking_level, row.message_count, row.total_tokens)
        for row in split_report.unconfigured_models
    ] == [("low", 1, 27), ("high", 1, 15)]
    assert [
        (row.thinking_level, row.message_count, row.total_tokens)
        for row in collapsed_report.unconfigured_models
    ] == [(None, 2, 42)]


def test_summarize_usage_returns_provider_summary_rows(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "providers")
    insert_usage_events(
        conn,
        session_id,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="opencode-go",
                model_id="deepseek-v4-pro",
                tokens=TokenBreakdown(input=10, output=2),
                source_cost_usd=1.0,
            ),
            make_usage_event(
                dedup_suffix="2",
                provider_id="opencode-go",
                model_id="deepseek-v4-lite",
                tokens=TokenBreakdown(input=20, output=3),
                source_cost_usd=2.0,
            ),
            make_usage_event(
                dedup_suffix="3",
                provider_id="anthropic",
                model_id="claude-sonnet-4",
                tokens=TokenBreakdown(input=5, output=1),
                source_cost_usd=0.5,
            ),
        ],
    )

    report = summarize_usage(conn, UsageReportFilter(tracking_session_id=session_id))

    assert [row.provider_id for row in report.by_provider] == [
        "opencode-go",
        "anthropic",
    ]
    assert report.by_provider[0].message_count == 2
    assert report.by_provider[0].tokens.total == 35
    assert report.by_provider[0].source_cost_usd == Decimal("3.0")


def test_summarize_usage_provider_filter_filters_provider_summary(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    session_id = create_tracking_session(conn, "providers")
    insert_usage_events(
        conn,
        session_id,
        [
            make_usage_event(dedup_suffix="1", provider_id="opencode-go"),
            make_usage_event(dedup_suffix="2", provider_id="anthropic"),
        ],
    )

    report = summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id, provider_id="opencode-go"),
    )

    assert [row.provider_id for row in report.by_provider] == ["opencode-go"]


def test_summarize_subscription_usage_zai_5h_only(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="z-ai",
                source_cost_usd=3.2,
                created_ms=1777799400000,
            ),
            make_usage_event(
                dedup_suffix="2",
                provider_id="z-ai",
                source_cost_usd=5.8,
                created_ms=1777801200000,
            ),
        ],
    )
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="z-ai-plan",
                usage_providers=("z-ai",),
                display_name="z.ai",
                timezone="UTC",
                quota_cost_basis="source",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=10,
                        reset_mode="fixed",
                        reset_at="2026-05-03T08:00:00+00:00",
                    ),
                ),
            ),
        ),
    )

    report = summarize_subscription_usage(
        conn,
        config,
        now_ms=1777802400000,
    )

    assert len(report.subscriptions) == 1
    row = report.subscriptions[0]
    assert row.subscription_id == "z-ai-plan"
    assert row.usage_provider_ids == ("z-ai",)
    assert [period.period for period in row.periods] == ["5h"]
    assert row.periods[0].used_usd == Decimal("9.0")
    assert row.periods[0].remaining_usd == Decimal("1.0")


def test_summarize_subscription_usage_uses_covered_provider_for_first_use_5h(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="zai",
                model_id="glm-4.5",
                source_cost_usd=0,
                tokens=TokenBreakdown(input=1_000_000, output=100_000),
                created_ms=1777890600000,
            ),
        ],
    )

    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="zai-coding-plan",
                display_name="Zai Coding Plan",
                usage_providers=("zai",),
                timezone="Europe/Berlin",
                quota_cost_basis="virtual",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=12,
                        reset_mode="first_use",
                        reset_at="2026-05-01T00:00:00+02:00",
                    ),
                ),
            ),
        ),
        virtual_prices=(
            make_price(
                provider="zai",
                model="glm-4.5",
                input_usd_per_1m=4.0,
                output_usd_per_1m=8.0,
            ),
        ),
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777892400000)
    row = report.subscriptions[0]
    period = row.periods[0]

    assert row.subscription_id == "zai-coding-plan"
    assert row.usage_provider_ids == ("zai",)
    assert period.status == "active"
    assert period.message_count == 1
    assert period.since_ms == 1777890600000
    assert period.until_ms == 1777908600000
    assert period.used_usd > Decimal("0")


def test_summarize_subscription_usage_codex_first_use_5h_and_weekly(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="codex",
                source_cost_usd=3.0,
                created_ms=1777799400000,
            ),
            make_usage_event(
                dedup_suffix="2",
                provider_id="codex",
                source_cost_usd=2.0,
                created_ms=1777801200000,
            ),
        ],
    )
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="codex",
                usage_providers=("codex",),
                timezone="UTC",
                quota_cost_basis="source",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=20,
                        reset_mode="first_use",
                        reset_at="2026-05-03T00:00:00+00:00",
                    ),
                    SubscriptionWindowConfig(
                        period="weekly",
                        limit_usd=200,
                        reset_mode="first_use",
                        reset_at="2026-05-03T00:00:00+00:00",
                    ),
                ),
            ),
        ),
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    periods = report.subscriptions[0].periods
    assert [period.period for period in periods] == ["5h", "weekly"]
    assert periods[0].status == "active"
    assert periods[0].used_usd == Decimal("5.0")
    assert periods[1].status == "active"
    assert periods[1].used_usd == Decimal("5.0")


def test_summarize_subscription_usage_opencode_go_5h_weekly_monthly(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="opencode-go",
                source_cost_usd=2.0,
                created_ms=1777799400000,
            ),
            make_usage_event(
                dedup_suffix="2",
                provider_id="opencode-go",
                source_cost_usd=3.0,
                created_ms=1777801200000,
            ),
        ],
    )

    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                timezone="UTC",
                id="opencode-go",
                usage_providers=("opencode-go",),
                quota_cost_basis="source",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=25,
                        reset_mode="fixed",
                        reset_at="2026-05-03T08:00:00+00:00",
                    ),
                    SubscriptionWindowConfig(
                        period="weekly",
                        limit_usd=100,
                        reset_mode="fixed",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                    SubscriptionWindowConfig(
                        period="monthly",
                        limit_usd=400,
                        reset_mode="fixed",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                ),
            ),
        ),
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    periods = report.subscriptions[0].periods
    assert [period.period for period in periods] == ["5h", "weekly", "monthly"]
    assert all(period.used_usd == Decimal("5.0") for period in periods)


def test_summarize_subscription_usage_disabled_window_is_omitted(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="opencode-go",
                usage_providers=("opencode-go",),
                timezone="UTC",
                quota_cost_basis="source",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=10,
                        reset_at="2026-05-03T08:00:00+00:00",
                        enabled=False,
                    ),
                    SubscriptionWindowConfig(
                        period="weekly",
                        limit_usd=50,
                        reset_at="2026-05-01T00:00:00+00:00",
                        enabled=True,
                    ),
                ),
            ),
        )
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    assert [period.period for period in report.subscriptions[0].periods] == ["weekly"]


def test_summarize_subscription_usage_reset_at_excludes_old_usage(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="opencode-go",
                source_cost_usd=4.0,
                created_ms=1777797600000,
            ),
            make_usage_event(
                dedup_suffix="2",
                provider_id="opencode-go",
                source_cost_usd=6.0,
                created_ms=1777801200000,
            ),
        ],
    )
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="opencode-go",
                usage_providers=("opencode-go",),
                timezone="UTC",
                quota_cost_basis="source",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=20,
                        reset_mode="fixed",
                        reset_at="2026-05-03T09:00:00+00:00",
                    ),
                ),
            ),
        )
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    assert report.subscriptions[0].periods[0].used_usd == Decimal("6.0")


def test_summarize_subscription_usage_first_use_waiting_returns_zero_usage(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="codex",
                usage_providers=("codex",),
                timezone="UTC",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=20,
                        reset_mode="first_use",
                        reset_at="2026-05-03T00:00:00+00:00",
                    ),
                ),
            ),
        )
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    period = report.subscriptions[0].periods[0]
    assert period.status == "waiting_for_first_use"
    assert period.since_ms is None
    assert period.until_ms is None
    assert period.used_usd == Decimal("0")


def test_summarize_subscription_usage_first_use_expired_waiting_returns_zero(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="codex",
                source_cost_usd=4.0,
                created_ms=1777799400000,
            ),
        ],
    )
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="codex",
                usage_providers=("codex",),
                timezone="UTC",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=20,
                        reset_mode="first_use",
                        reset_at="2026-05-03T00:00:00+00:00",
                    ),
                ),
            ),
        )
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777821000000)
    period = report.subscriptions[0].periods[0]
    assert period.status == "expired_waiting_for_next_use"
    assert period.since_ms is None
    assert period.until_ms is None
    assert period.last_since_ms is not None
    assert period.last_until_ms is not None
    assert period.last_usage_ms == 1777799400000
    assert period.used_usd == Decimal("0")


def test_subscription_billing_break_even_not_reached(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="opencode-go",
                source_cost_usd=7.0,
                created_ms=1777799400000,
            )
        ],
    )
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="opencode-go",
                usage_providers=("opencode-go",),
                timezone="UTC",
                quota_cost_basis="source",
                fixed_cost_usd=10.0,
                fixed_cost_period="monthly",
                fixed_cost_reset_at="2026-05-01T00:00:00+00:00",
                windows=(
                    SubscriptionWindowConfig(
                        period="monthly",
                        limit_usd=200,
                        reset_mode="fixed",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                ),
            ),
        ),
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    billing = report.subscriptions[0].billing

    assert billing is not None
    assert billing.fixed_cost_usd == Decimal("10")
    assert billing.value_usd == Decimal("7.0")
    assert billing.net_savings_usd == Decimal("-3.0")
    assert billing.break_even_remaining_usd == Decimal("3.0")


def test_subscription_billing_break_even_reached(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="opencode-go",
                source_cost_usd=13.0,
                created_ms=1777799400000,
            )
        ],
    )
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="opencode-go",
                usage_providers=("opencode-go",),
                timezone="UTC",
                quota_cost_basis="source",
                fixed_cost_usd=10.0,
                fixed_cost_period="monthly",
                fixed_cost_reset_at="2026-05-01T00:00:00+00:00",
                windows=(
                    SubscriptionWindowConfig(
                        period="monthly",
                        limit_usd=200,
                        reset_mode="fixed",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                ),
            ),
        ),
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    billing = report.subscriptions[0].billing

    assert billing is not None
    assert billing.net_savings_usd == Decimal("3.0")
    assert billing.break_even_remaining_usd == Decimal("0")


def test_subscription_billing_basis_selects_configured_cost_basis(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="opencode-go",
                source_cost_usd=7.0,
                created_ms=1777799400000,
                tokens=TokenBreakdown(input=0, output=1_000_000),
            )
        ],
    )
    virtual_price = make_price(
        provider="opencode-go",
        model="claude-sonnet-4",
        input_usd_per_1m=0.0,
        output_usd_per_1m=13.0,
    )
    source_basis = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="opencode-go",
                usage_providers=("opencode-go",),
                timezone="UTC",
                quota_cost_basis="source",
                fixed_cost_usd=10.0,
                fixed_cost_period="monthly",
                fixed_cost_reset_at="2026-05-01T00:00:00+00:00",
                fixed_cost_basis="source",
                windows=(
                    SubscriptionWindowConfig(
                        period="monthly",
                        limit_usd=200,
                        reset_mode="fixed",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                ),
            ),
        ),
        virtual_prices=(virtual_price,),
    )
    virtual_basis = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="opencode-go",
                usage_providers=("opencode-go",),
                timezone="UTC",
                quota_cost_basis="source",
                fixed_cost_usd=10.0,
                fixed_cost_period="monthly",
                fixed_cost_reset_at="2026-05-01T00:00:00+00:00",
                fixed_cost_basis="virtual",
                windows=(
                    SubscriptionWindowConfig(
                        period="monthly",
                        limit_usd=200,
                        reset_mode="fixed",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                ),
            ),
        ),
        virtual_prices=(virtual_price,),
    )

    source_report = summarize_subscription_usage(
        conn,
        source_basis,
        now_ms=1777802400000,
    )
    virtual_report = summarize_subscription_usage(
        conn,
        virtual_basis,
        now_ms=1777802400000,
    )

    source_billing = source_report.subscriptions[0].billing
    virtual_billing = virtual_report.subscriptions[0].billing
    assert source_billing is not None
    assert virtual_billing is not None
    assert source_billing.value_usd == Decimal("7.0")
    assert virtual_billing.value_usd == Decimal("13.0")


def test_subscription_billing_window_is_independent_from_quota_windows(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="opencode-go",
                source_cost_usd=5.0,
                created_ms=1777799400000,
            )
        ],
    )
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="opencode-go",
                usage_providers=("opencode-go",),
                timezone="UTC",
                quota_cost_basis="source",
                fixed_cost_usd=10.0,
                fixed_cost_period="monthly",
                fixed_cost_reset_at="2026-05-01T00:00:00+00:00",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=10,
                        reset_mode="first_use",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                    SubscriptionWindowConfig(
                        period="weekly",
                        limit_usd=50,
                        reset_mode="first_use",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                    SubscriptionWindowConfig(
                        period="monthly",
                        limit_usd=200,
                        reset_mode="first_use",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                ),
            ),
        ),
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    billing = report.subscriptions[0].billing
    assert billing is not None
    expected = resolve_fixed_subscription_window(
        period="monthly",
        reset_at="2026-05-01T00:00:00+00:00",
        timezone_name="UTC",
        now_ms=1777802400000,
    )
    assert billing.period == "monthly"
    assert billing.reset_at == "2026-05-01T00:00:00+00:00"
    assert billing.since_ms == expected.since_ms
    assert billing.until_ms == expected.until_ms


def test_subscription_billing_yearly_window(tmp_path: Path) -> None:
    conn = connect(tmp_path / "toktrail.db")
    migrate(conn)
    insert_usage_events(
        conn,
        None,
        [
            make_usage_event(
                dedup_suffix="1",
                provider_id="opencode-go",
                source_cost_usd=13.0,
                created_ms=1777799400000,
            )
        ],
    )
    config = CostingConfig(
        subscriptions=(
            SubscriptionConfig(
                id="opencode-go-plan",
                usage_providers=("opencode-go",),
                timezone="UTC",
                quota_cost_basis="source",
                fixed_cost_usd=120.0,
                fixed_cost_period="yearly",
                fixed_cost_reset_at="2026-01-01T00:00:00+00:00",
                fixed_cost_basis="virtual",
                windows=(
                    SubscriptionWindowConfig(
                        period="monthly",
                        limit_usd=200,
                        reset_mode="fixed",
                        reset_at="2026-05-01T00:00:00+00:00",
                    ),
                ),
            ),
        ),
        virtual_prices=(
            make_price(
                provider="opencode-go",
                model="claude-sonnet-4",
                input_usd_per_1m=0.0,
                output_usd_per_1m=13.0,
            ),
        ),
    )

    report = summarize_subscription_usage(conn, config, now_ms=1777802400000)
    billing = report.subscriptions[0].billing
    assert billing is not None
    assert billing.period == "yearly"
    assert billing.since_ms == 1767225600000
    assert billing.until_ms == 1798761600000
    assert billing.billing_basis == "virtual"


def test_summarize_usage_bounds_tracking_session_by_run_lifetime(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    try:
        migrate(conn)
        session_id = create_tracking_session(
            conn,
            "bounded-run",
            started_at_ms=1_000,
        )
        insert_usage_events(
            conn,
            session_id,
            [
                make_usage_event(
                    dedup_suffix="before1",
                    created_ms=999,
                    tokens=TokenBreakdown(input=100),
                ),
                make_usage_event(
                    dedup_suffix="during2",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=10, output=5),
                ),
                make_usage_event(
                    dedup_suffix="after3",
                    created_ms=2_000,
                    tokens=TokenBreakdown(output=77),
                ),
            ],
        )
        end_tracking_session(conn, session_id, ended_at_ms=1_500)

        report = summarize_usage(
            conn,
            UsageReportFilter(tracking_session_id=session_id),
        )
    finally:
        conn.close()

    assert report.totals.tokens.input == 10
    assert report.totals.tokens.output == 5
    assert report.totals.tokens.total == 15
    assert report.filters.since_ms == 1_000
    assert report.filters.until_ms == 1_500


def test_summarize_usage_explicit_bounds_cannot_widen_tracking_session(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    try:
        migrate(conn)
        session_id = create_tracking_session(
            conn,
            "bounded-run",
            started_at_ms=1_000,
        )
        end_tracking_session(conn, session_id, ended_at_ms=2_000)
        insert_usage_events(
            conn,
            session_id,
            [
                make_usage_event(
                    dedup_suffix="before1",
                    created_ms=500,
                    tokens=TokenBreakdown(input=100),
                ),
                make_usage_event(
                    dedup_suffix="during2",
                    created_ms=1_500,
                    tokens=TokenBreakdown(output=20),
                ),
                make_usage_event(
                    dedup_suffix="after3",
                    created_ms=2_500,
                    tokens=TokenBreakdown(reasoning=30),
                ),
            ],
        )

        report = summarize_usage(
            conn,
            UsageReportFilter(
                tracking_session_id=session_id,
                since_ms=0,
                until_ms=9_999,
            ),
        )
    finally:
        conn.close()

    assert report.totals.tokens.output == 20
    assert report.totals.tokens.total == 20
    assert report.filters.since_ms == 1_000
    assert report.filters.until_ms == 2_000


def test_summarize_usage_series_bounds_tracking_session_by_run_lifetime(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "toktrail.db")
    try:
        migrate(conn)
        session_id = create_tracking_session(
            conn,
            "bounded-series",
            started_at_ms=1_000,
        )
        insert_usage_events(
            conn,
            session_id,
            [
                make_usage_event(
                    dedup_suffix="before1",
                    created_ms=999,
                    tokens=TokenBreakdown(input=100),
                ),
                make_usage_event(
                    dedup_suffix="during2",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=7, output=3),
                ),
                make_usage_event(
                    dedup_suffix="after3",
                    created_ms=2_000,
                    tokens=TokenBreakdown(output=10),
                ),
            ],
        )
        end_tracking_session(conn, session_id, ended_at_ms=1_500)

        report = summarize_usage_series(
            conn,
            UsageSeriesFilter(
                granularity="daily",
                tracking_session_id=session_id,
            ),
        )
    finally:
        conn.close()

    assert report.totals.tokens.input == 7
    assert report.totals.tokens.output == 3
    assert report.totals.tokens.total == 10
    assert report.filters["since_ms"] == 1_000
    assert report.filters["until_ms"] == 1_500
