from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import typer

from toktrail.adapters.registry import get_harness
from toktrail.api.imports import import_configured_usage as import_configured_usage_api
from toktrail.api.models import ImportUsageResult
from toktrail.cli_parts.types import ImportExecutionResult
from toktrail.db import (
    InsertUsageResult,
    connect,
    get_active_tracking_session,
    get_state_metadata,
    get_tracking_session,
    insert_usage_events,
    migrate,
    persist_source_session_metadata,
)
from toktrail.errors import ToktrailError


@dataclass(frozen=True)
class RefreshRuntime:
    resolve_state_db: Callable[[typer.Context], Path]
    resolve_config_path: Callable[[typer.Context], Path]
    open_toktrail_connection: Callable[[typer.Context], object]
    load_resolved_toktrail_config_or_exit: Callable[[typer.Context], object]
    exit_with_error: Callable[[str], None]
    format_int: Callable[[int], str]
    print_table: Callable[..., None]


_RUNTIME: RefreshRuntime | None = None


def configure_refresh_runtime(runtime: RefreshRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def _runtime() -> RefreshRuntime:
    if _RUNTIME is None:
        msg = "refresh runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME


def _resolve_state_db(ctx: typer.Context) -> Path:
    return _runtime().resolve_state_db(ctx)


def _resolve_config_path(ctx: typer.Context) -> Path:
    return _runtime().resolve_config_path(ctx)


def _open_toktrail_connection(ctx: typer.Context) -> sqlite3.Connection:
    return _runtime().open_toktrail_connection(ctx)  # type: ignore[return-value]


def _exit_with_error(message: str) -> None:
    _runtime().exit_with_error(message)


def _load_resolved_toktrail_config_or_exit(ctx: typer.Context) -> object:
    return _runtime().load_resolved_toktrail_config_or_exit(ctx)


def _format_int(value: int) -> str:
    return _runtime().format_int(value)


def _print_table(*args: object, **kwargs: object) -> None:
    _runtime().print_table(*args, **kwargs)


def refresh_before_report(
    ctx: typer.Context,
    *,
    enabled: bool,
    details: bool,
    json_output: bool,
    harness: str | None = None,
    session_id: int | None = None,
    use_active_session: bool = True,
    include_raw_json: bool | None = None,
    since_start: bool = False,
) -> tuple[ImportUsageResult, ...]:
    refresh_mode = resolve_report_refresh_mode(ctx, enabled=enabled)
    if refresh_mode == "never":
        return ()
    if refresh_mode == "auto" and not details:
        loaded_config = _load_resolved_toktrail_config_or_exit(ctx)
        if should_skip_report_auto_refresh(
            state_db_path=_resolve_state_db(ctx),
            min_refresh_interval_secs=loaded_config.config.reports.min_refresh_interval_secs,  # type: ignore[attr-defined]
        ):
            return ()

    harnesses = [harness] if harness is not None else None
    try:
        results = import_configured_usage_api(
            _resolve_state_db(ctx),
            harnesses=harnesses,
            source_path=None,
            session_id=session_id,
            use_active_session=use_active_session,
            include_raw_json=include_raw_json,
            config_path=_resolve_config_path(ctx),
            refresh_mode="full" if refresh_mode == "always" else "quick",
            since_start=since_start,
        )
    except (OSError, ValueError, ToktrailError) as exc:
        _exit_with_error(str(exc))

    if details and not json_output:
        print_configured_refresh_results(results)
    return results


def resolve_report_refresh_mode(ctx: typer.Context, *, enabled: bool) -> str:
    source = ctx.get_parameter_source("refresh")
    if source is not None and source.name != "DEFAULT":
        return "always" if enabled else "never"
    loaded_config = _load_resolved_toktrail_config_or_exit(ctx)
    return loaded_config.config.reports.refresh  # type: ignore[no-any-return, attr-defined]


def should_skip_report_auto_refresh(
    *,
    state_db_path: Path,
    min_refresh_interval_secs: int,
) -> bool:
    conn = connect(state_db_path.expanduser())
    try:
        migrate(conn)
        completed = get_state_metadata(conn, "last_refresh_completed_ms")
    finally:
        conn.close()
    if completed is None:
        return False
    try:
        completed_ms = int(completed)
    except ValueError:
        return False
    return int(time.time() * 1000) - completed_ms < min_refresh_interval_secs * 1000


def wrap_refresh_json_payload(
    report_payload: object,
    *,
    refresh_results: tuple[ImportUsageResult, ...],
    include_refresh: bool,
) -> object:
    if not include_refresh:
        return report_payload
    return {
        "refresh": [result.as_dict() for result in refresh_results],
        "report": report_payload,
    }


def refresh_usage_impl(
    ctx: typer.Context,
    *,
    harness: str | None,
    source: Path | None,
    run_id: int | None,
    source_session_id: str | None,
    since_run_start: bool,
    raw: bool | None,
    no_run: bool,
    dry_run: bool,
    json_output: bool,
    full: bool,
) -> None:
    if harness is not None and source is not None:
        explicit_include_raw = False if raw is None else raw
        try:
            result = run_harness_import_with_dry_run(
                ctx,
                harness_name=harness,
                source_path=source,
                tracking_session_id=run_id,
                source_session_id=source_session_id,
                since_start=since_run_start,
                include_raw_json=explicit_include_raw,
                no_session=no_run,
                dry_run=dry_run,
            )
        except (OSError, ValueError, ToktrailError) as exc:
            _exit_with_error(str(exc))

        if json_output:
            output = asdict(result)
            if "source_path" in output:
                output["source_path"] = str(output["source_path"])
            if "harness" in output:
                output["harness"] = str(output["harness"]).lower()
            if dry_run:
                output["dry_run"] = True
            typer.echo(json.dumps([output], indent=2))
            return

        print_refresh_result(result)
        if dry_run:
            typer.echo("\n[dry-run: changes were not persisted]")
        return

    if harness is None and source is None:
        try:
            results = import_configured_usage_api(
                _resolve_state_db(ctx),
                harnesses=None,
                source_path=None,
                session_id=run_id,
                use_active_session=not no_run,
                include_raw_json=raw,
                config_path=_resolve_config_path(ctx),
                since_start=since_run_start,
                refresh_mode="full" if full else "quick",
            )
        except (OSError, ValueError, ToktrailError) as exc:
            _exit_with_error(str(exc))

        if json_output:
            typer.echo(json.dumps([result.as_dict() for result in results], indent=2))
            return
        print_configured_refresh_results(results)
        return

    _exit_with_error(
        "Either provide both --harness and --source, "
        "or neither for config-based refresh"
    )


def run_harness_import(
    ctx: typer.Context,
    *,
    harness_name: str,
    source_path: Path | None,
    tracking_session_id: int | None,
    source_session_id: str | None,
    since_start: bool,
    include_raw_json: bool = False,
) -> ImportExecutionResult:
    harness = get_harness(harness_name)
    conn = _open_toktrail_connection(ctx)
    try:
        resolved_source = harness.resolve_source_path(source_path)
        if resolved_source is None or not resolved_source.exists():
            _exit_with_error(
                missing_source_path_message(
                    harness_name,
                    resolved_source,
                    explicit_source=source_path,
                )
            )

        selected_session_id = tracking_session_id
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active run found.")

        tracking_session = get_tracking_session(conn, selected_session_id)
        if tracking_session is None:
            _exit_with_error(f"Run not found: {selected_session_id}")

        scan = harness.scan(
            resolved_source,
            source_session_id=source_session_id,
            include_raw_json=include_raw_json,
        )
        since_ms = tracking_session.started_at_ms
        if since_start:
            since_ms = tracking_session.started_at_ms
        filtered_events = [
            event
            for event in scan.events
            if since_ms is None or event.created_ms >= since_ms
        ]
        insert_result = insert_usage_events(
            conn,
            selected_session_id,
            filtered_events,
            link_scope=tracking_session.scope,
        )
        persist_source_session_metadata(
            conn,
            source_path=resolved_source,
            scan_session_metadata=scan.session_metadata,
            events=filtered_events,
        )
        rows_filtered = len(scan.events) - len(filtered_events)
    finally:
        conn.close()

    rows_skipped = (
        scan.rows_skipped
        + rows_filtered
        + len(filtered_events)
        - insert_result.rows_inserted
    )
    return ImportExecutionResult(
        harness=harness.display_name,
        source_path=resolved_source,  # type: ignore[arg-type]
        run_id=selected_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=insert_result.rows_inserted,
        rows_skipped=rows_skipped,
    )


def run_harness_import_with_dry_run(
    ctx: typer.Context,
    *,
    harness_name: str,
    source_path: Path,
    tracking_session_id: int | None,
    source_session_id: str | None,
    since_start: bool,
    include_raw_json: bool,
    no_session: bool,
    dry_run: bool,
) -> ImportExecutionResult:
    harness = get_harness(harness_name)
    conn = _open_toktrail_connection(ctx)
    try:
        resolved_source = harness.resolve_source_path(source_path)
        if resolved_source is None or not resolved_source.exists():
            _exit_with_error(
                missing_source_path_message(
                    harness_name,
                    resolved_source,
                    explicit_source=source_path,
                )
            )

        selected_session_id = tracking_session_id
        if selected_session_id is None and not no_session:
            selected_session_id = get_active_tracking_session(conn)

        tracking_session = None
        if selected_session_id is not None:
            tracking_session = get_tracking_session(conn, selected_session_id)
            if tracking_session is None:
                _exit_with_error(f"Run not found: {selected_session_id}")

        scan = harness.scan(
            resolved_source,
            source_session_id=source_session_id,
            include_raw_json=include_raw_json,
        )

        since_ms = None
        if tracking_session is not None:
            since_ms = tracking_session.started_at_ms
        if since_start and tracking_session is not None:
            since_ms = tracking_session.started_at_ms

        filtered_events = [
            event
            for event in scan.events
            if since_ms is None or event.created_ms >= since_ms
        ]

        if not dry_run:
            insert_result = insert_usage_events(
                conn,
                selected_session_id,
                filtered_events,
                link_scope=(
                    tracking_session.scope if tracking_session is not None else None
                ),
            )
            persist_source_session_metadata(
                conn,
                source_path=resolved_source,
                scan_session_metadata=scan.session_metadata,
                events=filtered_events,
            )
        else:
            insert_result = InsertUsageResult(
                rows_inserted=len(filtered_events),
                rows_linked=0,
                rows_skipped=0,
            )

        rows_filtered = len(scan.events) - len(filtered_events)
    finally:
        conn.close()

    rows_skipped = (
        scan.rows_skipped
        + rows_filtered
        + len(filtered_events)
        - insert_result.rows_inserted
    )
    return ImportExecutionResult(
        harness=harness.display_name,
        source_path=resolved_source,  # type: ignore[arg-type]
        run_id=selected_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=insert_result.rows_inserted,
        rows_skipped=rows_skipped,
    )


def missing_source_path_message(
    harness_name: str,
    resolved_source: Path | None,
    *,
    explicit_source: Path | None,
) -> str:
    if harness_name == "opencode":
        return f"OpenCode database not found: {resolved_source}"
    if harness_name == "pi":
        return f"Pi sessions path not found: {resolved_source}"
    if harness_name == "copilot" and (
        explicit_source is not None
        or (resolved_source is not None and resolved_source.suffix == ".jsonl")
    ):
        return f"Copilot telemetry file not found: {resolved_source}"
    display_name = get_harness(harness_name).display_name
    return f"{display_name} source path not found: {resolved_source}"


def print_refresh_result(result: ImportExecutionResult) -> None:
    typer.echo(f"Refreshed {result.harness} usage:")
    typer.echo(f"  source path: {result.source_path}")
    typer.echo(f"  run: {result.run_id}")
    typer.echo(f"  rows seen: {result.rows_seen}")
    typer.echo(f"  rows imported: {result.rows_imported}")
    typer.echo(f"  rows skipped: {result.rows_skipped}")


def print_configured_refresh_results(results: tuple[ImportUsageResult, ...]) -> None:
    typer.echo("Refreshed usage")
    rows = []
    for result in results:
        rows.append(
            {
                "harness": result.harness,
                "files": _format_int(result.files_seen or 0),
                "inserted": _format_int(result.rows_imported),
                "linked": _format_int(result.rows_linked),
                "scope_excluded": _format_int(result.rows_scope_excluded),
                "skipped": _format_int(result.rows_skipped),
                "fingerprint_ms": _format_int(result.fingerprint_ms or 0),
                "scan_ms": _format_int(result.scan_ms or 0),
                "db_write_ms": _format_int(result.db_write_ms or 0),
                "elapsed_ms": _format_int(result.elapsed_ms or 0),
                "status": result.status,
            }
        )
    _print_table(
        rows,
        [
            "harness",
            "files",
            "inserted",
            "linked",
            "scope_excluded",
            "skipped",
            "fingerprint_ms",
            "scan_ms",
            "db_write_ms",
            "elapsed_ms",
            "status",
        ],
        {
            "harness": "harness",
            "files": "files",
            "inserted": "inserted",
            "linked": "linked",
            "scope_excluded": "scope excl",
            "skipped": "skipped",
            "fingerprint_ms": "fingerprint ms",
            "scan_ms": "scan ms",
            "db_write_ms": "db ms",
            "elapsed_ms": "total ms",
            "status": "status",
        },
        rich_output=False,
        numeric_columns={
            "files",
            "inserted",
            "linked",
            "scope_excluded",
            "skipped",
            "fingerprint_ms",
            "scan_ms",
            "db_write_ms",
            "elapsed_ms",
        },
    )
