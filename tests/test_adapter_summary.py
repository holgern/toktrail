from __future__ import annotations

from decimal import Decimal

import pytest

from toktrail.adapters.summary import (
    add_tokens,
    summarize_event_totals,
    summarize_events_by_activity,
    summarize_events_by_model,
    summarize_events_by_source_session,
)
from toktrail.models import TokenBreakdown, UsageEvent


def test_add_tokens_sums_all_token_fields() -> None:
    left = TokenBreakdown(input=1, output=2, reasoning=3, cache_read=4, cache_write=5)
    right = TokenBreakdown(
        input=10,
        output=20,
        reasoning=30,
        cache_read=40,
        cache_write=50,
    )

    combined = add_tokens(left, right)

    assert combined == TokenBreakdown(
        input=11,
        output=22,
        reasoning=33,
        cache_read=44,
        cache_write=55,
    )


def test_summary_helpers_aggregate_events_consistently() -> None:
    events = [
        _event(
            source_session_id="ses-1",
            provider_id="anthropic",
            model_id="claude-sonnet-4",
            agent="plan",
            created_ms=2000,
            tokens=TokenBreakdown(input=10, output=2, cache_read=5),
            source_cost_usd=0.1,
        ),
        _event(
            source_session_id="ses-1",
            provider_id="anthropic",
            model_id="claude-sonnet-4",
            agent=None,
            created_ms=3000,
            tokens=TokenBreakdown(output=3, cache_write=7),
            source_cost_usd=0.2,
        ),
        _event(
            source_session_id="ses-2",
            provider_id="openai",
            model_id="gpt-5",
            agent="plan",
            created_ms=1000,
            tokens=TokenBreakdown(input=4, reasoning=6),
            source_cost_usd=0.3,
        ),
    ]

    totals = summarize_event_totals(events)
    by_source_session = summarize_events_by_source_session(
        "pi",
        events,
        source_paths_by_session={"ses-1": ["/tmp/a.jsonl", "/tmp/b.jsonl"]},
    )
    by_model = summarize_events_by_model(events)
    by_agent = summarize_events_by_activity(events)

    assert totals.tokens.total == 37
    assert float(totals.source_cost_usd) == pytest.approx(0.6)
    assert totals.actual_cost_usd == 0.0
    assert totals.virtual_cost_usd == 0.0
    assert totals.unpriced_count == 2

    assert [summary.source_session_id for summary in by_source_session] == [
        "ses-1",
        "ses-2",
    ]
    assert by_source_session[0].assistant_message_count == 2
    assert by_source_session[0].tokens.total == 27
    assert float(by_source_session[0].source_cost_usd) == pytest.approx(0.3)
    assert by_source_session[0].actual_cost_usd == 0.0
    assert by_source_session[0].models == ("claude-sonnet-4",)
    assert by_source_session[0].providers == ("anthropic",)
    assert by_source_session[0].source_paths == ("/tmp/a.jsonl", "/tmp/b.jsonl")

    assert [(row.provider_id, row.model_id, row.total_tokens) for row in by_model] == [
        ("anthropic", "claude-sonnet-4", 27),
        ("openai", "gpt-5", 10),
    ]
    assert float(by_model[0].source_cost_usd) == pytest.approx(0.3)
    assert by_model[0].actual_cost_usd == 0.0
    assert [(row.agent, row.total_tokens) for row in by_agent] == [
        ("plan", 27),
        ("unknown", 10),
    ]
    assert float(by_agent[0].source_cost_usd) == pytest.approx(0.4)
    assert by_agent[0].actual_cost_usd == 0.0


def test_summarize_events_by_model_can_collapse_thinking_levels() -> None:
    events = [
        _event(
            source_session_id="ses-1",
            provider_id="openai",
            model_id="gpt-5.4",
            thinking_level="high",
            agent="plan",
            created_ms=1000,
            tokens=TokenBreakdown(input=10, output=2),
            source_cost_usd=0.1,
        ),
        _event(
            source_session_id="ses-1",
            provider_id="openai",
            model_id="gpt-5.4",
            thinking_level="low",
            agent="plan",
            created_ms=2000,
            tokens=TokenBreakdown(input=4, reasoning=6),
            source_cost_usd=0.2,
        ),
    ]

    split_rows = summarize_events_by_model(events)
    collapsed_rows = summarize_events_by_model(events, split_thinking=False)

    assert [(row.thinking_level, row.total_tokens) for row in split_rows] == [
        ("high", 12),
        ("low", 10),
    ]
    assert [(row.thinking_level, row.total_tokens) for row in collapsed_rows] == [
        (None, 22)
    ]


def _event(
    *,
    source_session_id: str,
    provider_id: str,
    model_id: str,
    thinking_level: str | None = None,
    agent: str | None,
    created_ms: int,
    tokens: TokenBreakdown,
    source_cost_usd: float | Decimal,
) -> UsageEvent:
    dedup_key = f"{source_session_id}:{created_ms}:{model_id}"
    return UsageEvent(
        harness="pi",
        source_session_id=source_session_id,
        source_row_id=dedup_key,
        source_message_id=dedup_key,
        source_dedup_key=dedup_key,
        global_dedup_key=f"pi:{dedup_key}",
        fingerprint_hash=dedup_key,
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=thinking_level,
        agent=agent,
        created_ms=created_ms,
        completed_ms=None,
        tokens=tokens,
        source_cost_usd=Decimal(str(source_cost_usd))
        if not isinstance(source_cost_usd, Decimal)
        else source_cost_usd,
        raw_json=None,
    )
