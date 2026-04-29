from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from toktrail.adapters.copilot import scan_copilot_file
from toktrail.adapters.opencode import (
    list_opencode_sessions,
    scan_opencode_sqlite,
)
from toktrail.adapters.pi import list_pi_sessions, scan_pi_path
from toktrail.db import (
    connect,
    create_tracking_session,
    end_tracking_session,
    get_active_tracking_session,
    get_tracking_session,
    insert_usage_events,
    list_tracking_sessions,
    migrate,
    summarize_tracking_session,
)
from toktrail.paths import (
    resolve_copilot_file_path,
    resolve_opencode_db_path,
    resolve_pi_sessions_path,
    resolve_toktrail_db_path,
)

app = typer.Typer(help="Track harness token usage in local SQLite sessions.")
import_app = typer.Typer(help="Import usage from external harnesses.")
watch_app = typer.Typer(help="Watch external harnesses and import new usage.")
opencode_app = typer.Typer(help="Inspect OpenCode source data.")
pi_app = typer.Typer(help="Inspect Pi source data.")

app.add_typer(import_app, name="import")
app.add_typer(watch_app, name="watch")
app.add_typer(opencode_app, name="opencode")
app.add_typer(pi_app, name="pi")


@dataclass(frozen=True)
class ImportExecutionResult:
    harness: str
    source_path: Path
    tracking_session_id: int
    rows_seen: int
    rows_imported: int
    rows_skipped: int


DbPathOption = Annotated[
    Path | None,
    typer.Option("--db", help="Override toktrail DB path."),
]
SessionArgument = Annotated[int | None, typer.Argument()]
SessionOption = Annotated[int | None, typer.Option("--session")]
SourceSessionOption = Annotated[str | None, typer.Option("--source-session")]
NameOption = Annotated[str | None, typer.Option("--name")]
JsonOption = Annotated[bool, typer.Option("--json")]
OpenCodeDbOption = Annotated[
    Path | None,
    typer.Option("--opencode-db", "--db", help="Override OpenCode DB path."),
]
CopilotFileOption = Annotated[
    Path | None,
    typer.Option("--copilot-file", "--file", help="Copilot CLI OTEL JSONL file."),
]
PiPathOption = Annotated[
    Path | None,
    typer.Option("--pi-path", "--path", help="Override Pi sessions file or directory."),
]
SinceStartOption = Annotated[bool, typer.Option("--since-start")]
NoRawOption = Annotated[bool, typer.Option("--no-raw")]
IntervalOption = Annotated[float, typer.Option("--interval", min=0.1)]


@app.callback()
def main(
    ctx: typer.Context,
    db_path: DbPathOption = None,
) -> None:
    ctx.obj = {"db_path": db_path}


@app.command()
def init(ctx: typer.Context) -> None:
    db_path = _resolve_state_db(ctx)
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    typer.echo(f"Initialized toktrail database: {db_path}")


@app.command()
def start(
    ctx: typer.Context,
    name: NameOption = None,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        session_id = create_tracking_session(conn, name)
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    typer.echo(f"Started tracking session {session_id}: {name or '(unnamed)'}")


@app.command()
def stop(
    ctx: typer.Context,
    session_id: SessionArgument = None,
) -> None:
    conn = _open_toktrail_connection(ctx)
    session = None
    try:
        selected_session_id = session_id
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active tracking session found.")

        session = get_tracking_session(conn, selected_session_id)
        if session is None:
            _exit_with_error(f"Tracking session not found: {selected_session_id}")
        end_tracking_session(conn, selected_session_id)
    finally:
        conn.close()
    typer.echo(
        f"Stopped tracking session {selected_session_id}: {session.name or '(unnamed)'}"
    )


@app.command()
def status(
    ctx: typer.Context,
    session_id: SessionArgument = None,
    json_output: JsonOption = False,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        selected_session_id = session_id
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active tracking session found.")

        report = summarize_tracking_session(conn, selected_session_id)
    finally:
        conn.close()

    if json_output:
        typer.echo(json.dumps(report.as_dict(), indent=2))
        return

    typer.echo(
        f"toktrail session {report.session.id}: {report.session.name or '(unnamed)'}"
    )
    typer.echo("")
    typer.echo("Totals")
    typer.echo(f"  input:       {_format_int(report.totals.tokens.input)}")
    typer.echo(f"  output:      {_format_int(report.totals.tokens.output)}")
    typer.echo(f"  reasoning:   {_format_int(report.totals.tokens.reasoning)}")
    typer.echo(f"  cache read:  {_format_int(report.totals.tokens.cache_read)}")
    typer.echo(f"  cache write: {_format_int(report.totals.tokens.cache_write)}")
    typer.echo(f"  total:       {_format_int(report.totals.tokens.total)}")
    typer.echo(f"  cost:        {_format_cost(report.totals.cost_usd)}")

    typer.echo("")
    typer.echo("By harness")
    if report.by_harness:
        for harness_row in report.by_harness:
            typer.echo(
                f"  {harness_row.harness:<12}"
                f"{_format_int(harness_row.total_tokens):>12} tokens   "
                f"{_format_cost(harness_row.cost_usd)}"
            )
    else:
        typer.echo("  (none)")

    typer.echo("")
    typer.echo("By model")
    if report.by_model:
        for model_row in report.by_model:
            typer.echo(
                f"  {model_row.provider_id}/{model_row.model_id:<24}"
                f"{_format_int(model_row.total_tokens):>12} tokens   "
                f"{_format_cost(model_row.cost_usd)}"
            )
    else:
        typer.echo("  (none)")

    typer.echo("")
    typer.echo("By agent")
    if report.by_agent:
        for agent_row in report.by_agent:
            typer.echo(
                f"  {agent_row.agent:<12}"
                f"{_format_int(agent_row.total_tokens):>12} tokens   "
                f"{_format_cost(agent_row.cost_usd)}"
            )
    else:
        typer.echo("  (none)")


@app.command()
def sessions(ctx: typer.Context) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        tracking_sessions = list_tracking_sessions(conn)
    finally:
        conn.close()

    if not tracking_sessions:
        typer.echo("No tracking sessions found.")
        return

    for session in tracking_sessions:
        state = "active" if session.ended_at_ms is None else "stopped"
        typer.echo(
            f"{session.id}\t{state}\t{session.name or '(unnamed)'}\t"
            f"started={session.started_at_ms}\tended={session.ended_at_ms}"
        )


@import_app.command("opencode")
def import_opencode(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    opencode_db: OpenCodeDbOption = None,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    result = _run_opencode_import(
        ctx,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        opencode_db=opencode_db,
        since_start=since_start,
        no_raw=no_raw,
    )
    _print_import_result(result)


@import_app.command("copilot")
def import_copilot(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    copilot_file: CopilotFileOption = None,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    result = _run_copilot_import(
        ctx,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        copilot_file=copilot_file,
        since_start=since_start,
        no_raw=no_raw,
    )
    _print_import_result(result)


@import_app.command("pi")
def import_pi(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    pi_path: PiPathOption = None,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    result = _run_pi_import(
        ctx,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        pi_path=pi_path,
        since_start=since_start,
        no_raw=no_raw,
    )
    _print_import_result(result)


@watch_app.command("opencode")
def watch_opencode(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    opencode_db: OpenCodeDbOption = None,
    interval: IntervalOption = 2.0,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    total_seen = 0
    total_imported = 0
    total_skipped = 0
    try:
        while True:
            result = _run_opencode_import(
                ctx,
                tracking_session_id=session_id,
                source_session_id=source_session_id,
                opencode_db=opencode_db,
                since_start=since_start,
                no_raw=no_raw,
            )
            total_seen += result.rows_seen
            total_imported += result.rows_imported
            total_skipped += result.rows_skipped
            _print_import_result(result)
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("")
        typer.echo("Stopped watching OpenCode.")
        typer.echo(f"  rows seen:     {total_seen}")
        typer.echo(f"  rows imported: {total_imported}")
        typer.echo(f"  rows skipped:  {total_skipped}")


@watch_app.command("copilot")
def watch_copilot(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    copilot_file: CopilotFileOption = None,
    interval: IntervalOption = 2.0,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    total_seen = 0
    total_imported = 0
    total_skipped = 0
    try:
        while True:
            result = _run_copilot_import(
                ctx,
                tracking_session_id=session_id,
                source_session_id=source_session_id,
                copilot_file=copilot_file,
                since_start=since_start,
                no_raw=no_raw,
            )
            total_seen += result.rows_seen
            total_imported += result.rows_imported
            total_skipped += result.rows_skipped
            _print_import_result(result)
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("")
        typer.echo("Stopped watching Copilot.")
        typer.echo(f"  rows seen:     {total_seen}")
        typer.echo(f"  rows imported: {total_imported}")
        typer.echo(f"  rows skipped:  {total_skipped}")


@watch_app.command("pi")
def watch_pi(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    pi_path: PiPathOption = None,
    interval: IntervalOption = 2.0,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    total_seen = 0
    total_imported = 0
    total_skipped = 0
    try:
        while True:
            result = _run_pi_import(
                ctx,
                tracking_session_id=session_id,
                source_session_id=source_session_id,
                pi_path=pi_path,
                since_start=since_start,
                no_raw=no_raw,
            )
            total_seen += result.rows_seen
            total_imported += result.rows_imported
            total_skipped += result.rows_skipped
            _print_import_result(result)
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("")
        typer.echo("Stopped watching Pi.")
        typer.echo(f"  rows seen:     {total_seen}")
        typer.echo(f"  rows imported: {total_imported}")
        typer.echo(f"  rows skipped:  {total_skipped}")


@opencode_app.command("sessions")
def opencode_sessions(
    opencode_db: OpenCodeDbOption = None,
) -> None:
    source_path = resolve_opencode_db_path(opencode_db)
    if not source_path.exists():
        _exit_with_error(f"OpenCode database not found: {source_path}")

    sessions_summary = list_opencode_sessions(source_path)
    if not sessions_summary:
        typer.echo("No importable OpenCode assistant messages found.")
        return

    for session in sessions_summary:
        typer.echo(
            f"{session.source_session_id}\tfirst={session.first_created_ms}\t"
            f"last={session.last_created_ms}\tmessages={session.assistant_message_count}\t"
            f"tokens={session.tokens.total}\tcost={_format_cost(session.cost_usd)}"
        )


@pi_app.command("sessions")
def pi_sessions(
    pi_path: PiPathOption = None,
) -> None:
    source_path = resolve_pi_sessions_path(pi_path)
    if not source_path.exists():
        _exit_with_error(f"Pi sessions path not found: {source_path}")

    sessions_summary = list_pi_sessions(source_path)
    if not sessions_summary:
        typer.echo("No importable Pi assistant messages found.")
        return

    for session in sessions_summary:
        typer.echo(
            f"{session.source_session_id}\tfirst={session.first_created_ms}\t"
            f"last={session.last_created_ms}\tmessages={session.assistant_message_count}\t"
            f"tokens={session.tokens.total}\tcost={_format_cost(session.cost_usd)}"
        )


def cli_main() -> None:
    app()


def _run_opencode_import(
    ctx: typer.Context,
    *,
    tracking_session_id: int | None,
    source_session_id: str | None,
    opencode_db: Path | None,
    since_start: bool,
    no_raw: bool,
) -> ImportExecutionResult:
    conn = _open_toktrail_connection(ctx)
    try:
        source_path = resolve_opencode_db_path(opencode_db)
        if not source_path.exists():
            _exit_with_error(f"OpenCode database not found: {source_path}")

        selected_session_id = tracking_session_id
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active tracking session found.")

        session = get_tracking_session(conn, selected_session_id)
        if session is None:
            _exit_with_error(f"Tracking session not found: {selected_session_id}")

        scan = scan_opencode_sqlite(
            source_path,
            source_session_id=source_session_id,
            include_raw_json=not no_raw,
        )
        since_ms = session.started_at_ms if since_start else None
        filtered_events = [
            event
            for event in scan.events
            if since_ms is None or event.created_ms >= since_ms
        ]
        insert_result = insert_usage_events(
            conn,
            selected_session_id,
            filtered_events,
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
        harness="OpenCode",
        source_path=source_path,
        tracking_session_id=selected_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=insert_result.rows_inserted,
        rows_skipped=rows_skipped,
    )


def _run_copilot_import(
    ctx: typer.Context,
    *,
    tracking_session_id: int | None,
    source_session_id: str | None,
    copilot_file: Path | None,
    since_start: bool,
    no_raw: bool,
) -> ImportExecutionResult:
    conn = _open_toktrail_connection(ctx)
    try:
        source_path = resolve_copilot_file_path(copilot_file)
        if source_path is None:
            _exit_with_error(
                "Copilot telemetry file not provided. "
                "Use --copilot-file or TOKTRAIL_COPILOT_FILE."
            )
        if not source_path.exists():
            _exit_with_error(f"Copilot telemetry file not found: {source_path}")

        selected_session_id = tracking_session_id
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active tracking session found.")

        session = get_tracking_session(conn, selected_session_id)
        if session is None:
            _exit_with_error(f"Tracking session not found: {selected_session_id}")

        scan = scan_copilot_file(
            source_path,
            source_session_id=source_session_id,
            include_raw_json=not no_raw,
        )
        since_ms = session.started_at_ms if since_start else None
        filtered_events = [
            event
            for event in scan.events
            if since_ms is None or event.created_ms >= since_ms
        ]
        insert_result = insert_usage_events(
            conn,
            selected_session_id,
            filtered_events,
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
        harness="Copilot",
        source_path=source_path,
        tracking_session_id=selected_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=insert_result.rows_inserted,
        rows_skipped=rows_skipped,
    )


def _run_pi_import(
    ctx: typer.Context,
    *,
    tracking_session_id: int | None,
    source_session_id: str | None,
    pi_path: Path | None,
    since_start: bool,
    no_raw: bool,
) -> ImportExecutionResult:
    conn = _open_toktrail_connection(ctx)
    try:
        source_path = resolve_pi_sessions_path(pi_path)
        if not source_path.exists():
            _exit_with_error(f"Pi sessions path not found: {source_path}")

        selected_session_id = tracking_session_id
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active tracking session found.")

        session = get_tracking_session(conn, selected_session_id)
        if session is None:
            _exit_with_error(f"Tracking session not found: {selected_session_id}")

        scan = scan_pi_path(
            source_path,
            source_session_id=source_session_id,
            include_raw_json=not no_raw,
        )
        since_ms = session.started_at_ms if since_start else None
        filtered_events = [
            event
            for event in scan.events
            if since_ms is None or event.created_ms >= since_ms
        ]
        insert_result = insert_usage_events(
            conn,
            selected_session_id,
            filtered_events,
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
        harness="Pi",
        source_path=source_path,
        tracking_session_id=selected_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=insert_result.rows_inserted,
        rows_skipped=rows_skipped,
    )


def _print_import_result(
    result: ImportExecutionResult,
) -> None:
    typer.echo(f"Imported {result.harness} usage:")
    typer.echo(f"  source path: {result.source_path}")
    typer.echo(f"  tracking session: {result.tracking_session_id}")
    typer.echo(f"  rows seen: {result.rows_seen}")
    typer.echo(f"  rows imported: {result.rows_imported}")
    typer.echo(f"  rows skipped: {result.rows_skipped}")


def _resolve_state_db(ctx: typer.Context) -> Path:
    root_obj = ctx.find_root().obj or {}
    db_path = root_obj.get("db_path")
    if db_path is not None and not isinstance(db_path, Path):
        msg = "Unexpected CLI state for --db."
        raise TypeError(msg)
    return resolve_toktrail_db_path(db_path)


def _open_toktrail_connection(ctx: typer.Context) -> sqlite3.Connection:
    db_path = _resolve_state_db(ctx)
    conn = connect(db_path)
    migrate(conn)
    return conn


def _exit_with_error(message: str) -> NoReturn:
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_cost(value: float) -> str:
    return f"${value:.2f}"
