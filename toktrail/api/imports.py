from __future__ import annotations

import sqlite3
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _get_harness, _open_state_db, _validate_source_path
from toktrail.api.models import ImportUsageResult
from toktrail.api.paths import resolve_source_path
from toktrail.errors import (
    InvalidAPIUsageError,
    NoActiveSessionError,
    SessionNotFoundError,
    UsageImportError,
)


def import_usage(
    db_path: Path | None,
    harness: str,
    *,
    session_id: int | None = None,
    source_path: Path | None = None,
    source_session_id: str | None = None,
    since_start: bool = False,
    since_ms: int | None = None,
    include_raw_json: bool = False,
) -> ImportUsageResult:
    if since_start and since_ms is not None:
        msg = "since_start=True and since_ms cannot be used together."
        raise InvalidAPIUsageError(msg)

    definition = _get_harness(harness)
    conn, _ = _open_state_db(db_path)
    try:
        resolved = _validate_source_path(
            definition.name,
            resolve_source_path(definition.name, source_path),
            explicit_source=source_path,
        )
        if resolved is None:
            msg = f"No source path available for harness {definition.name}."
            raise UsageImportError(msg)

        selected_session_id = session_id
        if selected_session_id is None:
            active = db_module.get_active_tracking_session(conn)
            if active is None:
                raise NoActiveSessionError(
                    "An active tracking session is required, but none exists."
                )
            selected_session_id = active

        tracking_session = db_module.get_tracking_session(conn, selected_session_id)
        if tracking_session is None:
            msg = f"Tracking session not found: {selected_session_id}"
            raise SessionNotFoundError(msg)

        effective_since_ms = since_ms
        if since_start:
            effective_since_ms = tracking_session.started_at_ms

        scan = definition.scan(
            resolved,
            source_session_id=source_session_id,
            include_raw_json=include_raw_json,
        )
        filtered_events = [
            event
            for event in scan.events
            if effective_since_ms is None or event.created_ms >= effective_since_ms
        ]
        try:
            insert_result = db_module.insert_usage_events(
                conn,
                selected_session_id,
                filtered_events,
            )
        except (sqlite3.Error, ValueError) as exc:
            msg = (
                f"Failed to import {definition.name} usage into session "
                f"{selected_session_id}: {exc}"
            )
            raise UsageImportError(msg) from exc
    finally:
        conn.close()

    rows_filtered = len(scan.events) - len(filtered_events)
    rows_imported = insert_result.rows_inserted
    rows_skipped = (
        scan.rows_skipped + rows_filtered + len(filtered_events) - rows_imported
    )
    first_event_ms = min((event.created_ms for event in filtered_events), default=None)
    last_event_ms = max((event.created_ms for event in filtered_events), default=None)

    return ImportUsageResult(
        tracking_session_id=selected_session_id,
        harness=definition.name,
        source_path=resolved,
        source_session_id=source_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=rows_imported,
        rows_skipped=rows_skipped,
        events_seen=len(scan.events),
        events_imported=rows_imported,
        events_skipped=scan.rows_skipped
        + rows_filtered
        + len(filtered_events)
        - rows_imported,
        files_seen=scan.files_seen,
        since_ms=effective_since_ms,
        first_event_ms=first_event_ms,
        last_event_ms=last_event_ms,
    )


__all__ = ["import_usage"]
