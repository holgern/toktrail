from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from toktrail import db as db_module
from toktrail.adapters.base import ImportScanState, ScanResult
from toktrail.api._common import _get_harness, _open_state_db, _validate_source_path
from toktrail.api.models import ImportUsageResult
from toktrail.api.paths import resolve_source_path
from toktrail.config import load_resolved_toktrail_config
from toktrail.errors import (
    InvalidAPIUsageError,
    RunNotFoundError,
    UsageImportError,
)


@dataclass(frozen=True)
class ImportRequest:
    db_path: Path | None
    harness: str
    session_id: int | None
    source_path: Path | None
    source_session_id: str | None
    since_start: bool
    since_ms: int | None
    use_active_session: bool
    include_raw_json: bool
    refresh_mode: str


@dataclass(frozen=True)
class ImportExecutionTiming:
    fingerprint_ms: int = 0
    scan_ms: int = 0
    db_write_ms: int = 0


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
    refresh_mode: str = "full",
) -> ImportUsageResult:
    if since_start and since_ms is not None:
        msg = "since_start=True and since_ms cannot be used together."
        raise InvalidAPIUsageError(msg)
    request = ImportRequest(
        db_path=db_path,
        harness=harness,
        session_id=session_id,
        source_path=source_path,
        source_session_id=source_session_id,
        since_start=since_start,
        since_ms=since_ms,
        use_active_session=use_active_session,
        include_raw_json=include_raw_json,
        refresh_mode=refresh_mode,
    )
    started = time.perf_counter()
    definition = _get_harness(request.harness)
    conn, _ = _open_state_db(request.db_path)
    timing = ImportExecutionTiming()
    try:
        resolved = _validate_source_path(
            definition.name,
            resolve_source_path(definition.name, request.source_path),
            explicit_source=request.source_path,
        )
        if resolved is None:
            msg = f"No source path available for harness {definition.name}."
            raise UsageImportError(msg)
        selected_session_id, tracking_session = _resolve_tracking_session(
            conn,
            session_id=request.session_id,
            use_active_session=request.use_active_session,
            since_start=request.since_start,
        )
        source_state, import_state = _load_import_scan_state(
            conn,
            harness_name=definition.name,
            resolved_source=resolved,
            source_session_id=request.source_session_id,
        )
        effective_since_ms, scan_since_ms = _resolve_scan_since_ms(
            since_ms=request.since_ms,
            tracking_session=tracking_session,
            source_state=source_state,
        )
        use_recursive_fingerprint = not (
            request.refresh_mode == "quick" and resolved.is_dir()
        )
        if use_recursive_fingerprint:
            started_fingerprint = time.perf_counter()
            pre_scan_fingerprint = _source_fingerprint(resolved)
            timing = ImportExecutionTiming(
                fingerprint_ms=int((time.perf_counter() - started_fingerprint) * 1000),
                scan_ms=timing.scan_ms,
                db_write_ms=timing.db_write_ms,
            )
        else:
            pre_scan_fingerprint = _source_state_fingerprint_tuple(source_state)
        if _can_skip_scan(
            use_recursive_fingerprint=use_recursive_fingerprint,
            selected_session_id=selected_session_id,
            source_state=source_state,
            pre_scan_fingerprint=pre_scan_fingerprint,
            scan_since_ms=scan_since_ms,
        ):
            scan = ScanResult(
                source_path=resolved,
                rows_seen=0,
                rows_skipped=0,
                events=[],
                files_seen=0,
            )
        else:
            started_scan = time.perf_counter()
            scan = _scan_source(
                definition=definition,
                resolved_source=resolved,
                source_session_id=request.source_session_id,
                include_raw_json=request.include_raw_json,
                scan_since_ms=scan_since_ms,
                import_state=import_state,
            )
            timing = ImportExecutionTiming(
                fingerprint_ms=timing.fingerprint_ms,
                scan_ms=int((time.perf_counter() - started_scan) * 1000),
                db_write_ms=timing.db_write_ms,
            )
        filtered_events = [
            event
            for event in scan.events
            if effective_since_ms is None or event.created_ms >= effective_since_ms
        ]
        started_db_write = time.perf_counter()
        insert_result = _persist_scan(
            conn=conn,
            definition_name=definition.name,
            selected_session_id=selected_session_id,
            tracking_session=tracking_session,
            resolved_source=resolved,
            source_session_id=request.source_session_id,
            source_state=source_state,
            pre_scan_fingerprint=pre_scan_fingerprint,
            scan=scan,
            filtered_events=filtered_events,
        )
        timing = ImportExecutionTiming(
            fingerprint_ms=timing.fingerprint_ms,
            scan_ms=timing.scan_ms,
            db_write_ms=int((time.perf_counter() - started_db_write) * 1000),
        )
    finally:
        conn.close()
    return _build_import_result(
        selected_session_id=selected_session_id,
        definition_name=definition.name,
        resolved_source=resolved,
        source_session_id=request.source_session_id,
        scan=scan,
        filtered_events=filtered_events,
        insert_result=insert_result,
        effective_since_ms=effective_since_ms,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        timing=timing,
    )


def import_configured_usage(  # noqa: C901
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
    refresh_mode: str = "full",
    quick_sync_margin_seconds: int = 10,
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

    if refresh_mode not in {"full", "quick", "none"}:
        msg = "refresh_mode must be one of: full, quick, none."
        raise InvalidAPIUsageError(msg)
    if refresh_mode == "none":
        return ()

    refresh_started_ms = int(time.time() * 1000)
    quick_cutoff_ms: int | None = None
    if refresh_mode == "quick":
        conn, _ = _open_state_db(db_path)
        try:
            last_started = db_module.get_state_metadata(
                conn,
                "last_refresh_started_ms",
            )
        finally:
            conn.close()
        if last_started is not None:
            quick_cutoff_ms = max(
                0,
                int(last_started) - quick_sync_margin_seconds * 1000,
            )
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
            if refresh_mode == "quick" and quick_cutoff_ms is not None:
                quick_resolved = resolve_source_path(harness_name, configured_source)
                if quick_resolved is not None and quick_resolved.is_file():
                    try:
                        mtime_ms = int(quick_resolved.stat().st_mtime_ns // 1_000_000)
                    except OSError:
                        mtime_ms = quick_cutoff_ms
                    if mtime_ms < quick_cutoff_ms:
                        continue
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
                        refresh_mode=refresh_mode,
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
                        elapsed_ms=0,
                        fingerprint_ms=0,
                        scan_ms=0,
                        db_write_ms=0,
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
                    refresh_mode=refresh_mode,
                )
            )
    conn, _ = _open_state_db(db_path)
    try:
        db_module.set_state_metadata(
            conn,
            "last_refresh_started_ms",
            str(refresh_started_ms),
        )
        db_module.set_state_metadata(
            conn,
            "last_refresh_completed_ms",
            str(int(time.time() * 1000)),
        )
        conn.commit()
    finally:
        conn.close()
    return tuple(results)


def _resolve_tracking_session(
    conn: sqlite3.Connection,
    *,
    session_id: int | None,
    use_active_session: bool,
    since_start: bool,
) -> tuple[int | None, object | None]:
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
    return selected_session_id, tracking_session


def _resolve_scan_since_ms(
    *,
    since_ms: int | None,
    tracking_session: object | None,
    source_state: object | None,
) -> tuple[int | None, int | None]:
    effective_since_ms = since_ms
    if tracking_session is not None:
        started_at_ms = tracking_session.started_at_ms
        if effective_since_ms is None:
            effective_since_ms = started_at_ms
        else:
            effective_since_ms = max(effective_since_ms, started_at_ms)
    scan_since_ms = effective_since_ms
    if source_state is not None and source_state.last_imported_created_ms is not None:
        scan_since_ms = (
            max(scan_since_ms, source_state.last_imported_created_ms)
            if scan_since_ms is not None
            else source_state.last_imported_created_ms
        )
    return effective_since_ms, scan_since_ms


def _load_import_scan_state(
    conn: sqlite3.Connection,
    *,
    harness_name: str,
    resolved_source: Path,
    source_session_id: str | None,
) -> tuple[object | None, ImportScanState]:
    source_state = db_module.get_import_source_state(
        conn,
        harness=harness_name,
        source_path=str(resolved_source),
        source_session_id=source_session_id,
    )
    file_states = (
        db_module.list_import_source_file_states(
            conn,
            harness=harness_name,
            source_path=str(resolved_source),
        )
        if resolved_source.is_dir()
        else ()
    )
    return source_state, ImportScanState.from_file_states(
        source_state=source_state,
        file_states=file_states,
    )


def _source_state_fingerprint_tuple(
    source_state: object | None,
) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    return (
        source_state.fingerprint_size if source_state is not None else None,
        source_state.fingerprint_mtime_ns if source_state is not None else None,
        source_state.fingerprint_inode if source_state is not None else None,
        source_state.sqlite_page_count if source_state is not None else None,
        source_state.sqlite_schema_version if source_state is not None else None,
    )


def _can_skip_scan(
    *,
    use_recursive_fingerprint: bool,
    selected_session_id: int | None,
    source_state: object | None,
    pre_scan_fingerprint: tuple[
        int | None, int | None, int | None, int | None, int | None
    ],
    scan_since_ms: int | None,
) -> bool:
    return bool(
        use_recursive_fingerprint
        and selected_session_id is None
        and source_state is not None
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
    )


def _scan_source(
    *,
    definition: object,
    resolved_source: Path,
    source_session_id: str | None,
    include_raw_json: bool,
    scan_since_ms: int | None,
    import_state: ImportScanState,
) -> ScanResult:
    return definition.scan(
        resolved_source,
        source_session_id=source_session_id,
        include_raw_json=include_raw_json,
        since_ms=scan_since_ms,
        import_state=import_state,
    )


def _persist_scan(
    *,
    conn: sqlite3.Connection,
    definition_name: str,
    selected_session_id: int | None,
    tracking_session: object | None,
    resolved_source: Path,
    source_session_id: str | None,
    source_state: object | None,
    pre_scan_fingerprint: tuple[
        int | None, int | None, int | None, int | None, int | None
    ],
    scan: ScanResult,
    filtered_events: list[object],
) -> object:
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
            f"Failed to import {definition_name} usage into session "
            f"{selected_session_id}: {exc}"
        )
        raise UsageImportError(msg) from exc
    db_module.persist_source_session_metadata(
        conn,
        source_path=resolved_source,
        scan_session_metadata=scan.session_metadata,
        events=filtered_events,
    )
    latest_seen_ms = max((event.created_ms for event in filtered_events), default=None)
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
        harness=definition_name,
        source_path=str(resolved_source),
        source_session_id=source_session_id,
        fingerprint_size=pre_scan_fingerprint[0],
        fingerprint_mtime_ns=pre_scan_fingerprint[1],
        fingerprint_inode=pre_scan_fingerprint[2],
        sqlite_page_count=pre_scan_fingerprint[3],
        sqlite_schema_version=pre_scan_fingerprint[4],
        last_imported_created_ms=latest_imported_ms,
    )
    for file_state in scan.file_states:
        db_module.upsert_import_source_file_state(
            conn,
            harness=file_state.harness,
            source_path=file_state.source_path,
            file_path=file_state.file_path,
            size=file_state.size,
            mtime_ns=file_state.mtime_ns,
            inode=file_state.inode,
            last_imported_created_ms=file_state.last_imported_created_ms,
            last_file_offset=file_state.last_file_offset,
            parser_version=file_state.parser_version,
            parser_state_json=file_state.parser_state_json,
        )
    if resolved_source.is_dir():
        db_module.delete_import_source_file_states(
            conn,
            harness=definition_name,
            source_path=str(resolved_source),
            keep_file_paths=scan.discovered_file_paths,
        )
    conn.commit()
    return insert_result


def _build_import_result(
    *,
    selected_session_id: int | None,
    definition_name: str,
    resolved_source: Path,
    source_session_id: str | None,
    scan: ScanResult,
    filtered_events: list[object],
    insert_result: object,
    effective_since_ms: int | None,
    elapsed_ms: int,
    timing: ImportExecutionTiming,
) -> ImportUsageResult:
    rows_filtered = len(scan.events) - len(filtered_events)
    rows_imported = insert_result.rows_inserted
    rows_skipped = scan.rows_skipped + rows_filtered + insert_result.rows_skipped
    first_event_ms = min((event.created_ms for event in filtered_events), default=None)
    last_event_ms = max((event.created_ms for event in filtered_events), default=None)
    return ImportUsageResult(
        run_id=selected_session_id,
        harness=definition_name,
        source_path=resolved_source,
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
        elapsed_ms=elapsed_ms,
        fingerprint_ms=timing.fingerprint_ms,
        scan_ms=timing.scan_ms,
        db_write_ms=timing.db_write_ms,
    )


__all__ = ["import_configured_usage", "import_usage"]


def _source_fingerprint(
    path: Path,
) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    if not path.exists():
        return (None, None, None, None, None)
    if path.is_dir():
        return _directory_fingerprint(path)
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


def _directory_fingerprint(
    path: Path,
) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    file_count = 0
    total_size = 0
    max_mtime_ns = 0
    inode_hash = 0
    try:
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            file_count += 1
            total_size += int(stat.st_size)
            max_mtime_ns = max(max_mtime_ns, int(stat.st_mtime_ns))
            inode_hash ^= int(stat.st_ino)
    except OSError:
        return (None, None, None, None, None)
    return (total_size, max_mtime_ns, file_count ^ inode_hash, None, None)
