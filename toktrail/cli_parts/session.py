from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import typer

from toktrail.api.analysis import session_digest as session_digest_api
from toktrail.api.analysis import session_report as session_report_api
from toktrail.api.analysis import (
    session_tool_call_analysis as session_tool_call_analysis_api,
)
from toktrail.api.models import SessionDigest, UsageSessionRow
from toktrail.api.reports import get_usage_session as get_usage_session_api
from toktrail.api.reports import usage_sessions_report as usage_sessions_report_api
from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.cli_parts.table import _print_table
from toktrail.cli_parts.usage import (
    _add_digest_summaries_to_payload,
    _digest_health_summary,
    _format_model_list,
    _session_digest_lookup,
)
from toktrail.errors import ToktrailError
from toktrail.formatting import format_epoch_ms_compact
from toktrail.periods import resolve_time_range
from toktrail.session_digests import public_digest_from_internal

session_app = typer.Typer(help="Stable source-session automation commands.")


@dataclass(frozen=True)
class SessionRuntime:
    resolve_state_db: Callable[[typer.Context], Path]
    resolve_config_path: Callable[[typer.Context], Path]
    open_toktrail_connection: Callable[[typer.Context], sqlite3.Connection]
    exit_with_error: Callable[[str], None]
    print_session_digest: Callable[..., None]
    print_session_compact_report: Callable[..., None]
    print_session_tool_call_report: Callable[..., None]


_RUNTIME: SessionRuntime | None = None


def configure_session_runtime(runtime: SessionRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def _runtime() -> SessionRuntime:
    if _RUNTIME is None:
        msg = "session runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME


def _resolve_state_db(ctx: typer.Context) -> Path:
    return _runtime().resolve_state_db(ctx)


def _resolve_config_path(ctx: typer.Context) -> Path:
    return _runtime().resolve_config_path(ctx)


def _open_toktrail_connection(ctx: typer.Context) -> sqlite3.Connection:
    return _runtime().open_toktrail_connection(ctx)


def _exit_with_error(message: str) -> None:
    _runtime().exit_with_error(message)


def _resolve_session_selection(
    ctx: typer.Context,
    *,
    session_key: str | None,
    last: bool,
) -> UsageSessionRow:
    if session_key is None and not last:
        _exit_with_error("Provide a session key or use --last.")
    if session_key is not None and last:
        _exit_with_error("Use either a session key or --last, not both.")
    if session_key is not None:
        return get_usage_session_api(
            _resolve_state_db(ctx),
            session_key=session_key,
            config_path=_resolve_config_path(ctx),
        )
    report = usage_sessions_report_api(
        _resolve_state_db(ctx),
        config_path=_resolve_config_path(ctx),
        limit=1,
        order="desc",
    )
    if not report.sessions:
        _exit_with_error("No sessions found.")
    return report.sessions[0]


def _session_digest_lookup_for_rows(
    ctx: typer.Context, sessions: tuple[UsageSessionRow, ...]
) -> dict[tuple[str, str, str], object]:
    conn = _open_toktrail_connection(ctx)
    try:
        return _session_digest_lookup(conn, sessions)
    finally:
        conn.close()


def _persisted_session_digest(
    ctx: typer.Context, session: UsageSessionRow
) -> SessionDigest | None:
    if session.origin_machine_id is None:
        return None
    from toktrail.db import get_source_session_digest

    conn = _open_toktrail_connection(ctx)
    try:
        digest = get_source_session_digest(
            conn,
            origin_machine_id=session.origin_machine_id,
            harness=session.harness,
            source_session_id=session.source_session_id,
        )
    finally:
        conn.close()
    if digest is None:
        return None
    return cast(SessionDigest, public_digest_from_internal(digest))


def _echo_session_row(
    session: UsageSessionRow, *, digest: SessionDigest | None
) -> None:
    typer.echo(session.key)
    typer.echo(f"  Machine: {session.machine_label}")
    typer.echo(f"  Area:    {session.area_path or 'unassigned'}")
    typer.echo(
        f"  Window:  {format_epoch_ms_compact(session.first_ms)} -> "
        f"{format_epoch_ms_compact(session.last_ms)}"
    )
    if session.cwd:
        typer.echo(f"  CWD:     {session.cwd}")
    elif session.source_dir:
        typer.echo(f"  CWD:     {session.source_dir}")
    if session.source_paths:
        typer.echo(f"  Source:  {session.source_paths[0]}")
    typer.echo(f"  Models:  {_format_model_list(session.models, rich_output=False)}")
    typer.echo(
        "  Tokens:  "
        f"input={_format_int(session.tokens.input)} "
        f"output={_format_int(session.tokens.output)} "
        f"reasoning={_format_int(session.tokens.reasoning)} "
        f"cache_read={_format_int(session.tokens.cache_read)} "
        f"cache_write={_format_int(session.tokens.cache_write)} "
        f"total={_format_int(session.tokens.total)}"
    )
    typer.echo(
        "  Costs:   "
        f"source={_format_cost(session.costs.source_cost_usd)} "
        f"actual={_format_cost(session.costs.actual_cost_usd)} "
        f"virtual={_format_cost(session.costs.virtual_cost_usd)}"
    )
    if digest is not None:
        typer.echo(f"  Summary: {digest.summary.one_line or '-'}")
        typer.echo(f"  Health:  {_digest_health_summary(digest)}")


def _resolve_list_period(
    *,
    today: bool,
    since: str | None,
    until: str | None,
) -> tuple[str | None, int | None, int | None]:
    if today and (since is not None or until is not None):
        msg = "Use either --today or explicit --since/--until bounds."
        raise ValueError(msg)
    resolved = resolve_time_range(
        period="today" if today else None,
        since_text=since,
        until_text=until,
    )
    return resolved.period, resolved.since_ms, resolved.until_ms


@session_app.command("list")
def session_list(
    ctx: typer.Context,
    today: bool = typer.Option(False, "--today", help="Limit to the current day."),
    since: str | None = typer.Option(None, "--since", help="Inclusive time boundary."),
    until: str | None = typer.Option(None, "--until", help="Exclusive time boundary."),
    area: str | None = typer.Option(None, "--area", help="Filter by area path."),
    harness: str | None = typer.Option(None, "--harness", help="Filter by harness."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        period, since_ms, until_ms = _resolve_list_period(
            today=today, since=since, until=until
        )
        report = usage_sessions_report_api(
            _resolve_state_db(ctx),
            config_path=_resolve_config_path(ctx),
            area=area,
            harness=harness,
            since_ms=since_ms if period is None else None,
            until_ms=until_ms if period is None else None,
            period=period,
            limit=None,
            order="desc",
        )
        digest_lookup = _session_digest_lookup_for_rows(ctx, report.sessions)
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = report.as_dict()
        _add_digest_summaries_to_payload(payload, digest_lookup)
        typer.echo(json.dumps(payload, indent=2))
        return

    title = "toktrail session list"
    if period is not None:
        title += f" ({period})"
    typer.echo(title)
    if not report.sessions:
        typer.echo("No sessions.")
        return

    rows = []
    for session in report.sessions:
        digest = digest_lookup.get(
            (
                session.origin_machine_id or "",
                session.harness,
                session.source_session_id,
            )
        )
        summary = "-"
        health = "-"
        if digest is not None:
            summary = getattr(getattr(digest, "summary", None), "one_line", "-") or "-"
            health = _digest_health_summary(digest)
        rows.append(
            {
                "session": session.key,
                "area": session.area_path or "unassigned",
                "last": format_epoch_ms_compact(session.last_ms),
                "msgs": _format_int(session.message_count),
                "total": _format_int(session.tokens.total),
                "models": _format_model_list(session.models, rich_output=False),
                "summary": summary,
                "health": health,
            }
        )
    _print_table(
        rows,
        ["session", "area", "last", "msgs", "total", "models", "summary", "health"],
        {
            "session": "session",
            "area": "area",
            "last": "last",
            "msgs": "msgs",
            "total": "total",
            "models": "models",
            "summary": "summary",
            "health": "health",
        },
        rich_output=False,
        numeric_columns={"msgs", "total"},
        wrap_columns={"session", "area", "models", "summary", "health"},
        max_widths={
            "session": 48,
            "area": 32,
            "models": 40,
            "summary": 40,
            "health": 32,
        },
    )


@session_app.command("get")
def session_get(
    ctx: typer.Context,
    session_key: str = typer.Argument(..., help="machine/harness/source_session_id"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        session = get_usage_session_api(
            _resolve_state_db(ctx),
            session_key=session_key,
            config_path=_resolve_config_path(ctx),
        )
        digest = _persisted_session_digest(ctx, session)
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = session.as_dict()
        payload["type"] = "usage_session"
        payload["digest_available"] = digest is not None
        if digest is not None:
            payload["summary"] = digest.summary.as_dict()
            payload["tool_health"] = digest.tool_health.as_dict()
            payload["health"] = (
                None if digest.health is None else digest.health.as_dict()
            )
        typer.echo(json.dumps(payload, indent=2))
        return

    _echo_session_row(session, digest=digest)


@session_app.command("health")
def session_health(
    ctx: typer.Context,
    session_key: str | None = typer.Argument(
        None, help="machine/harness/source_session_id"
    ),
    last: bool = typer.Option(False, "--last", help="Use the newest imported session."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        session = _resolve_session_selection(ctx, session_key=session_key, last=last)
        digest = session_digest_api(
            db_path=_resolve_state_db(ctx),
            config_path=_resolve_config_path(ctx),
            session_key=session.key,
            refresh=False,
            persist=False,
        )
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = digest.as_dict()
        payload["key"] = session.key
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Key: {session.key}")
    _runtime().print_session_digest(digest, utc=False, rich_output=False)


@session_app.command("usage")
def session_usage(
    ctx: typer.Context,
    session_key: str | None = typer.Argument(
        None, help="machine/harness/source_session_id"
    ),
    last: bool = typer.Option(False, "--last", help="Use the newest imported session."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        session = _resolve_session_selection(ctx, session_key=session_key, last=last)
        report = session_report_api(
            db_path=_resolve_state_db(ctx),
            config_path=_resolve_config_path(ctx),
            session_key=session.key,
            refresh=False,
            persist=False,
        )
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = report.as_dict()
        payload["key"] = session.key
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Key: {session.key}")
    _runtime().print_session_compact_report(report, utc=False)


@session_app.command("tool-calls")
def session_tool_calls(
    ctx: typer.Context,
    session_key: str | None = typer.Argument(
        None, help="machine/harness/source_session_id"
    ),
    last: bool = typer.Option(False, "--last", help="Use the newest imported session."),
    bad_only: bool = typer.Option(False, "--bad-only", help="Only include bad calls."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        session = _resolve_session_selection(ctx, session_key=session_key, last=last)
        report = session_tool_call_analysis_api(
            db_path=_resolve_state_db(ctx),
            config_path=_resolve_config_path(ctx),
            session_key=session.key,
            bad_only=bad_only,
        )
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = report.as_dict(include_calls=True)
        payload["key"] = session.key
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Key: {session.key}")
    _runtime().print_session_tool_call_report(
        report,
        bad_only=bad_only,
        utc=False,
        rich_output=False,
    )


@session_app.command("search", help="Search the opt-in session index.")
def session_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query."),
    harness: str | None = typer.Option(None, "--harness"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    from toktrail.config import load_resolved_toktrail_config
    from toktrail.db import connect
    from toktrail.session_search import search_session_index

    config = load_resolved_toktrail_config(_resolve_config_path(ctx))
    if not config.runtime.session_index.enabled:
        _exit_with_error("Session index is disabled. Enable [session_index] in config.")

    db_path = _resolve_state_db(ctx)
    conn = connect(db_path)
    try:
        results = search_session_index(
            conn,
            query,
            harness=harness,
        )
    finally:
        conn.close()

    if json_output:
        typer.echo(json.dumps([r.as_dict() for r in results], indent=2))
        return
    if not results:
        typer.echo("No results.")
        return
    for r in results:
        typer.echo(
            f"{r.harness}/{r.source_session_id} [{r.kind}] {r.content_redacted[:80]}"
        )
