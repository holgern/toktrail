from __future__ import annotations

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
from toktrail.reporting import UsageReportFilter


def session_report(
    db_path: Path | None,
    session_id: int | None = None,
    *,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    agent: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
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
                agent=agent,
                since_ms=since_ms,
                until_ms=until_ms,
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
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    agent: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    config_path: Path | None = None,
) -> TrackingSessionReport:
    if session_id is None:
        msg = "usage_report() currently requires session_id."
        raise InvalidAPIUsageError(msg)
    return session_report(
        db_path,
        session_id,
        harness=harness,
        source_session_id=source_session_id,
        provider_id=provider_id,
        model_id=model_id,
        agent=agent,
        since_ms=since_ms,
        until_ms=until_ms,
        config_path=config_path,
    )


__all__ = ["session_report", "usage_report"]
