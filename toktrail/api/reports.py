from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _load_costing_config, _open_state_db
from toktrail.api._conversions import _to_public_report
from toktrail.api.models import TrackingSessionReport
from toktrail.errors import (
    InvalidAPIUsageError,
    NoActiveSessionError,
    SessionNotFoundError,
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
    split_thinking: bool = True,
    config_path: Path | None = None,
) -> TrackingSessionReport:
    conn, _ = _open_state_db(db_path)
    try:
        selected_session_id = session_id
        if selected_session_id is None:
            active = db_module.get_active_tracking_session(conn)
            if active is None:
                raise NoActiveSessionError(
                    "An active tracking session is required, but none exists."
                )
            selected_session_id = active
        if db_module.get_tracking_session(conn, selected_session_id) is None:
            msg = f"Tracking session not found: {selected_session_id}"
            raise SessionNotFoundError(msg)
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
    split_thinking: bool = True,
    config_path: Path | None = None,
) -> TrackingSessionReport:
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
        filters["since_ms"] = effective_since_ms
    if effective_until_ms is not None:
        filters["until_ms"] = effective_until_ms
    if period is not None:
        filters["period"] = resolved_range.period
    if period is not None or timezone is not None or utc:
        filters["timezone"] = resolved_range.timezone
    return replace(public_report, filters=filters)


__all__ = ["session_report", "usage_report"]
