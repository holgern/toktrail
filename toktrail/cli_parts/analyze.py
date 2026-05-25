from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer

from toktrail.api.analysis import session_cache_analysis as session_cache_analysis_api
from toktrail.api.analysis import session_digest as session_digest_api
from toktrail.api.analysis import session_report as session_report_api
from toktrail.api.analysis import (
    session_tool_call_analysis as session_tool_call_analysis_api,
)
from toktrail.errors import ToktrailError


@dataclass(frozen=True)
class AnalyzeRuntime:
    resolve_state_db: Callable[[typer.Context], Path]
    resolve_config_path: Callable[[typer.Context], Path]
    exit_with_error: Callable[[str], None]
    print_session_cache_analysis_report: Callable[..., None]
    print_session_digest: Callable[..., None]
    print_session_compact_report: Callable[..., None]
    print_session_tool_call_report: Callable[..., None]


_RUNTIME: AnalyzeRuntime | None = None


def configure_analyze_runtime(runtime: AnalyzeRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def _runtime() -> AnalyzeRuntime:
    if _RUNTIME is None:
        msg = "analyze runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME


def _resolve_state_db(ctx: typer.Context) -> Path:
    return _runtime().resolve_state_db(ctx)


def _resolve_config_path(ctx: typer.Context) -> Path:
    return _runtime().resolve_config_path(ctx)


def _exit_with_error(message: str) -> None:
    _runtime().exit_with_error(message)


def analyze_cache_impl(
    ctx: typer.Context,
    *,
    harness: str,
    source_session_id: str | None,
    source_path: Path | None,
    last: bool,
    json_output: bool,
    utc: bool,
    refresh: bool,
    use_active_run: bool,
    cluster_tolerance: float,
    include_calls: bool,
    rich_output: bool,
) -> None:
    try:
        report = session_cache_analysis_api(
            db_path=_resolve_state_db(ctx),
            config_path=_resolve_config_path(ctx),
            harness=harness,
            source_session_id=source_session_id,
            last=last,
            source_path=source_path,
            refresh=refresh,
            use_active_run=use_active_run,
            cluster_tolerance=cluster_tolerance,
            include_calls=include_calls,
        )
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(report.as_dict(include_calls=include_calls), indent=2))
        return

    _runtime().print_session_cache_analysis_report(
        report,
        utc=utc,
        include_calls=include_calls,
        rich_output=rich_output,
    )


def analyze_session_impl(
    ctx: typer.Context,
    *,
    harness: str,
    source_session_id: str | None,
    source_path: Path | None,
    last: bool,
    bad_calls: bool,
    all_tool_calls: bool,
    show_output: bool,
    tool_limit: int | None,
    max_snippet_chars: int,
    raw_tool_json: bool,
    json_output: bool,
    utc: bool,
    refresh: bool,
    persist: bool,
    include_snippets: bool,
    details: bool,
    rich_output: bool,
) -> None:
    if bad_calls or all_tool_calls:
        if raw_tool_json and not json_output:
            _exit_with_error("--raw-tool-json requires --json.")

        try:
            report = session_tool_call_analysis_api(
                harness=harness,
                db_path=_resolve_state_db(ctx),
                config_path=_resolve_config_path(ctx),
                source_path=source_path,
                source_session_id=source_session_id,
                last=last,
                bad_only=not all_tool_calls,
                include_output=show_output,
                include_raw_json=raw_tool_json,
                limit=tool_limit,
                max_snippet_chars=max_snippet_chars,
            )
        except (ToktrailError, OSError, ValueError) as exc:
            _exit_with_error(str(exc))

        if json_output:
            payload = report.as_dict(
                include_calls=True,
                include_output=show_output,
                include_raw_json=raw_tool_json,
            )
            typer.echo(json.dumps(payload, indent=2))
            return

        _runtime().print_session_tool_call_report(
            report,
            bad_only=not all_tool_calls,
            utc=utc,
            show_output=show_output,
            rich_output=rich_output,
        )
        return

    try:
        if details:
            digest = session_digest_api(
                db_path=_resolve_state_db(ctx),
                config_path=_resolve_config_path(ctx),
                harness=harness,
                source_session_id=source_session_id,
                last=last,
                source_path=source_path,
                refresh=refresh,
                persist=persist,
                include_snippets=include_snippets,
            )
        else:
            report = session_report_api(
                db_path=_resolve_state_db(ctx),
                config_path=_resolve_config_path(ctx),
                harness=harness,
                source_session_id=source_session_id,
                last=last,
                source_path=source_path,
                refresh=refresh,
                persist=persist,
                include_snippets=include_snippets,
            )
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        if details:
            payload = digest.as_dict(include_artifacts=rich_output)
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(json.dumps(report.as_dict(), indent=2))
        return

    if details:
        _runtime().print_session_digest(digest, utc=utc, rich_output=rich_output)
    else:
        _runtime().print_session_compact_report(report, utc=utc)
