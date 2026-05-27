"""Aggregate insights session data by dimensions and time periods.

Provides grouping utilities beyond what extractors.aggregate_sessions
covers, including period-aware grouping for temporal comparison.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from toktrail.insights.models import (
    InsightAggregate,
    InsightGroupRow,
    InsightSessionMeta,
)


def aggregate_sessions(
    sessions: tuple[InsightSessionMeta, ...],
) -> InsightAggregate:
    """Aggregate per-session metas into group totals and per-dimension rows.

    Re-exports the canonical aggregator from extractors for convenience.
    """
    from toktrail.insights.extractors import (
        aggregate_sessions as _aggregate_sessions,
    )

    return _aggregate_sessions(sessions)


def group_sessions_by_period(
    sessions: tuple[InsightSessionMeta, ...],
    *,
    period_ms: int,
) -> dict[str, tuple[InsightSessionMeta, ...]]:
    """Group sessions into time buckets of ``period_ms`` duration.

    Keys are ISO date strings (YYYY-MM-DD for daily buckets,
    YYYY-Www for weekly, YYYY-MM for monthly).
    Returns groups sorted by key.
    """
    if not sessions:
        return {}

    # Determine bucket boundaries from session timestamps
    groups: dict[str, list[InsightSessionMeta]] = defaultdict(list)
    for s in sessions:
        ts = s.start_ms
        if ts is None:
            # Sessions without timestamps go in "unknown" bucket
            groups["unknown"].append(s)
            continue
        # Convert ms to approximate bucket key
        bucket_start_ms = (ts // period_ms) * period_ms
        # Use simple date-based key
        bucket_key = str(bucket_start_ms)
        groups[bucket_key].append(s)

    return {k: tuple(v) for k, v in sorted(groups.items())}


def compute_group_row(
    key: str,
    label: str,
    sessions: tuple[InsightSessionMeta, ...],
) -> InsightGroupRow:
    """Compute an InsightGroupRow from a set of sessions."""
    return InsightGroupRow(
        key=key,
        label=label,
        session_count=len(sessions),
        message_count=sum(s.user_messages + s.assistant_messages for s in sessions),
        total_tokens=sum(s.total_tokens for s in sessions),
        input_tokens=sum(s.input_tokens for s in sessions),
        output_tokens=sum(s.output_tokens for s in sessions),
        reasoning_tokens=sum(s.reasoning_tokens for s in sessions),
        cache_read_tokens=sum(s.cache_read_tokens for s in sessions),
        cache_write_tokens=sum(s.cache_write_tokens for s in sessions),
        cache_output_tokens=sum(s.cache_output_tokens for s in sessions),
        actual_cost=sum((s.actual_cost for s in sessions), Decimal(0)),
        virtual_cost=sum((s.virtual_cost for s in sessions), Decimal(0)),
        source_cost=sum((s.source_cost for s in sessions), Decimal(0)),
        unpriced_count=sum(s.unpriced_count for s in sessions),
        tool_call_count=sum(s.tool_call_count for s in sessions),
        tool_failure_count=sum(s.tool_failure_count for s in sessions),
    )
