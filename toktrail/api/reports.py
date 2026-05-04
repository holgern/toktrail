from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _load_costing_config, _open_state_db
from toktrail.api._conversions import (
    _to_public_report,
    _to_public_series_report,
    _to_public_subscription_report,
    _to_public_usage_sessions_report,
)
from toktrail.api.models import (
    RunReport,
    SubscriptionUsageReport,
    UsageSeriesReport,
    UsageSessionsReport,
)
from toktrail.errors import (
    InvalidAPIUsageError,
    NoActiveRunError,
    RunNotFoundError,
    StateDatabaseError,
)
from toktrail.periods import resolve_time_range
from toktrail.reporting import UsageReportFilter


def session_report(
    db_path: Path | None,
    session_id: int | None = None,
    *,
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

    conn, _ = _open_state_db(db_path)
    try:
        report = db_module.summarize_usage(
            conn,
            UsageReportFilter(
                tracking_session_id=session_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
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
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    project: str | None = None,
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
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                since_ms=since_ms,
                until_ms=until_ms,
                split_thinking=split_thinking,
                project=project,
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
) -> SubscriptionUsageReport:
    conn, _ = _open_state_db(db_path)
    try:
        report = db_module.summarize_subscription_usage(
            conn,
            _load_costing_config(config_path),
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
    since_ms: int | None = None,
    until_ms: int | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    limit: int | None = 10,
    order: str = "desc",
    breakdown: bool = False,
    split_thinking: bool = False,
    config_path: Path | None = None,
) -> UsageSessionsReport:
    from toktrail.db import migrate, summarize_usage_sessions
    from toktrail.reporting import UsageSessionsFilter

    if order not in ("asc", "desc"):
        msg = f"Invalid order: {order!r}. Use asc or desc."
        raise InvalidAPIUsageError(msg)
    if limit is not None and limit < 0:
        msg = f"Invalid limit: {limit}. Must be non-negative."
        raise InvalidAPIUsageError(msg)

    conn, _ = _open_state_db(db_path)
    try:
        migrate(conn)
        costing_config = _load_costing_config(config_path)
        report = summarize_usage_sessions(
            conn,
            UsageSessionsFilter(
                tracking_session_id=session_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                since_ms=since_ms,
                until_ms=until_ms,
                split_thinking=split_thinking,
                limit=limit,
                order=order,
                breakdown=breakdown,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    return _to_public_usage_sessions_report(report)


def usage_runs_report(
    db_path: Path | None = None,
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    limit: int | None = 10,
    order: str = "desc",
    split_thinking: bool = False,
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

    conn, _ = _open_state_db(db_path)
    try:
        migrate(conn)
        costing_config = _load_costing_config(config_path)
        report = summarize_usage_runs(
            conn,
            UsageRunsFilter(
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                since_ms=since_ms,
                until_ms=until_ms,
                split_thinking=split_thinking,
                limit=limit,
                order=order,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    return report


__all__ = [
    "session_report",
    "usage_report",
    "usage_series_report",
    "usage_sessions_report",
    "subscription_usage_report",
    "usage_runs_report",
]
