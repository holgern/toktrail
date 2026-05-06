from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from toktrail import db as db_module
from toktrail.adapters.base import ScanResult
from toktrail.api._common import _get_harness, _open_state_db, _validate_source_path
from toktrail.api.models import ImportUsageResult
from toktrail.api.paths import resolve_source_path
from toktrail.config import load_resolved_toktrail_config
from toktrail.errors import (
    InvalidAPIUsageError,
    RunNotFoundError,
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
                msg = f"Run not found: {selected_session_id}"
                raise RunNotFoundError(msg)
        elif since_start:
            msg = "since_start=True requires an explicit or active run."
            raise InvalidAPIUsageError(msg)

        effective_since_ms = since_ms
        if tracking_session is not None:
            if effective_since_ms is None:
                effective_since_ms = tracking_session.started_at_ms
            else:
                effective_since_ms = max(
                    effective_since_ms,
                    tracking_session.started_at_ms,
                )

        source_state = db_module.get_import_source_state(
            conn,
            harness=definition.name,
            source_path=str(resolved),
            source_session_id=source_session_id,
        )
        scan_since_ms = effective_since_ms
        if (
            source_state is not None
            and source_state.last_imported_created_ms is not None
        ):
            scan_since_ms = (
                max(scan_since_ms, source_state.last_imported_created_ms)
                if scan_since_ms is not None
                else source_state.last_imported_created_ms
            )

        pre_scan_fingerprint = _source_fingerprint(resolved)
        scan: ScanResult
        if (
            source_state is not None
            and source_state.fingerprint_size == pre_scan_fingerprint[0]
            and source_state.fingerprint_mtime_ns == pre_scan_fingerprint[1]
            and source_state.fingerprint_inode == pre_scan_fingerprint[2]
            and source_state.sqlite_page_count == pre_scan_fingerprint[3]
            and source_state.sqlite_schema_version == pre_scan_fingerprint[4]
            and source_state.last_imported_created_ms is not None
            and (
                scan_since_ms is None
                or scan_since_ms <= source_state.last_imported_created_ms
            )
        ):
            scan = ScanResult(
                source_path=resolved,
                rows_seen=0,
                rows_skipped=0,
                events=[],
                files_seen=0,
            )
        else:
            scan = definition.scan(
                resolved,
                source_session_id=source_session_id,
                include_raw_json=include_raw_json,
                since_ms=scan_since_ms,
                import_state=source_state,
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
                link_scope=(
                    tracking_session.scope if tracking_session is not None else None
                ),
            )
        except (sqlite3.Error, ValueError) as exc:
            msg = (
                f"Failed to import {definition.name} usage into session "
                f"{selected_session_id}: {exc}"
            )
            raise UsageImportError(msg) from exc

        fingerprint = pre_scan_fingerprint
        latest_seen_ms = max(
            (event.created_ms for event in filtered_events), default=None
        )
        latest_imported_ms = latest_seen_ms
        if (
            source_state is not None
            and source_state.last_imported_created_ms is not None
            and latest_imported_ms is not None
        ):
            latest_imported_ms = max(
                latest_imported_ms,
                source_state.last_imported_created_ms,
            )
        elif source_state is not None and latest_imported_ms is None:
            latest_imported_ms = source_state.last_imported_created_ms

        db_module.upsert_import_source_state(
            conn,
            harness=definition.name,
            source_path=str(resolved),
            source_session_id=source_session_id,
            fingerprint_size=fingerprint[0],
            fingerprint_mtime_ns=fingerprint[1],
            fingerprint_inode=fingerprint[2],
            sqlite_page_count=fingerprint[3],
            sqlite_schema_version=fingerprint[4],
            last_imported_created_ms=latest_imported_ms,
        )
    finally:
        conn.close()

    rows_filtered = len(scan.events) - len(filtered_events)
    rows_imported = insert_result.rows_inserted
    rows_skipped = scan.rows_skipped + rows_filtered + insert_result.rows_skipped
    first_event_ms = min((event.created_ms for event in filtered_events), default=None)
    last_event_ms = max((event.created_ms for event in filtered_events), default=None)

    return ImportUsageResult(
        run_id=selected_session_id,
        harness=definition.name,
        source_path=resolved,
        source_session_id=source_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=rows_imported,
        rows_linked=insert_result.rows_linked,
        rows_scope_excluded=insert_result.rows_scope_excluded,
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
    since_start: bool = False,
    since_ms: int | None = None,
) -> tuple[ImportUsageResult, ...]:
    if since_start and since_ms is not None:
        msg = "since_start=True and since_ms cannot be used together."
        raise InvalidAPIUsageError(msg)
    loaded = load_resolved_toktrail_config(config_path)
    import_config = loaded.config.imports
    selected_harnesses = (
        tuple(_get_harness(harness).name for harness in harnesses)
        if harnesses is not None
        else import_config.harnesses
    )
    if session_id is not None or use_active_session:
        conn, _ = _open_state_db(db_path)
        try:
            selected_session_id = session_id
            if selected_session_id is None and use_active_session:
                selected_session_id = db_module.get_active_tracking_session(conn)
            if selected_session_id is not None:
                tracking_session = db_module.get_tracking_session(
                    conn,
                    selected_session_id,
                )
                if tracking_session is None:
                    msg = f"Run not found: {selected_session_id}"
                    raise RunNotFoundError(msg)
                if tracking_session.scope.harnesses:
                    allowed_harnesses = set(tracking_session.scope.harnesses)
                    selected_harnesses = tuple(
                        harness_name
                        for harness_name in selected_harnesses
                        if harness_name in allowed_harnesses
                    )
        finally:
            conn.close()
    if source_path is not None and len(selected_harnesses) != 1:
        msg = "--source is only valid when importing exactly one harness."
        raise InvalidAPIUsageError(msg)

    results: list[ImportUsageResult] = []
    for harness_name in selected_harnesses:
        sources = import_config.sources or {}
        raw_source = (
            source_path if source_path is not None else sources.get(harness_name)
        )

        # Normalize to a list of individual source paths
        if raw_source is None:
            source_candidates: Sequence[Path | None] = [None]
        elif isinstance(raw_source, list):
            source_candidates = raw_source
        else:
            source_candidates = [raw_source]

        for configured_source in source_candidates:
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
                        since_start=since_start,
                        since_ms=since_ms,
                    )
                    results.append(result)
                    continue
                results.append(
                    ImportUsageResult(
                        run_id=session_id,
                        harness=harness_name,
                        source_path=resolved,
                        source_session_id=None,
                        rows_seen=0,
                        rows_imported=0,
                        rows_linked=0,
                        rows_scope_excluded=0,
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
                    since_start=since_start,
                    since_ms=since_ms,
                )
            )
    return tuple(results)


__all__ = ["import_configured_usage", "import_usage"]


def _source_fingerprint(
    path: Path,
) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    if not path.exists():
        return (None, None, None, None, None)
    try:
        stat = path.stat()
        size = int(stat.st_size)
        mtime_ns = int(stat.st_mtime_ns)
        inode = int(stat.st_ino)
    except OSError:
        return (None, None, None, None, None)

    sqlite_page_count: int | None = None
    sqlite_schema_version: int | None = None
    if path.is_file() and path.suffix == ".db":
        try:
            sqlite_conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            sqlite_conn.execute("PRAGMA query_only = ON")
            sqlite_page_count = int(
                sqlite_conn.execute("PRAGMA page_count").fetchone()[0]
            )
            sqlite_schema_version = int(
                sqlite_conn.execute("PRAGMA user_version").fetchone()[0]
            )
            sqlite_conn.close()
        except (sqlite3.Error, OSError, TypeError, ValueError):
            sqlite_page_count = None
            sqlite_schema_version = None
    return (size, mtime_ns, inode, sqlite_page_count, sqlite_schema_version)
