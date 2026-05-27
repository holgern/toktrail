"""Orchestration service for insights report generation.

Combines extraction, aggregation, temporal comparison, anomaly
detection, and suggestion rules into a single InsightsReport.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from toktrail.insights.extractors import (
    aggregate_sessions,
    extract_session_metas,
)
from toktrail.insights.models import (
    DeterministicSuggestion,
    InsightAggregate,
    InsightDelta,
    InsightSessionMeta,
    InsightsReport,
)
from toktrail.insights.suggestions import generate_suggestions
from toktrail.insights.temporal import (
    compute_deltas,
    detect_anomalies,
    resolve_previous_period,
)
from toktrail.periods import resolve_time_range


def _period_label(
    *,
    period: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
) -> str:
    """Human-readable period label for the report."""
    if period:
        return period.replace("-", " ")
    parts: list[str] = []
    if since_ms is not None:
        parts.append(f"since {since_ms}")
    if until_ms is not None:
        parts.append(f"until {until_ms}")
    if parts:
        return ", ".join(parts)
    return "all time"


def insights_report(
    *,
    db_path: Path | None = None,
    config_path: Path | None = None,
    period: str | None = None,
    timezone_name: str | None = None,
    utc: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    machine_id: str | None = None,
    harnesses: tuple[str, ...] = (),
    provider_id: str | None = None,
    model_id: str | None = None,
    agent: str | None = None,
    split_thinking: bool = False,
    refresh: bool = True,
) -> InsightsReport:
    """Generate a full insights report from existing toktrail state.

    This is the primary API entry point.  It extracts session data,
    aggregates, computes temporal deltas, detects anomalies, and
    assembles deterministic suggestions.
    """
    # Resolve period to since/until
    effective_since = since_ms
    effective_until = until_ms

    if period is not None:
        resolved = resolve_time_range(
            period=period,
            timezone_name=timezone_name,
            utc=utc,
        )
        effective_since = resolved.since_ms
        effective_until = resolved.until_ms

    # Extract current period sessions
    # Use the first harness if specified, otherwise None
    harness = harnesses[0] if harnesses else None

    current_metas = extract_session_metas(
        db_path=db_path,
        config_path=config_path,
        since_ms=effective_since,
        until_ms=effective_until,
        machine_id=machine_id,
        harness=harness,
        provider_id=provider_id,
        model_id=model_id,
        agent=agent,
        area=area,
        area_leaf=area_leaf,
        area_exact=area_exact,
        unassigned_area=unassigned_area,
        split_thinking=split_thinking,
    )

    # Filter by multiple harnesses if specified
    if harnesses:
        current_metas = tuple(s for s in current_metas if s.harness in harnesses)

    # Aggregate current period
    current = aggregate_sessions(current_metas)

    # Resolve previous period and compute deltas
    previous: InsightAggregate | None = None
    deltas: tuple[InsightDelta, ...] = ()

    prev_since, prev_until = resolve_previous_period(effective_since, effective_until)
    if prev_since is not None and prev_until is not None:
        prev_metas = extract_session_metas(
            db_path=db_path,
            config_path=config_path,
            since_ms=prev_since,
            until_ms=prev_until,
            machine_id=machine_id,
            harness=harness,
            provider_id=provider_id,
            model_id=model_id,
            agent=agent,
            area=area,
            area_leaf=area_leaf,
            area_exact=area_exact,
            unassigned_area=unassigned_area,
            split_thinking=split_thinking,
        )
        if harnesses:
            prev_metas = tuple(s for s in prev_metas if s.harness in harnesses)
        previous = aggregate_sessions(prev_metas)
        deltas = compute_deltas(current, previous)

    # Detect anomalies
    anomalies = detect_anomalies(current, current_metas)

    # Generate suggestions
    suggestion_dicts = generate_suggestions(current, previous, current_metas)
    suggestions = tuple(
        DeterministicSuggestion(
            kind=str(s["kind"]),
            severity=cast(Literal["info", "warning", "critical"], s["severity"]),
            title=str(s["title"]),
            detail=str(s["detail"]),
            command=(None if s.get("command") is None else str(s.get("command"))),
        )
        for s in suggestion_dicts
    )

    # Sessions to inspect: top 10 by cost, prioritizing failures and anomalies
    sessions_to_inspect = _pick_sessions_to_inspect(current_metas)

    # Build filters dict
    filters: dict[str, object] = {}
    if period:
        filters["period"] = period
    if effective_since is not None:
        filters["since_ms"] = effective_since
    if effective_until is not None:
        filters["until_ms"] = effective_until
    if area:
        filters["area"] = area
    if area_leaf:
        filters["area_leaf"] = area_leaf
    if machine_id:
        filters["machine_id"] = machine_id
    if harnesses:
        filters["harnesses"] = list(harnesses)
    if provider_id:
        filters["provider_id"] = provider_id
    if model_id:
        filters["model_id"] = model_id

    label = _period_label(
        period=period, since_ms=effective_since, until_ms=effective_until
    )

    return InsightsReport(
        filters=filters,
        period_label=label,
        current=current,
        previous=previous,
        deltas=deltas,
        anomalies=anomalies,
        suggestions=suggestions,
        sessions_to_inspect=sessions_to_inspect,
    )


def _pick_sessions_to_inspect(
    sessions: tuple[InsightSessionMeta, ...],
    limit: int = 10,
) -> tuple[InsightSessionMeta, ...]:
    """Select sessions to inspect, prioritizing anomalies.

    Sorts by virtual cost descending, then boosts sessions with
    failures or unpriced counts.
    """
    if not sessions:
        return ()

    def _priority(s: InsightSessionMeta) -> tuple[int, Decimal]:
        """Higher priority = more interesting to inspect."""
        anomaly_flags = (1 if s.tool_failure_count > 0 else 0) + (
            1 if s.unpriced_count > 0 else 0
        )
        return (-anomaly_flags, -s.virtual_cost)

    sorted_sessions = sorted(sessions, key=_priority)
    return tuple(sorted_sessions[:limit])
