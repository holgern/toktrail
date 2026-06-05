from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

from toktrail import db as db_module
from toktrail.api._common import _load_costing_config, _open_state_db
from toktrail.api._conversions import (
    _to_public_report,
    _to_public_series_report,
    _to_public_subscription_report,
    _to_public_usage_areas_report,
    _to_public_usage_session_row,
    _to_public_usage_sessions_report,
)
from toktrail.api.models import (
    RunReport,
    StatsReport,
    SubscriptionUsageReport,
    ToolUsageReport,
    ToolUsageRow,
    UsageAreasReport,
    UsageSeriesReport,
    UsageSessionRow,
    UsageSessionsReport,
)
from toktrail.config import CostingConfig, load_resolved_costing_config
from toktrail.errors import (
    AmbiguousSourceSessionError,
    InvalidAPIUsageError,
    NoActiveRunError,
    RunNotFoundError,
    StateDatabaseError,
)
from toktrail.insights.models import InsightSessionMeta
from toktrail.paths import resolve_toktrail_config_path
from toktrail.periods import resolve_time_range
from toktrail.reporting import UsageReportFilter, UsageSessionsFilter
from toktrail.reporting import UsageSessionRow as InternalUsageSessionRow


def _resolve_area_filter_inputs(
    *,
    area: str | None,
    area_leaf: str | None,
    unassigned_area: bool,
) -> tuple[str | None, str]:
    if (
        sum(
            bool(value)
            for value in (area is not None, area_leaf is not None, unassigned_area)
        )
        > 1
    ):
        msg = "Use only one of area, area_leaf, or unassigned_area."
        raise InvalidAPIUsageError(msg)
    if area_leaf is not None:
        return area_leaf, "leaf"
    return area, "auto"


def _parse_session_key(session_key: str) -> tuple[str, str, str]:
    machine_selector, separator, remainder = session_key.partition("/")
    harness, separator_two, source_session_id = remainder.partition("/")
    if not all(
        (
            machine_selector,
            separator,
            harness,
            separator_two,
            source_session_id,
        )
    ):
        msg = "Session key must use machine/harness/source_session_id."
        raise InvalidAPIUsageError(msg)
    return machine_selector, harness, source_session_id


def _resolve_usage_session_row(
    conn: sqlite3.Connection,
    *,
    costing_config: CostingConfig,
    session_key: str | None = None,
    machine_id: str | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    last: bool = False,
) -> InternalUsageSessionRow:
    resolved_machine_id = machine_id
    resolved_harness = harness
    resolved_source_session_id = source_session_id
    if session_key is not None:
        if (
            any(value is not None for value in (machine_id, harness, source_session_id))
            or last
        ):
            msg = (
                "session_key cannot be combined with machine_id, harness, "
                "source_session_id, or last."
            )
            raise InvalidAPIUsageError(msg)
        (
            machine_selector,
            resolved_harness,
            resolved_source_session_id,
        ) = _parse_session_key(session_key)
        try:
            resolved_machine_id = db_module.resolve_machine_selector(
                conn, machine_selector
            ).machine_id
        except ValueError as exc:
            raise StateDatabaseError(f"Session not found: {session_key}") from exc

    report = db_module.summarize_usage_sessions(
        conn,
        UsageSessionsFilter(
            machine_id=resolved_machine_id,
            harness=resolved_harness,
            source_session_id=resolved_source_session_id,
            limit=1 if last else None,
            order="desc",
        ),
        costing_config=costing_config,
    )

    if session_key is not None:
        if not report.sessions:
            raise StateDatabaseError(f"Session not found: {session_key}")
        return cast(InternalUsageSessionRow, report.sessions[0])

    if resolved_source_session_id is not None:
        if not report.sessions:
            if resolved_harness is not None:
                msg = (
                    f"Source session not found for harness {resolved_harness}: "
                    f"{resolved_source_session_id}"
                )
            else:
                msg = f"Source session not found: {resolved_source_session_id}"
            raise StateDatabaseError(msg)
        if len(report.sessions) == 1:
            return cast(InternalUsageSessionRow, report.sessions[0])
        candidates = ", ".join(row.key for row in report.sessions[:10])
        msg = (
            f"Multiple source sessions found for {resolved_source_session_id}: "
            f"{candidates}. Use a session key."
        )
        raise AmbiguousSourceSessionError(msg)

    if last:
        if not report.sessions:
            if resolved_harness is not None:
                msg = f"No usage events found for harness {resolved_harness}."
            else:
                msg = "No sessions found."
            raise StateDatabaseError(msg)
        return cast(InternalUsageSessionRow, report.sessions[0])

    if len(report.sessions) == 1:
        return cast(InternalUsageSessionRow, report.sessions[0])
    if not report.sessions:
        if resolved_harness is not None:
            msg = f"No usage events found for harness {resolved_harness}."
        else:
            msg = "No sessions found."
        raise StateDatabaseError(msg)
    candidates = ", ".join(row.key for row in report.sessions[:10])
    if resolved_harness is not None:
        msg = (
            f"Multiple source sessions found for harness {resolved_harness}: "
            f"{candidates}. Provide source_session_id, use last=True, or use "
            "a session key."
        )
    else:
        msg = (
            f"Multiple sessions found: {candidates}. "
            "Provide a session key or use last=True."
        )
    raise AmbiguousSourceSessionError(msg)


def get_usage_session(
    db_path: Path | None,
    *,
    session_key: str,
    config_path: Path | None = None,
) -> UsageSessionRow:
    conn, _ = _open_state_db(db_path)
    try:
        row = _resolve_usage_session_row(
            conn,
            costing_config=_load_costing_config(config_path),
            session_key=session_key,
        )
    finally:
        conn.close()
    return _to_public_usage_session_row(row)


def session_report(
    db_path: Path | None,
    session_id: int | None = None,
    *,
    machine_id: str | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    split_thinking: bool = False,
    config_path: Path | None = None,
) -> RunReport:
    conn, _ = _open_state_db(db_path)
    try:
        selected_session_id = session_id
        if selected_session_id is None:
            active = db_module.get_active_tracking_session(conn)
            if active is None:
                raise NoActiveRunError("An active run is required, but none exists.")
            selected_session_id = active
        if db_module.get_tracking_session(conn, selected_session_id) is None:
            msg = f"Run not found: {selected_session_id}"
            raise RunNotFoundError(msg)
        report = db_module.summarize_usage(
            conn,
            UsageReportFilter(
                tracking_session_id=selected_session_id,
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                since_ms=since_ms,
                until_ms=until_ms,
                split_thinking=split_thinking,
            ),
            costing_config=_load_costing_config(config_path),
        )
    except ValueError as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()
    return _to_public_report(report)


run_report = session_report


def usage_report(
    db_path: Path | None,
    *,
    session_id: int | None = None,
    period: str | None = None,
    timezone: str | None = None,
    utc: bool = False,
    machine_id: str | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    split_thinking: bool = False,
    config_path: Path | None = None,
) -> RunReport:
    if period is not None and (since_ms is not None or until_ms is not None):
        msg = "usage_report() accepts either period or since/until filters, not both."
        raise InvalidAPIUsageError(msg)

    try:
        resolved_range = resolve_time_range(
            period=period,
            timezone_name=timezone,
            utc=utc,
        )
    except ValueError as exc:
        raise InvalidAPIUsageError(str(exc)) from exc
    effective_since_ms = resolved_range.since_ms if period is not None else since_ms
    effective_until_ms = resolved_range.until_ms if period is not None else until_ms
    resolved_area, area_match = _resolve_area_filter_inputs(
        area=area,
        area_leaf=area_leaf,
        unassigned_area=unassigned_area,
    )

    conn, _ = _open_state_db(db_path)
    try:
        report = db_module.summarize_usage(
            conn,
            UsageReportFilter(
                tracking_session_id=session_id,
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=resolved_area,
                area_match=area_match,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=effective_since_ms,
                until_ms=effective_until_ms,
                split_thinking=split_thinking,
            ),
            costing_config=_load_costing_config(config_path),
        )
    except ValueError as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()

    public_report = _to_public_report(report)
    filters = dict(public_report.filters)
    if effective_since_ms is not None:
        existing_since = filters.get("since_ms")
        if isinstance(existing_since, int):
            filters["since_ms"] = max(existing_since, effective_since_ms)
        else:
            filters["since_ms"] = effective_since_ms
    if effective_until_ms is not None:
        existing_until = filters.get("until_ms")
        if isinstance(existing_until, int):
            filters["until_ms"] = min(existing_until, effective_until_ms)
        else:
            filters["until_ms"] = effective_until_ms
    if period is not None:
        filters["period"] = resolved_range.period
    if period is not None or timezone is not None or utc:
        filters["timezone"] = resolved_range.timezone
    return replace(public_report, filters=filters)


def _compute_distributions(
    session_metas: tuple[InsightSessionMeta, ...],
) -> dict[str, object]:
    """Compute duration and message count distributions."""
    durations = [m.duration_ms for m in session_metas if m.duration_ms is not None]
    user_msgs = [m.user_messages for m in session_metas]
    total_msgs = [m.user_messages + m.assistant_messages for m in session_metas]

    def _bucket_ms(ms: int) -> str:
        minutes = ms / 60_000
        if minutes < 1:
            return "<1m"
        if minutes < 5:
            return "1-5m"
        if minutes < 15:
            return "5-15m"
        if minutes < 30:
            return "15-30m"
        if minutes < 60:
            return "30-60m"
        if minutes < 120:
            return "1-2h"
        return ">2h"

    def _bucket_count(n: int) -> str:
        if n == 0:
            return "0"
        if n <= 3:
            return "1-3"
        if n <= 10:
            return "4-10"
        if n <= 30:
            return "11-30"
        if n <= 100:
            return "31-100"
        return ">100"

    def _histogram(
        values: list[int], bucket_fn: Callable[[int], str]
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for v in values:
            b = bucket_fn(v)
            counts[b] = counts.get(b, 0) + 1
        return counts

    duration_hist = _histogram(durations, _bucket_ms)
    user_msg_hist = _histogram(user_msgs, _bucket_count)
    total_msg_hist = _histogram(total_msgs, _bucket_count)

    result: dict[str, object] = {}
    if durations:
        result["duration_ms"] = {
            "median": sorted(durations)[len(durations) // 2],
            "p90": sorted(durations)[int(len(durations) * 0.9)]
            if len(durations) >= 5
            else sorted(durations)[-1],
            "histogram": duration_hist,
        }
    result["user_messages"] = {
        "median": sorted(user_msgs)[len(user_msgs) // 2] if user_msgs else 0,
        "histogram": user_msg_hist,
    }
    result["total_messages"] = {
        "median": sorted(total_msgs)[len(total_msgs) // 2] if total_msgs else 0,
        "histogram": total_msg_hist,
    }
    return result


def _compute_archetypes(
    session_metas: tuple[InsightSessionMeta, ...],
) -> dict[str, object]:
    """Classify sessions into archetypes based on duration and message counts."""
    counts: dict[str, int] = {
        "automation": 0,
        "quick": 0,
        "standard": 0,
        "deep": 0,
        "marathon": 0,
    }
    for m in session_metas:
        duration_m = (m.duration_ms or 0) / 60_000
        msgs = m.user_messages + m.assistant_messages
        if duration_m < 1 and msgs <= 2:
            counts["automation"] += 1
        elif duration_m < 5 and msgs <= 5:
            counts["quick"] += 1
        elif duration_m < 30 and msgs <= 30:
            counts["standard"] += 1
        elif duration_m < 120:
            counts["deep"] += 1
        else:
            counts["marathon"] += 1
    total = len(session_metas) or 1
    fractions = {k: round(v / total, 3) for k, v in counts.items()}
    return {"counts": counts, "fractions": fractions}


def _compute_health_aggregates(
    db_path: Path | None,
    session_metas: tuple[InsightSessionMeta, ...],
) -> dict[str, object]:
    """Compute aggregate health stats from persisted digests."""
    if not session_metas:
        return {}
    from toktrail.db import connect as connect_db
    from toktrail.db import list_source_session_digests

    conn = connect_db(db_path)
    try:
        all_digests = list_source_session_digests(conn)
    finally:
        conn.close()
    digest_lookup = {
        (d.origin_machine_id or "", d.harness, d.source_session_id): d
        for d in all_digests
    }
    scores: list[int] = []
    grades: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    total_failures = 0
    total_tool_calls = 0
    for m in session_metas:
        key = (m.origin_machine_id or "", m.harness, m.source_session_id)
        digest = digest_lookup.get(key)
        if digest is None or digest.health is None:
            continue
        h = digest.health
        if h.score is not None:
            scores.append(h.score)
        if h.grade:
            grades[h.grade] = grades.get(h.grade, 0) + 1
        outcomes[h.outcome] = outcomes.get(h.outcome, 0) + 1
        total_failures += digest.tool_health.tool_failure_count
        total_tool_calls += digest.tool_health.tool_call_count
    result: dict[str, object] = {
        "sessions_with_health": len(scores) + sum(1 for _ in grades.values()),
        "outcomes": outcomes,
        "grade_distribution": grades,
        "failure_rate": (round(total_failures / max(total_tool_calls, 1), 4)),
    }
    if scores:
        result["average_score"] = round(sum(scores) / len(scores), 1)
    return result


def _compute_area_mix(
    session_metas: tuple[InsightSessionMeta, ...],
) -> tuple[dict[str, object], ...]:
    """Compute area breakdown from session metas."""
    from collections import defaultdict

    area_data: dict[str, dict[str, int | Decimal]] = defaultdict(
        lambda: {"sessions": 0, "messages": 0, "tokens": 0, "cost": Decimal(0)}
    )
    for m in session_metas:
        area = m.area_path or "(unassigned)"
        area_data[area]["sessions"] += 1
        area_data[area]["messages"] += m.user_messages + m.assistant_messages
        area_data[area]["tokens"] += m.total_tokens
        area_data[area]["cost"] += m.actual_cost
    rows: list[dict[str, object]] = []
    for area, data in sorted(area_data.items()):
        rows.append(
            {
                "area": area,
                "sessions": data["sessions"],
                "messages": data["messages"],
                "tokens": data["tokens"],
                "cost": str(data["cost"]),
            }
        )
    return tuple(rows)
def _tools_for_stats(
    db_path: Path | None,
    *,
    period: str | None = None,
    timezone: str | None = None,
    utc: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    config_path: Path | None = None,
) -> tuple[dict[str, object], ...]:
    try:
        report = tool_usage_report(
            db_path,
            period=period,
            timezone=timezone,
            utc=utc,
            since_ms=since_ms,
            until_ms=until_ms,
            config_path=config_path,
            limit=10,
        )
        return tuple(row.as_dict() for row in report.tools)
    except Exception:
        return ()


def _tool_usage_summary(
    db_path: Path | None,
    *,
    period: str | None = None,
    timezone: str | None = None,
    utc: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    config_path: Path | None = None,
) -> dict[str, object]:
    try:
        report = tool_usage_report(
            db_path,
            period=period,
            timezone=timezone,
            utc=utc,
            since_ms=since_ms,
            until_ms=until_ms,
            config_path=config_path,
            limit=10,
        )
        return {
            "total_tool_calls": report.total_tool_calls,
            "sessions_considered": report.sessions_considered,
            "sessions_with_tool_stats": report.sessions_with_tool_stats,
            "missing_session_count": report.missing_session_count,
        }
    except Exception:
        return {}




def stats_report(
    db_path: Path | None = None,
    *,
    period: str | None = None,
    timezone: str | None = None,
    utc: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    config_path: Path | None = None,
) -> StatsReport:
    import time

    from toktrail.insights.extractors import extract_session_metas

    generated_at_ms = int(time.time() * 1000)

    report = usage_report(
        db_path,
        period=period,
        timezone=timezone,
        utc=utc,
        since_ms=since_ms,
        until_ms=until_ms,
        config_path=config_path,
    )
    tokens = report.totals.tokens
    costs = report.totals.costs
    prompt_like = tokens.input + tokens.cache_read + tokens.cache_write
    output_like = tokens.output + tokens.cache_output + tokens.reasoning
    cache_denominator = max(prompt_like, 1)
    filters = dict(report.filters)

    # Extract session metas for distribution/archetype/health analysis
    session_metas = extract_session_metas(
        db_path=db_path,
        config_path=config_path,
        period=period,
        timezone_name=timezone,
        utc=utc,
        since_ms=since_ms,
        until_ms=until_ms,
    )
    distributions = _compute_distributions(session_metas)
    archetypes = _compute_archetypes(session_metas)
    health = _compute_health_aggregates(db_path, session_metas)
    area_mix = _compute_area_mix(session_metas)

    return StatsReport(
        schema_version=1,
        range={
            "since_ms": filters.get("since_ms"),
            "until_ms": filters.get("until_ms"),
            "timezone": filters.get("timezone"),
        },
        totals={
            "messages": report.totals.message_count,
            "tokens": tokens.as_dict(),
            "prompt_like_tokens": prompt_like,
            "output_like_tokens": output_like,
            "source_usd": format(costs.source_cost_usd, "f"),
            "actual_usd": format(costs.actual_cost_usd, "f"),
            "virtual_usd": format(costs.virtual_cost_usd, "f"),
            "savings_usd": format(costs.savings_usd, "f"),
            "unpriced_count": costs.unpriced_count,
        },
        sessions={
            "message_count": report.totals.message_count,
            "session_count": len(session_metas),
        },
        cache={
            "cache_read_ratio": tokens.cache_read / cache_denominator,
            "cache_write_ratio": tokens.cache_write / cache_denominator,
            "cache_read_tokens": tokens.cache_read,
            "cache_write_tokens": tokens.cache_write,
            "reuse_ratio": (tokens.cache_read / cache_denominator),
        },
        models=tuple(row.as_dict() for row in report.by_model),
        providers=tuple(row.as_dict() for row in report.by_provider),
        harnesses=tuple(row.as_dict() for row in report.by_harness),
        distributions=distributions,
        archetypes=archetypes,
        health=health,
        area_mix=area_mix,
        tools=_tools_for_stats(
            db_path,
            period=period,
            timezone=timezone,
            utc=utc,
            since_ms=since_ms,
            until_ms=until_ms,
            config_path=config_path,
        ),
        tool_usage=_tool_usage_summary(
            db_path,
            period=period,
            timezone=timezone,
            utc=utc,
            since_ms=since_ms,
            until_ms=until_ms,
            config_path=config_path,
        ),
        generated_at_ms=generated_at_ms,
    )


def usage_series_report(
    db_path: Path | None = None,
    *,
    granularity: str = "daily",
    session_id: int | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    timezone: str | None = None,
    utc: bool = False,
    start_of_week: str = "monday",
    machine_id: str | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    instances: bool = False,
    breakdown: bool = False,
    split_thinking: bool = False,
    config_path: Path | None = None,
) -> UsageSeriesReport:
    if granularity not in ("daily", "weekly", "monthly"):
        msg = f"Invalid granularity: {granularity}. Use daily, weekly, or monthly."
        raise InvalidAPIUsageError(msg)

    from toktrail.db import migrate, summarize_usage_series
    from toktrail.periods import _resolve_timezone
    from toktrail.reporting import UsageSeriesFilter

    resolved_area, area_match = _resolve_area_filter_inputs(
        area=area,
        area_leaf=area_leaf,
        unassigned_area=unassigned_area,
    )
    conn, _ = _open_state_db(db_path)
    try:
        migrate(conn)
        costing_config = _load_costing_config(config_path)
        _resolve_timezone(timezone_name=timezone, utc=utc)  # validate timezone
        report = summarize_usage_series(
            conn,
            UsageSeriesFilter(
                granularity=granularity,
                tracking_session_id=session_id,
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                since_ms=since_ms,
                until_ms=until_ms,
                split_thinking=split_thinking,
                area=resolved_area,
                area_match=area_match,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                instances=instances,
                breakdown=breakdown,
                start_of_week=start_of_week,
                locale=None,
                timezone_name=timezone,
                utc=utc,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    return _to_public_series_report(report)


def subscription_usage_report(
    db_path: Path | None = None,
    *,
    provider_id: str | None = None,
    now_ms: int | None = None,
    config_path: Path | None = None,
    prices_path: Path | None = None,
    prices_dir: Path | None = None,
    subscriptions_path: Path | None = None,
) -> SubscriptionUsageReport:
    has_explicit = prices_path or prices_dir or subscriptions_path
    if has_explicit:
        resolved_config = resolve_toktrail_config_path(config_path)
        costing_config = load_resolved_costing_config(
            config_cli_value=resolved_config,
            prices_cli_value=prices_path,
            prices_dir_cli_value=prices_dir,
            subscriptions_cli_value=subscriptions_path,
        ).config
    else:
        costing_config = _load_costing_config(config_path)
    conn, _ = _open_state_db(db_path)
    try:
        report = db_module.summarize_subscription_usage(
            conn,
            costing_config,
            provider_id=provider_id,
            now_ms=now_ms,
        )
    except ValueError as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()

    return _to_public_subscription_report(report)


def usage_sessions_report(
    db_path: Path | None = None,
    *,
    session_id: int | None = None,
    period: str | None = None,
    timezone: str | None = None,
    utc: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    machine_id: str | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    limit: int | None = 10,
    order: str = "desc",
    breakdown: bool = False,
    split_thinking: bool = False,
    config_path: Path | None = None,
) -> UsageSessionsReport:
    from toktrail.db import migrate, summarize_usage_sessions
    from toktrail.reporting import UsageSessionsFilter

    if period is not None and (since_ms is not None or until_ms is not None):
        msg = (
            "usage_sessions_report() accepts either period or since/until "
            "filters, not both."
        )
        raise InvalidAPIUsageError(msg)

    try:
        resolved_range = resolve_time_range(
            period=period,
            timezone_name=timezone,
            utc=utc,
        )
    except ValueError as exc:
        raise InvalidAPIUsageError(str(exc)) from exc
    effective_since_ms = resolved_range.since_ms if period is not None else since_ms
    effective_until_ms = resolved_range.until_ms if period is not None else until_ms

    if order not in ("asc", "desc"):
        msg = f"Invalid order: {order!r}. Use asc or desc."
        raise InvalidAPIUsageError(msg)
    if limit is not None and limit < 0:
        msg = f"Invalid limit: {limit}. Must be non-negative."
        raise InvalidAPIUsageError(msg)
    resolved_area, area_match = _resolve_area_filter_inputs(
        area=area,
        area_leaf=area_leaf,
        unassigned_area=unassigned_area,
    )

    conn, _ = _open_state_db(db_path)
    try:
        migrate(conn)
        costing_config = _load_costing_config(config_path)
        report = summarize_usage_sessions(
            conn,
            UsageSessionsFilter(
                tracking_session_id=session_id,
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=resolved_area,
                area_match=area_match,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=effective_since_ms,
                until_ms=effective_until_ms,
                split_thinking=split_thinking,
                limit=limit,
                order=order,
                breakdown=breakdown,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    public_report = _to_public_usage_sessions_report(report)
    filters = dict(public_report.filters)
    if effective_since_ms is not None:
        existing_since = filters.get("since_ms")
        if isinstance(existing_since, int):
            filters["since_ms"] = max(existing_since, effective_since_ms)
        else:
            filters["since_ms"] = effective_since_ms
    if effective_until_ms is not None:
        existing_until = filters.get("until_ms")
        if isinstance(existing_until, int):
            filters["until_ms"] = min(existing_until, effective_until_ms)
        else:
            filters["until_ms"] = effective_until_ms
    if period is not None:
        filters["period"] = resolved_range.period
    if period is not None or timezone is not None or utc:
        filters["timezone"] = resolved_range.timezone
    return replace(public_report, filters=filters)


def usage_runs_report(
    db_path: Path | None = None,
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
    machine_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    limit: int | None = 10,
    order: str = "desc",
    split_thinking: bool = False,
    include_archived: bool = False,
    archived_only: bool = False,
    config_path: Path | None = None,
) -> object:
    from toktrail.db import migrate, summarize_usage_runs
    from toktrail.reporting import UsageRunsFilter

    if order not in ("asc", "desc"):
        msg = f"Invalid order: {order!r}. Use asc or desc."
        raise InvalidAPIUsageError(msg)
    if limit is not None and limit < 0:
        msg = f"Invalid limit: {limit}. Must be non-negative."
        raise InvalidAPIUsageError(msg)
    resolved_area, area_match = _resolve_area_filter_inputs(
        area=area,
        area_leaf=area_leaf,
        unassigned_area=unassigned_area,
    )

    conn, _ = _open_state_db(db_path)
    try:
        migrate(conn)
        costing_config = _load_costing_config(config_path)
        report = summarize_usage_runs(
            conn,
            UsageRunsFilter(
                provider_id=provider_id,
                machine_id=machine_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=resolved_area,
                area_match=area_match,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=since_ms,
                until_ms=until_ms,
                split_thinking=split_thinking,
                limit=limit,
                order=order,
                include_archived=include_archived,
                archived_only=archived_only,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    return report


def usage_areas_report(
    db_path: Path | None = None,
    *,
    session_id: int | None = None,
    period: str | None = None,
    timezone: str | None = None,
    utc: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    machine_id: str | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    split_thinking: bool = False,
    config_path: Path | None = None,
) -> UsageAreasReport:
    from toktrail.db import migrate, summarize_usage_areas

    if period is not None and (since_ms is not None or until_ms is not None):
        msg = (
            "usage_areas_report() accepts either period or since/until filters, "
            "not both."
        )
        raise InvalidAPIUsageError(msg)

    try:
        resolved_range = resolve_time_range(
            period=period,
            timezone_name=timezone,
            utc=utc,
        )
    except ValueError as exc:
        raise InvalidAPIUsageError(str(exc)) from exc
    effective_since_ms = resolved_range.since_ms if period is not None else since_ms
    effective_until_ms = resolved_range.until_ms if period is not None else until_ms
    resolved_area, area_match = _resolve_area_filter_inputs(
        area=area,
        area_leaf=area_leaf,
        unassigned_area=unassigned_area,
    )

    conn, _ = _open_state_db(db_path)
    try:
        migrate(conn)
        report = summarize_usage_areas(
            conn,
            UsageReportFilter(
                tracking_session_id=session_id,
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=resolved_area,
                area_match=area_match,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=effective_since_ms,
                until_ms=effective_until_ms,
                split_thinking=split_thinking,
            ),
            costing_config=_load_costing_config(config_path),
        )
    except ValueError as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()

    public_report = _to_public_usage_areas_report(report)
    filters = dict(public_report.filters)
    if effective_since_ms is not None:
        existing_since = filters.get("since_ms")
        if isinstance(existing_since, int):
            filters["since_ms"] = max(existing_since, effective_since_ms)
        else:
            filters["since_ms"] = effective_since_ms
    if effective_until_ms is not None:
        existing_until = filters.get("until_ms")
        if isinstance(existing_until, int):
            filters["until_ms"] = min(existing_until, effective_until_ms)
        else:
            filters["until_ms"] = effective_until_ms
    if period is not None:
        filters["period"] = resolved_range.period
    if period is not None or timezone is not None or utc:
        filters["timezone"] = resolved_range.timezone
    return replace(public_report, filters=filters)


__all__ = [
    "get_usage_session",
    "session_report",
    "usage_report",
    "stats_report",
    "usage_series_report",
    "usage_sessions_report",
    "subscription_usage_report",
    "usage_runs_report",
    "usage_areas_report",
    "tool_usage_report",
]


def tool_usage_report(
    db_path: Path | None = None,
    *,
    period: str | None = None,
    timezone: str | None = None,
    utc: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    machine_id: str | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    agent: str | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    limit: int | None = 10,
    config_path: Path | None = None,
) -> ToolUsageReport:
    """Aggregate tool usage counts from persisted session digests."""
    from collections import Counter

    from toktrail.db import list_source_session_digests, migrate

    sessions_report = usage_sessions_report(
        db_path,
        period=period,
        timezone=timezone,
        utc=utc,
        since_ms=since_ms,
        until_ms=until_ms,
        machine_id=machine_id,
        harness=harness,
        source_session_id=source_session_id,
        provider_id=provider_id,
        model_id=model_id,
        agent=agent,
        area=area,
        area_leaf=area_leaf,
        area_exact=area_exact,
        unassigned_area=unassigned_area,
        limit=None,
        order="desc",
        breakdown=False,
        config_path=config_path,
    )

    conn, _ = _open_state_db(db_path)
    migrate(conn)

    all_digests = list_source_session_digests(conn)
    digest_lookup: dict[tuple[str, str, str], object] = {}
    for d in all_digests:
        key = (d.origin_machine_id or "", d.harness, d.source_session_id)
        digest_lookup[key] = d

    tool_counts: Counter[str] = Counter()
    tool_failures: Counter[str] = Counter()
    sessions_considered = 0
    sessions_with_stats = 0
    missing_count = 0

    for session in sessions_report.sessions:
        sessions_considered += 1
        machine = session.origin_machine_id or ""
        key = (machine, session.harness, session.source_session_id)
        digest = digest_lookup.get(key)
        if digest is None:
            missing_count += 1
            continue
        tool_health = digest.tool_health
        if not tool_health.tools and not tool_health.warnings:
            missing_count += 1
            continue
        sessions_with_stats += 1
        for name, count in tool_health.tools.items():
            tool_counts[name] += count
        for name, count in tool_health.failed_tools.items():
            tool_failures[name] += count

    conn.close()

    total = sum(tool_counts.values())
    rows: list[ToolUsageRow] = []
    for name, count in sorted(tool_counts.items(), key=lambda x: (-x[1], x[0])):
        percent = count / total if total > 0 else 0.0
        rows.append(ToolUsageRow(
            name=name,
            count=count,
            percent=percent,
            failure_count=tool_failures.get(name, 0),
        ))

    if limit is not None:
        rows = rows[:limit]

    filters: dict[str, object] = {}
    if period is not None:
        filters["period"] = period
    if harness is not None:
        filters["harness"] = harness
    if area is not None:
        filters["area"] = area

    return ToolUsageReport(
        filters=filters,
        total_tool_calls=total,
        sessions_considered=sessions_considered,
        sessions_with_tool_stats=sessions_with_stats,
        missing_session_count=missing_count,
        tools=tuple(rows),
    )
