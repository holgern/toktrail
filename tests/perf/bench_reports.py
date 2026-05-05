from __future__ import annotations

import tempfile
import time
from decimal import Decimal
from pathlib import Path

from toktrail.config import (
    CostingConfig,
    Price,
    SubscriptionConfig,
    SubscriptionWindowConfig,
)
from toktrail.db import (
    connect,
    insert_usage_events,
    migrate,
    summarize_subscription_usage,
    summarize_usage,
    summarize_usage_series,
    summarize_usage_sessions,
)
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import UsageReportFilter, UsageSeriesFilter, UsageSessionsFilter


def _event(index: int) -> UsageEvent:
    provider = "openai" if index % 2 == 0 else "anthropic"
    model = "gpt-5.4" if provider == "openai" else "claude-sonnet-4"
    created_ms = 1_700_000_000_000 + (index * 10_000)
    return UsageEvent(
        harness="codex" if index % 3 == 0 else "opencode",
        source_session_id=f"session-{index % 2000:04d}",
        source_row_id=f"row-{index}",
        source_message_id=f"msg-{index}",
        source_dedup_key=f"dedup-{index}",
        global_dedup_key=f"bench:{index}",
        fingerprint_hash=f"fp-{index}",
        provider_id=provider,
        model_id=model,
        thinking_level=None,
        agent="default",
        created_ms=created_ms,
        completed_ms=created_ms + 1000,
        tokens=TokenBreakdown(
            input=1200 + (index % 50),
            output=400 + (index % 30),
            reasoning=100 + (index % 20),
            cache_read=300 + (index % 40),
            cache_write=50 + (index % 10),
            cache_output=25 + (index % 5),
        ),
        source_cost_usd=Decimal("0"),
        raw_json=None,
    )


def _costing_config() -> CostingConfig:
    virtual_prices = (
        Price(
            provider="openai",
            model="gpt-5.4",
            aliases=(),
            input_usd_per_1m=5.0,
            cached_input_usd_per_1m=0.5,
            output_usd_per_1m=20.0,
        ),
        Price(
            provider="anthropic",
            model="claude-sonnet-4",
            aliases=(),
            input_usd_per_1m=3.0,
            cached_input_usd_per_1m=0.3,
            output_usd_per_1m=15.0,
        ),
    )
    return CostingConfig(
        default_actual_mode="zero",
        default_virtual_mode="pricing",
        virtual_prices=virtual_prices,
        subscriptions=(
            SubscriptionConfig(
                id="openai-plan",
                usage_providers=("openai",),
                quota_cost_basis="virtual",
                windows=(
                    SubscriptionWindowConfig(
                        period="5h",
                        limit_usd=50,
                        reset_at="2026-01-01T00:00:00+00:00",
                    ),
                    SubscriptionWindowConfig(
                        period="weekly",
                        limit_usd=250,
                        reset_at="2026-01-01T00:00:00+00:00",
                    ),
                    SubscriptionWindowConfig(
                        period="monthly",
                        limit_usd=800,
                        reset_at="2026-01-01T00:00:00+00:00",
                    ),
                ),
            )
        ),
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="toktrail-bench-") as tmp:
        db_path = Path(tmp) / "bench.db"
        conn = connect(db_path)
        migrate(conn)

        events = [_event(index) for index in range(100_000)]
        insert_usage_events(conn, None, events)
        config = _costing_config()

        started = time.perf_counter()
        summarize_usage(conn, UsageReportFilter(), costing_config=config)
        summary_s = time.perf_counter() - started

        started = time.perf_counter()
        summarize_usage_series(
            conn,
            UsageSeriesFilter(granularity="daily"),
            costing_config=config,
        )
        daily_s = time.perf_counter() - started

        started = time.perf_counter()
        summarize_usage_sessions(
            conn,
            UsageSessionsFilter(limit=10, order="desc"),
            costing_config=config,
        )
        sessions_s = time.perf_counter() - started

        started = time.perf_counter()
        summarize_subscription_usage(conn, config, now_ms=1_778_000_000_000)
        subscriptions_s = time.perf_counter() - started

        print(f"usage summary: {summary_s:.3f}s")
        print(f"usage daily: {daily_s:.3f}s")
        print(f"usage sessions limit=10: {sessions_s:.3f}s")
        print(f"subscriptions: {subscriptions_s:.3f}s")

        conn.close()


if __name__ == "__main__":
    main()
