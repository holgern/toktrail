from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _get_harness, _open_state_db, _validate_source_path
from toktrail.api.models import ImportUsageResult
from toktrail.api.paths import resolve_source_path
from toktrail.config import load_resolved_toktrail_config
from toktrail.errors import (
    InvalidAPIUsageError,
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
    use_active_session: bool = True,
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
        tracking_session = None
        if selected_session_id is None and use_active_session:
            selected_session_id = db_module.get_active_tracking_session(conn)
        if selected_session_id is not None:
            tracking_session = db_module.get_tracking_session(conn, selected_session_id)
            if tracking_session is None:
                msg = f"Tracking session not found: {selected_session_id}"
                raise SessionNotFoundError(msg)
        elif since_start:
            msg = "since_start=True requires an explicit or active tracking session."
            raise InvalidAPIUsageError(msg)

        effective_since_ms = since_ms
        if since_start and tracking_session is not None:
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
    rows_skipped = scan.rows_skipped + rows_filtered + insert_result.rows_skipped
    first_event_ms = min((event.created_ms for event in filtered_events), default=None)
    last_event_ms = max((event.created_ms for event in filtered_events), default=None)

    return ImportUsageResult(
        tracking_session_id=selected_session_id,
        harness=definition.name,
        source_path=resolved,
        source_session_id=source_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=rows_imported,
        rows_linked=insert_result.rows_linked,
        rows_skipped=rows_skipped,
        events_seen=len(scan.events),
        events_imported=rows_imported,
        events_skipped=scan.rows_skipped + rows_filtered + insert_result.rows_skipped,
        files_seen=scan.files_seen,
        since_ms=effective_since_ms,
        first_event_ms=first_event_ms,
        last_event_ms=last_event_ms,
    )


def import_configured_usage(
    db_path: Path | None,
    *,
    harnesses: Sequence[str] | None = None,
    source_path: Path | None = None,
    session_id: int | None = None,
    use_active_session: bool = True,
    include_raw_json: bool | None = None,
    config_path: Path | None = None,
) -> tuple[ImportUsageResult, ...]:
    loaded = load_resolved_toktrail_config(config_path)
    import_config = loaded.config.imports
    selected_harnesses = (
        tuple(_get_harness(harness).name for harness in harnesses)
        if harnesses is not None
        else import_config.harnesses
    )
    if source_path is not None and len(selected_harnesses) != 1:
        msg = "--source is only valid when importing exactly one harness."
        raise InvalidAPIUsageError(msg)

    results: list[ImportUsageResult] = []
    for harness_name in selected_harnesses:
        sources = import_config.sources or {}
        configured_source = (
            source_path
            if source_path is not None
            else sources.get(harness_name)
        )
        resolved = resolve_source_path(harness_name, configured_source)
        if resolved is None or not resolved.exists():
            if source_path is not None or import_config.missing_source == "error":
                result = import_usage(
                    db_path,
                    harness_name,
                    session_id=session_id,
                    source_path=configured_source,
                    use_active_session=use_active_session,
                    include_raw_json=(
                        import_config.include_raw_json
                        if include_raw_json is None
                        else include_raw_json
                    ),
                )
                results.append(result)
                continue
            results.append(
                ImportUsageResult(
                    tracking_session_id=session_id,
                    harness=harness_name,
                    source_path=resolved,
                    source_session_id=None,
                    rows_seen=0,
                    rows_imported=0,
                    rows_skipped=0,
                    events_seen=0,
                    events_imported=0,
                    events_skipped=0,
                    status="skipped",
                    error_message=(
                        None
                        if import_config.missing_source == "skip"
                        else f"Missing source path for {harness_name}: {resolved}"
                    ),
                )
            )
            continue
        results.append(
            import_usage(
                db_path,
                harness_name,
                session_id=session_id,
                source_path=configured_source,
                use_active_session=use_active_session,
                include_raw_json=(
                    import_config.include_raw_json
                    if include_raw_json is None
                    else include_raw_json
                ),
            )
        )
    return tuple(results)


__all__ = ["import_configured_usage", "import_usage"]
