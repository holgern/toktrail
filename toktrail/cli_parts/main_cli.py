# mypy: ignore-errors
from __future__ import annotations

import datetime
import json
import os
import shlex
import sqlite3
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, NoReturn, cast

import typer

if TYPE_CHECKING:
    pass

from toktrail.adapters.base import SourceSessionSummary
from toktrail.adapters.registry import get_harness
from toktrail.adapters.summary import (
    summarize_event_totals,
    summarize_events_by_activity,
    summarize_events_by_model,
)
from toktrail.api.environment import prepare_environment as prepare_api_environment
from toktrail.api.imports import import_configured_usage as import_configured_usage_api
from toktrail.api.models import (
    ImportUsageResult,
    SessionCacheAnalysisReport,
    SessionCompactReport,
    SessionDigest,
    SessionToolCallReport,
    StatuslineCache,
    StatuslineReport,
)
from toktrail.api.models import (
    RunScope as PublicRunScope,
)
from toktrail.api.reports import stats_report as stats_report_api
from toktrail.api.sessions import list_runs
from toktrail.api.statusline import statusline_report as statusline_report_api
from toktrail.cli_parts import analyze as analyze_parts
from toktrail.cli_parts import insights as insights_parts
from toktrail.cli_parts import prices as prices_parts
from toktrail.cli_parts import refresh as refresh_parts
from toktrail.cli_parts import session as session_parts
from toktrail.cli_parts import sources as sources_parts
from toktrail.cli_parts import statusline as statusline_parts
from toktrail.cli_parts import subscriptions as subscriptions_parts
from toktrail.cli_parts import usage as usage_parts
from toktrail.cli_parts import watch as watch_parts
from toktrail.cli_parts.area import register_area_commands
from toktrail.cli_parts.filters import (
    _aliases_from_row,
    _as_float_or_none,
    _filter_model_rows,
    _filter_unconfigured_models,
    _normalize_report_display_filter,
)
from toktrail.cli_parts.formatting import (
    _format_cost,
    _format_cost_or_dash,
    _format_cost_precise,
    _format_int,
    _format_percent,
    _format_price,
    _format_ratio_percent,
    _format_signed_int,
    _format_token_delta,
)
from toktrail.cli_parts.machines import register_machine_commands
from toktrail.cli_parts.table import (
    _print_model_table,
    _print_table,
)
from toktrail.cli_sync import sync_app
from toktrail.config import (
    DEFAULT_TEMPLATE_NAME,
    ContextWindowConfig,
    CostingConfig,
    LoadedCostingConfig,
    LoadedMachineConfig,
    LoadedToktrailConfig,
    Price,
    StatuslineConfig,
    load_machine_config,
    load_resolved_costing_config,
    load_resolved_toktrail_config,
    normalize_identity,
    render_config_template,
    render_prices_template,
    render_subscriptions_template,
    summarize_costing_config,
)
from toktrail.db import (
    apply_local_machine_config,
    archive_tracking_session,
    clear_skipped_sources,
    connect,
    create_tracking_session,
    end_tracking_session,
    get_active_tracking_session,
    get_state_metadata,
    get_tracking_session,
    list_skipped_sources,
    migrate,
    resolve_machine_selector,
    summarize_subscription_usage,
    summarize_usage,
    unarchive_tracking_session,
)
from toktrail.errors import InvalidAPIUsageError, ToktrailError
from toktrail.formatting import format_epoch_ms_compact
from toktrail.models import (
    RunScope,
    UsageEvent,
    normalize_thinking_level,
)
from toktrail.paths import (
    new_copilot_otel_file_path,
    resolve_toktrail_config_path,
    resolve_toktrail_db_path,
    resolve_toktrail_machine_path,
)
from toktrail.reporting import (
    SubscriptionBillingPeriod,
    SubscriptionUsagePeriod,
    SubscriptionUsageReport,
    UsageReportFilter,
)
from toktrail.statusline import (
    StatuslineRequest,
    load_statusline_cache_metadata,
    load_statusline_output_cache,
    statusline_cache_dir,
    statusline_cache_key,
    write_statusline_output_cache,
)

app = typer.Typer(help="Track harness token usage in local SQLite sessions.")
sources_app = typer.Typer(
    invoke_without_command=True,
    help="Inspect configured source paths and source sessions.",
)
run_app = typer.Typer(help="Manage toktrail tracking runs.")
usage_app = typer.Typer(help="Report imported token and cost usage.")
statusline_app = typer.Typer(
    invoke_without_command=True,
    help="Render compact session and quota status lines.",
)
statusline_config_app = typer.Typer(help="Inspect statusline configuration.")
copilot_app = typer.Typer(help="Inspect and run GitHub Copilot CLI tracking.")
config_app = typer.Typer(help="Inspect toktrail configuration files.")
prices_app = typer.Typer(help="Inspect configured and used model pricing.")
subscriptions_app = typer.Typer(help="Inspect provider subscription limits.")
analyze_app = typer.Typer(help="Analyze per-call cache and cost behavior.")
stats_app = typer.Typer(help="Report aggregate usage and session statistics.")
machine_app = typer.Typer(help="Inspect and configure local machine identity.")
area_app = typer.Typer(help="Create and manage hierarchical usage areas.")
insights_cli_app = insights_parts.insights_app

app.add_typer(run_app, name="run")
app.add_typer(session_parts.session_app, name="session")
app.add_typer(sources_app, name="sources")
app.add_typer(usage_app, name="usage")
app.add_typer(statusline_app, name="statusline")
app.add_typer(copilot_app, name="copilot")
app.add_typer(config_app, name="config")
app.add_typer(prices_app, name="prices")
app.add_typer(subscriptions_app, name="subscriptions")
app.add_typer(analyze_app, name="analyze")
app.add_typer(stats_app, name="stats")
app.add_typer(machine_app, name="machine")
app.add_typer(area_app, name="area")
app.add_typer(insights_cli_app, name="insights")
app.add_typer(sync_app, name="sync")
statusline_app.add_typer(statusline_config_app, name="config")

CopilotEnvVar = tuple[str, str]


DbPathOption = Annotated[
    Path | None,
    typer.Option("--db", help="Override toktrail DB path."),
]
ConfigPathOption = Annotated[
    Path | None,
    typer.Option("--config", help="Override toktrail config TOML path."),
]
MachineConfigPathOption = Annotated[
    Path | None,
    typer.Option(
        "--machine-config",
        help="Override machine config TOML path.",
    ),
]
PricesPathOption = Annotated[
    Path | None,
    typer.Option("--prices", help="Override toktrail prices TOML path."),
]
PricesDirOption = Annotated[
    Path | None,
    typer.Option("--prices-dir", help="Override toktrail provider prices directory."),
]
SubscriptionsPathOption = Annotated[
    Path | None,
    typer.Option(
        "--subscriptions",
        help="Override toktrail subscriptions TOML path.",
    ),
]
RunArgument = Annotated[int | None, typer.Argument()]
RunOption = Annotated[int | None, typer.Option("--run", "--run-id")]
SourceSessionOption = Annotated[str | None, typer.Option("--source-session")]
MachineOption = Annotated[
    str | None,
    typer.Option("--machine", help="Filter by machine name or machine id."),
]
NameOption = Annotated[str | None, typer.Option("--name")]
JsonOption = Annotated[bool, typer.Option("--json")]
HarnessOption = Annotated[str | None, typer.Option("--harness")]
HarnessesOption = Annotated[list[str] | None, typer.Option("--harness")]
ProviderOption = Annotated[str | None, typer.Option("--provider")]
ModelOption = Annotated[str | None, typer.Option("--model")]
ThinkingOption = Annotated[str | None, typer.Option("--thinking")]
AgentOption = Annotated[str | None, typer.Option("--agent")]
AreaOption = Annotated[str | None, typer.Option("--area")]
AreaLeafOption = Annotated[str | None, typer.Option("--area-leaf")]
AreaExactOption = Annotated[bool, typer.Option("--area-exact")]
UnassignedAreaOption = Annotated[bool, typer.Option("--unassigned-area")]
SinceMsOption = Annotated[int | None, typer.Option("--since-ms")]
UntilMsOption = Annotated[int | None, typer.Option("--until-ms")]
SourceSessionArgument = Annotated[str | None, typer.Argument()]
LastOption = Annotated[bool, typer.Option("--last")]
BreakdownOption = Annotated[bool, typer.Option("--breakdown")]
UtcOption = Annotated[bool, typer.Option("--utc")]
LimitOption = Annotated[int | None, typer.Option("--limit", min=1)]
SortOption = Annotated[str, typer.Option("--sort")]
ColumnsOption = Annotated[str | None, typer.Option("--columns")]
RichOption = Annotated[
    bool,
    typer.Option(
        "--rich",
        help="Render tables with Rich formatting. Default output stays borderless.",
    ),
]
SplitThinkingOption = Annotated[bool, typer.Option("--split-thinking")]
TimeBoundaryOption = Annotated[str | None, typer.Option("--since")]
UntilBoundaryOption = Annotated[str | None, typer.Option("--until")]
TimezoneOption = Annotated[str | None, typer.Option("--timezone")]
UsagePeriodOption = Annotated[str | None, typer.Option("--period")]
SessionTableOption = Annotated[
    bool, typer.Option("--table", help="Render usage sessions as the legacy table.")
]
SessionTodayOption = Annotated[bool, typer.Option("--today")]
SessionYesterdayOption = Annotated[bool, typer.Option("--yesterday")]
SessionThisWeekOption = Annotated[bool, typer.Option("--this-week")]
SessionLastWeekOption = Annotated[bool, typer.Option("--last-week")]
SessionThisMonthOption = Annotated[bool, typer.Option("--this-month")]
SessionLastMonthOption = Annotated[bool, typer.Option("--last-month")]
PriceStateOption = Annotated[str, typer.Option("--price-state")]
MinMessagesOption = Annotated[int | None, typer.Option("--min-messages")]
MinTokensOption = Annotated[int | None, typer.Option("--min-tokens")]
ReportSortOption = Annotated[str, typer.Option("--sort")]
ReportLimitOption = Annotated[int | None, typer.Option("--limit")]
PriceTableOption = Annotated[str, typer.Option("--table")]
PriceQueryOption = Annotated[str | None, typer.Option("--query")]
CategoryOption = Annotated[str | None, typer.Option("--category")]
ReleaseStatusOption = Annotated[str | None, typer.Option("--release-status")]
PriceSortOption = Annotated[str, typer.Option("--sort")]
AliasesOption = Annotated[bool, typer.Option("--aliases")]
OpenCodeDbOption = Annotated[
    Path | None,
    typer.Option("--opencode-db", "--db", help="Override OpenCode DB path."),
]
CopilotPathOption = Annotated[
    Path | None,
    typer.Option(
        "--copilot-file",
        "--copilot-path",
        "--file",
        "--path",
        help="Copilot CLI OTEL JSONL file or directory.",
    ),
]
PiPathOption = Annotated[
    Path | None,
    typer.Option("--pi-path", "--path", help="Override Pi sessions file or directory."),
]
CodexPathOption = Annotated[
    Path | None,
    typer.Option(
        "--codex-path",
        "--path",
        help="Override Codex sessions file or directory.",
    ),
]
GoosePathOption = Annotated[
    Path | None,
    typer.Option(
        "--goose-db",
        "--goose-path",
        "--path",
        help="Override Goose sessions.db path.",
    ),
]
DroidPathOption = Annotated[
    Path | None,
    typer.Option(
        "--droid-path",
        "--path",
        help="Override Droid sessions file or directory.",
    ),
]
AmpPathOption = Annotated[
    Path | None,
    typer.Option(
        "--amp-path",
        "--path",
        help="Override Amp threads file or directory.",
    ),
]
ClaudePathOption = Annotated[
    Path | None,
    typer.Option(
        "--claude-path",
        "--path",
        help="Override Claude Code projects file or directory.",
    ),
]
VibePathOption = Annotated[
    Path | None,
    typer.Option(
        "--vibe-path",
        "--path",
        help="Override Vibe logs/session directory or meta.json file.",
    ),
]
SinceRunStartOption = Annotated[bool, typer.Option("--since-run-start")]
NoRawOption = Annotated[bool, typer.Option("--no-raw")]
NoRunOption = Annotated[
    bool, typer.Option("--no-run", help="Refresh without a tracking run.")
]
IntervalOption = Annotated[float, typer.Option("--interval", min=0.1)]
CopilotRunArgs = Annotated[list[str], typer.Argument(help="Command to run after --.")]
SourcePathOption = Annotated[Path | None, typer.Option("--source")]
RawOption = Annotated[bool | None, typer.Option("--raw/--no-raw")]
RefreshOption = Annotated[
    bool,
    typer.Option(
        "--refresh/--no-refresh",
        help="Refresh configured harness usage before producing the report.",
    ),
]
RefreshDetailsOption = Annotated[
    bool,
    typer.Option(
        "--refresh-details",
        help="Print a compact refresh summary before the requested output.",
    ),
]
RawModeOption = Annotated[
    bool | None,
    typer.Option(
        "--raw/--no-raw",
        help=(
            "Override imports.include_raw_json for this refresh. Omit to use config."
        ),
    ),
]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Simulate refresh without persisting changes.")
]
RequiredHarnessOption = Annotated[
    str | None, typer.Option("--harness", help="Name of the harness to refresh from.")
]
RequiredSourceOption = Annotated[
    Path | None, typer.Option("--source", help="Path to source data.")
]


@app.callback()
def main(
    ctx: typer.Context,
    db_path: DbPathOption = None,
    config_path: ConfigPathOption = None,
    machine_config_path: MachineConfigPathOption = None,
    prices_path: PricesPathOption = None,
    prices_dir_path: PricesDirOption = None,
    subscriptions_path: SubscriptionsPathOption = None,
) -> None:
    ctx.obj = {
        "db_path": db_path,
        "config_path": config_path,
        "machine_config_path": machine_config_path,
        "prices_path": prices_path,
        "prices_dir_path": prices_dir_path,
        "subscriptions_path": subscriptions_path,
    }


@app.command(help="Initialize the toktrail SQLite database and apply migrations.")
def init(ctx: typer.Context) -> None:
    db_path = _resolve_state_db(ctx)
    conn = connect(db_path)
    migrate(conn)
    loaded_machine = _load_machine_config_or_exit(ctx)
    apply_local_machine_config(conn, loaded_machine.config)
    conn.close()
    typer.echo(f"Initialized toktrail database: {db_path}")


@app.command("tui")
def tui(
    ctx: typer.Context,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
    area: AreaOption = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
    tui_mode: Annotated[
        str | None,
        typer.Option(
            "--tui-mode",
            help="TUI layout mode: auto, full, compact, or micro.",
        ),
    ] = None,
) -> None:
    """Open interactive terminal UI."""
    from toktrail.tui.layout import normalize_tui_mode

    try:
        requested_tui_mode = normalize_tui_mode(
            tui_mode or os.environ.get("TOKTRAIL_TUI_MODE") or "auto"
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
    try:
        from toktrail.tui.app import ToktrailTuiApp
    except ImportError:
        _exit_with_error(
            "Textual mode requires installing toktrail[tui]. "
            "Run: python -m pip install 'toktrail[tui]'"
        )
    app_instance = ToktrailTuiApp(
        db_path=_resolve_state_db(ctx),
        config_path=_resolve_config_path(ctx),
        prices_path=_resolve_prices_path(ctx),
        prices_dir=_resolve_prices_dir(ctx),
        subscriptions_path=_resolve_subscriptions_path(ctx),
        initial_area=area,
        timezone_name=timezone_name,
        utc=utc,
        refresh_on_start=not no_refresh,
        tui_mode=requested_tui_mode,
    )
    app_instance.run()


@run_app.command(help="Start a new tracking run and import usage.")
def start(
    ctx: typer.Context,
    name: NameOption = None,
    harnesses: Annotated[list[str] | None, typer.Option("--harness")] = None,
    provider_ids: Annotated[list[str] | None, typer.Option("--provider")] = None,
    model_ids: Annotated[list[str] | None, typer.Option("--model")] = None,
    source_session_ids: Annotated[
        list[str] | None,
        typer.Option("--source-session"),
    ] = None,
    thinking_levels: Annotated[list[str] | None, typer.Option("--thinking")] = None,
    agents: Annotated[list[str] | None, typer.Option("--agent")] = None,
    json_output: JsonOption = False,
) -> None:
    harnesses = harnesses or []
    provider_ids = provider_ids or []
    model_ids = model_ids or []
    source_session_ids = source_session_ids or []
    thinking_levels = thinking_levels or []
    agents = agents or []
    conn = _open_toktrail_connection(ctx)
    scope = _build_run_scope_or_exit(
        harnesses=harnesses,
        provider_ids=provider_ids,
        model_ids=model_ids,
        source_session_ids=source_session_ids,
        thinking_levels=thinking_levels,
        agents=agents,
    )
    try:
        session_id = create_tracking_session(conn, name, scope=scope)
        run = get_tracking_session(conn, session_id)
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    if run is None:
        _exit_with_error(f"Run not found after creation: {session_id}")
    if json_output:
        typer.echo(json.dumps(run.as_dict(), indent=2))
        return
    typer.echo(f"Started run {session_id}: {name or '(unnamed)'}")
    typer.echo(f"Scope: {_format_scope_summary(run.scope)}")


@run_app.command(help="Stop a tracking run. Optionally refreshes first.")
def stop(
    ctx: typer.Context,
    run_id: RunArgument = None,
    refresh: RefreshOption = True,
    refresh_details: RefreshDetailsOption = False,
    raw: RawModeOption = None,
) -> None:
    conn = _open_toktrail_connection(ctx)
    session = None
    selected_session_id = run_id
    try:
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active run found.")

        session = get_tracking_session(conn, selected_session_id)
        if session is None:
            _exit_with_error(f"Run not found: {selected_session_id}")
    finally:
        conn.close()
    refresh_results = _refresh_before_report(
        ctx,
        enabled=refresh,
        details=refresh_details,
        json_output=False,
        session_id=selected_session_id,
        use_active_session=False,
        include_raw_json=raw,
        since_start=True,
    )
    conn = _open_toktrail_connection(ctx)
    try:
        end_tracking_session(conn, selected_session_id)
    finally:
        conn.close()
    typer.echo(f"Stopped run {selected_session_id}: {session.name or '(unnamed)'}")
    excluded_total = sum(result.rows_scope_excluded for result in refresh_results)
    if excluded_total > 0:
        typer.echo(f"Linked events excluded by scope: {excluded_total}")


@run_app.command(help="Show token and cost breakdown for a run.")
def status(
    ctx: typer.Context,
    run_id: RunArgument = None,
    json_output: JsonOption = False,
    harness: HarnessOption = None,
    source_session_id: SourceSessionOption = None,
    machine: MachineOption = None,
    provider_id: ProviderOption = None,
    model_id: ModelOption = None,
    thinking_level: ThinkingOption = None,
    agent: AgentOption = None,
    since_ms: SinceMsOption = None,
    until_ms: UntilMsOption = None,
    rich_output: RichOption = False,
    split_thinking: SplitThinkingOption = False,
    price_state: PriceStateOption = "all",
    min_messages: MinMessagesOption = None,
    min_tokens: MinTokensOption = None,
    sort: ReportSortOption = "actual",
    limit: ReportLimitOption = None,
    refresh: RefreshOption = True,
    refresh_details: RefreshDetailsOption = False,
    raw: RawModeOption = None,
) -> None:
    costing_config = _load_costing_config_or_exit(ctx)
    try:
        display_filters = _normalize_report_display_filter(
            price_state=price_state,
            min_messages=min_messages,
            min_tokens=min_tokens,
            sort=sort,
            limit=limit,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
    selected_session_id = run_id
    conn = _open_toktrail_connection(ctx)
    try:
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active run found.")
        if get_tracking_session(conn, selected_session_id) is None:
            _exit_with_error(f"Run not found: {selected_session_id}")
    finally:
        conn.close()

    refresh_results = _refresh_before_report(
        ctx,
        enabled=refresh,
        details=refresh_details,
        json_output=json_output,
        harness=harness,
        session_id=selected_session_id,
        use_active_session=False,
        include_raw_json=raw,
        since_start=True,
    )

    conn = _open_toktrail_connection(ctx)
    try:
        report = summarize_usage(
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
            costing_config=costing_config,
        )
    finally:
        conn.close()

    filtered_by_model = _filter_model_rows(
        report.by_model,
        price_state=display_filters.price_state,
        min_messages=display_filters.min_messages,
        min_tokens=display_filters.min_tokens,
        sort=display_filters.sort,
        limit=display_filters.limit,
    )
    filtered_unconfigured = _filter_unconfigured_models(
        report.unconfigured_models,
        price_state=display_filters.price_state,
        min_messages=display_filters.min_messages,
        min_tokens=display_filters.min_tokens,
    )

    if json_output:
        payload = report.as_dict()
        payload["by_model"] = [row.as_dict() for row in filtered_by_model]
        payload["unconfigured_models"] = [
            row.as_dict() for row in filtered_unconfigured
        ]
        payload["display_filters"] = display_filters.as_dict()
        typer.echo(
            json.dumps(
                _wrap_refresh_json_payload(
                    payload,
                    refresh_results=refresh_results,
                    include_refresh=refresh_details,
                ),
                indent=2,
            )
        )
        return

    session = report.session
    if session is None:
        msg = "Run report unexpectedly has no session."
        raise TypeError(msg)
    typer.echo(f"toktrail run {session.id}: {session.name or '(unnamed)'}")
    typer.echo(f"Scope: {_format_scope_summary(session.scope)}")
    if session.archived_at_ms is not None:
        typer.echo(f"Archived: {format_epoch_ms_compact(session.archived_at_ms)}")
    _print_usage_summary(
        report,
        rich_output=rich_output,
        by_model=filtered_by_model,
        unconfigured_models=filtered_unconfigured,
        missing_price_mode=costing_config.missing_price,
    )


@run_app.command("list")
def list_command(
    ctx: typer.Context,
    active: Annotated[bool, typer.Option("--active")] = False,
    ended: Annotated[bool, typer.Option("--ended")] = False,
    archived: Annotated[bool, typer.Option("--archived")] = False,
    all_runs: Annotated[bool, typer.Option("--all")] = False,
    json_output: JsonOption = False,
    limit: ReportLimitOption = None,
    rich_output: RichOption = False,
) -> None:
    """List toktrail tracking runs."""
    if archived and all_runs:
        _exit_with_error("Use either --archived or --all, not both.")
    if active and ended:
        _exit_with_error("Use either --active or --ended, not both.")

    rows = list_runs(
        _resolve_state_db(ctx),
        limit=limit,
        include_ended=not active,
        include_archived=all_runs,
        archived_only=archived,
        active_only=active,
    )
    if ended:
        rows = tuple(run for run in rows if not run.active)

    if not rows:
        typer.echo("No toktrail runs found.")
        return

    if json_output:
        typer.echo(json.dumps([run.as_dict() for run in rows], indent=2))
        return

    typer.echo(f"{len(rows)} toktrail run{'s' if len(rows) != 1 else ''}:\n")

    payload_rows = [
        {
            "id": str(run.id),
            "state": "active"
            if run.active
            else ("archived" if run.archived_at_ms is not None else "ended"),
            "archived": format_epoch_ms_compact(run.archived_at_ms)
            if run.archived_at_ms
            else "",
            "scope": _format_scope_summary(run.scope),
            "name": run.name or "(unnamed)",
            "started": format_epoch_ms_compact(run.started_at_ms),
            "ended": format_epoch_ms_compact(run.ended_at_ms)
            if run.ended_at_ms
            else "",
        }
        for run in rows
    ]

    _print_table(
        payload_rows,
        ["id", "state", "archived", "scope", "name", "started", "ended"],
        {
            "id": "ID",
            "state": "State",
            "archived": "Archived",
            "scope": "Scope",
            "name": "Name",
            "started": "Started",
            "ended": "Ended",
        },
        rich_output=rich_output,
        numeric_columns={"id"},
        wrap_columns={"scope", "name"},
        max_widths={"scope": 40, "name": 24},
    )


def _build_run_scope_or_exit(
    *,
    harnesses: list[str],
    provider_ids: list[str],
    model_ids: list[str],
    source_session_ids: list[str],
    thinking_levels: list[str],
    agents: list[str],
) -> RunScope:
    normalized_harnesses: list[str] = []
    for harness in harnesses:
        normalized = normalize_identity(harness)
        try:
            definition = get_harness(normalized)
        except ValueError:
            _exit_with_error(f"Unsupported harness: {harness}")
        normalized_harnesses.append(definition.name)

    normalized_thinking: list[str] = []
    for level in thinking_levels:
        normalized_level = normalize_thinking_level(level)
        if normalized_level is None:
            _exit_with_error(f"Invalid thinking level: {level}")
        normalized_thinking.append(normalized_level)

    cleaned_source_sessions = [
        value.strip() for value in source_session_ids if value.strip()
    ]

    return RunScope(
        harnesses=tuple(normalized_harnesses),
        provider_ids=tuple(provider_ids),
        model_ids=tuple(model_ids),
        source_session_ids=tuple(cleaned_source_sessions),
        thinking_levels=tuple(normalized_thinking),
        agents=tuple(agents),
    )


def _format_scope_summary(scope: RunScope | PublicRunScope) -> str:
    if scope.empty:
        return "all configured usage"
    segments: list[str] = []
    if scope.harnesses:
        segments.append(f"harness={','.join(scope.harnesses)}")
    if scope.provider_ids:
        segments.append(f"provider={','.join(scope.provider_ids)}")
    if scope.model_ids:
        segments.append(f"model={','.join(scope.model_ids)}")
    if scope.source_session_ids:
        segments.append(f"source-session={','.join(scope.source_session_ids)}")
    if scope.thinking_levels:
        segments.append(f"thinking={','.join(scope.thinking_levels)}")
    if scope.agents:
        segments.append(f"agent={','.join(scope.agents)}")
    return "; ".join(segments)


@run_app.command("archive", help="Archive a tracking run.")
def archive_command(
    ctx: typer.Context,
    run_id: Annotated[int, typer.Argument()],
    json_output: JsonOption = False,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        archive_tracking_session(conn, run_id)
        run = get_tracking_session(conn, run_id)
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    if run is None:
        _exit_with_error(f"Run not found: {run_id}")
    if json_output:
        typer.echo(json.dumps(run.as_dict(), indent=2))
        return
    typer.echo(f"Archived run {run.id}: {run.name or '(unnamed)'}")


@run_app.command("unarchive", help="Restore an archived run.")
def unarchive_command(
    ctx: typer.Context,
    run_id: Annotated[int, typer.Argument()],
    json_output: JsonOption = False,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        unarchive_tracking_session(conn, run_id)
        run = get_tracking_session(conn, run_id)
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    if run is None:
        _exit_with_error(f"Run not found: {run_id}")
    if json_output:
        typer.echo(json.dumps(run.as_dict(), indent=2))
        return
    typer.echo(f"Unarchived run {run.id}: {run.name or '(unnamed)'}")


@subscriptions_app.callback(invoke_without_command=True)
def subscriptions(
    ctx: typer.Context,
    provider_id: ProviderOption = None,
    period: Annotated[str, typer.Option("--period")] = "all",
    json_output: JsonOption = False,
    rich_output: RichOption = False,
    now_ms: Annotated[int | None, typer.Option("--now-ms", hidden=True)] = None,
    refresh: RefreshOption = True,
    refresh_details: RefreshDetailsOption = False,
    raw: RawModeOption = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _subscriptions_status_impl(
        ctx=ctx,
        provider_id=provider_id,
        period=period,
        json_output=json_output,
        rich_output=rich_output,
        now_ms=now_ms,
        refresh=refresh,
        refresh_details=refresh_details,
        raw=raw,
        timezone_name=timezone_name,
        utc=utc,
    )


@subscriptions_app.command("status", help="Show subscription usage against limits.")
def subscriptions_status(
    ctx: typer.Context,
    provider_id: ProviderOption = None,
    period: Annotated[str, typer.Option("--period")] = "all",
    json_output: JsonOption = False,
    rich_output: RichOption = False,
    now_ms: Annotated[int | None, typer.Option("--now-ms", hidden=True)] = None,
    refresh: RefreshOption = True,
    refresh_details: RefreshDetailsOption = False,
    raw: RawModeOption = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
) -> None:
    _subscriptions_status_impl(
        ctx=ctx,
        provider_id=provider_id,
        period=period,
        json_output=json_output,
        rich_output=rich_output,
        now_ms=now_ms,
        refresh=refresh,
        refresh_details=refresh_details,
        raw=raw,
        timezone_name=timezone_name,
        utc=utc,
    )


def _subscriptions_status_impl(
    *,
    ctx: typer.Context,
    provider_id: str | None,
    period: str,
    json_output: bool,
    rich_output: bool,
    now_ms: int | None,
    refresh: bool,
    refresh_details: bool,
    raw: bool | None,
    timezone_name: str | None,
    utc: bool,
) -> None:
    subscriptions_parts.subscriptions_status_impl(
        ctx=ctx,
        provider_id=provider_id,
        period=period,
        json_output=json_output,
        rich_output=rich_output,
        now_ms=now_ms,
        refresh=refresh,
        refresh_details=refresh_details,
        raw=raw,
        timezone_name=timezone_name,
        utc=utc,
        exit_with_error_fn=_exit_with_error,
        refresh_before_report_fn=_refresh_before_report,
        load_costing_config_or_exit_fn=_load_costing_config_or_exit,
        open_toktrail_connection_fn=_open_toktrail_connection,
        summarize_subscription_usage_fn=summarize_subscription_usage,
        filter_subscription_usage_report_fn=_filter_subscription_usage_report,
        wrap_refresh_json_payload_fn=_wrap_refresh_json_payload,
        print_subscription_usage_report_fn=_print_subscription_usage_report,
    )


@statusline_app.callback(invoke_without_command=True)
def statusline(
    ctx: typer.Context,
    json_output: JsonOption = False,
    harness: HarnessOption = None,
    provider_id: ProviderOption = None,
    model_id: ModelOption = None,
    source_session_id: SourceSessionOption = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="Session selection: auto, latest, or none."),
    ] = None,
    basis: Annotated[
        str | None,
        typer.Option("--basis", help="Cost basis: source, actual, or virtual."),
    ] = None,
    refresh: Annotated[
        str | None,
        typer.Option("--refresh", help="Refresh policy: never, auto, or always."),
    ] = None,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
    refresh_details: RefreshDetailsOption = False,
    raw: RawModeOption = None,
    max_width: Annotated[int | None, typer.Option("--max-width", min=1)] = None,
    stale_after: Annotated[int | None, typer.Option("--stale-after", min=0)] = None,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    statusline_parts.statusline_impl(
        ctx,
        json_output=json_output,
        harness=harness,
        provider_id=provider_id,
        model_id=model_id,
        source_session_id=source_session_id,
        session=session,
        basis=basis,
        refresh=refresh,
        no_refresh=no_refresh,
        refresh_details=refresh_details,
        raw=raw,
        max_width=max_width,
        stale_after=stale_after,
    )


@statusline_app.command("test", help="Preview statusline output for your prompt.")
def statusline_test(
    ctx: typer.Context,
    harness: HarnessOption = None,
    provider_id: ProviderOption = None,
    model_id: ModelOption = None,
    source_session_id: SourceSessionOption = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="Session selection: auto, latest, or none."),
    ] = None,
    basis: Annotated[
        str | None,
        typer.Option("--basis", help="Cost basis: source, actual, or virtual."),
    ] = None,
    refresh: Annotated[
        str | None,
        typer.Option("--refresh", help="Refresh policy: never, auto, or always."),
    ] = None,
    no_refresh: Annotated[bool, typer.Option("--no-refresh")] = False,
    raw: RawModeOption = None,
    max_width: Annotated[int | None, typer.Option("--max-width", min=1)] = None,
    stale_after: Annotated[int | None, typer.Option("--stale-after", min=0)] = None,
) -> None:
    statusline_parts.statusline_test_impl(
        ctx,
        basis=basis or "virtual",
        refresh=refresh or "auto",
        no_refresh=no_refresh,
        refresh_details=False,
        raw=raw,
        max_width=max_width,
        stale_after=stale_after,
        json_output=False,
    )


@statusline_app.command("install", help="Install statusline into starship.")
def statusline_install(
    target: Annotated[str, typer.Option("--target")] = "starship",
) -> None:
    normalized = target.strip().lower()
    statusline_parts.statusline_install_impl(normalized)


@statusline_config_app.command("show", help="Show statusline configuration.")
def statusline_config_show(ctx: typer.Context) -> None:
    statusline_parts.statusline_config_show_impl(ctx)


@statusline_config_app.command("set", help="Set a statusline config value.")
def statusline_config_set(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument()],
    value: Annotated[str, typer.Argument()],
) -> None:
    statusline_parts.statusline_config_set_impl(ctx, key=key, value=value)


@usage_app.command("statusline", help="Render status line for prompt.")
def usage_statusline(
    ctx: typer.Context,
    json_output: JsonOption = False,
    provider_id: ProviderOption = None,
    harness: HarnessOption = None,
    basis: Annotated[
        str,
        typer.Option("--basis", help="Cost basis: source, actual, or virtual."),
    ] = "virtual",
    refresh: RefreshOption = True,
    refresh_details: RefreshDetailsOption = False,
    raw: RawModeOption = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
) -> None:
    _ = timezone_name, utc
    report, refresh_results, _payload, _elapsed_ms = _build_statusline_cli(
        ctx,
        harness=harness,
        provider_id=provider_id,
        model_id=None,
        source_session_id=None,
        session_mode="auto",
        basis=basis,
        refresh="auto" if refresh else "never",
        no_refresh=False,
        refresh_details=refresh_details,
        raw=raw,
        max_width=120,
        stale_after=60,
    )
    if json_output:
        typer.echo(
            json.dumps(
                _wrap_refresh_json_payload(
                    report.as_dict(),
                    refresh_results=refresh_results,
                    include_refresh=refresh_details,
                ),
                indent=2,
            )
        )
        return
    typer.echo(report.line)


@stats_app.callback(invoke_without_command=True)
def stats(
    ctx: typer.Context,
    format_: Annotated[
        str,
        typer.Option("--format", help="Output format: human or json."),
    ] = "human",
    period: UsagePeriodOption = None,
    since: Annotated[str | None, typer.Option("--since")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if format_ not in {"human", "json"}:
        _exit_with_error("--format must be one of: human, json.")
    from toktrail.periods import _resolve_timezone, parse_cli_boundary

    tz = _resolve_timezone(timezone_name=timezone_name, utc=utc)
    since_ms = parse_cli_boundary(since, tz=tz, is_until=False)
    until_ms = parse_cli_boundary(until, tz=tz, is_until=True)
    report = stats_report_api(
        _resolve_state_db(ctx),
        period=period,
        since_ms=since_ms,
        until_ms=until_ms,
        timezone=timezone_name,
        utc=utc,
        config_path=_resolve_config_path(ctx),
    )
    if format_ == "json":
        typer.echo(json.dumps(report.as_dict(), indent=2))
        return
    totals = report.totals
    token_payload = totals.get("tokens")
    tokens = token_payload if isinstance(token_payload, dict) else {}
    messages = totals.get("messages")
    message_count = int(messages) if isinstance(messages, (int, float, str)) else 0
    total_tokens_value = tokens.get("total")
    total_tokens = (
        int(total_tokens_value)
        if isinstance(total_tokens_value, (int, float, str))
        else 0
    )
    typer.echo("Stats v1")
    typer.echo(f"Messages: {_format_int(message_count)}")
    typer.echo(f"Tokens: {_format_int(total_tokens)}")
    typer.echo(f"Virtual cost: ${totals['virtual_usd']}")
    typer.echo(f"Unpriced models: {totals['unpriced_count']}")

    sessions_data = report.sessions
    session_count = sessions_data.get("session_count")
    if isinstance(session_count, (int, float)):
        typer.echo(f"Sessions: {_format_int(int(session_count))}")

    archetypes = report.archetypes
    if archetypes:
        counts = archetypes.get("counts")
        if isinstance(counts, dict) and any(v > 0 for v in counts.values()):
            parts = [f"{k}={v}" for k, v in counts.items() if v]
            typer.echo(f"Archetypes: {', '.join(parts)}")

    health = report.health
    if health:
        avg_score = health.get("average_score")
        outcomes = health.get("outcomes")
        if isinstance(outcomes, dict) and any(v > 0 for v in outcomes.values()):
            outcome_parts = [f"{k}={v}" for k, v in outcomes.items() if v]
            typer.echo(f"Outcomes: {', '.join(outcome_parts)}")
        if avg_score is not None:
            typer.echo(f"Avg health: {avg_score}")

    distributions = report.distributions
    if distributions:
        dur = distributions.get("duration_ms")
        if isinstance(dur, dict):
            median = dur.get("median")
            if median is not None:
                from toktrail.formatting import format_duration_seconds

                seconds = int(median) // 1000
                typer.echo(f"Median duration: {format_duration_seconds(seconds)}")

    # Tool usage section
    tools = report.tools
    tool_usage = report.tool_usage
    if tools:
        typer.echo("")
        typer.echo("Tool usage:")
        for row in tools:
            name = row.get("name", "?")
            count = row.get("count", 0)
            percent = row.get("percent", 0.0)
            typer.echo(f"  {name:<14} {_format_int(count):>8} ({percent * 100:>4.1f}%)")
    elif tool_usage:
        missing = tool_usage.get("missing_session_count", 0)
        if missing:
            typer.echo("")
            typer.echo(
                f"Tool usage: unavailable; {missing} sessions"
                " have no persisted tool stats."
            )




@stats_app.command("tools", help="Show ranked tool usage with bars.")
def stats_tools(
    ctx: typer.Context,
    format_: Annotated[
        str, typer.Option("--format", help="Output format: human or json.")
    ] = "human",
    period: UsagePeriodOption = None,
    since: Annotated[str | None, typer.Option("--since")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
    harness: HarnessOption = None,
    area: AreaOption = None,
    limit: Annotated[int, typer.Option("--limit", help="Max tools to show.")] = 20,
) -> None:
    from toktrail.api.reports import tool_usage_report
    from toktrail.periods import _resolve_timezone, parse_cli_boundary

    if format_ not in {"human", "json"}:
        _exit_with_error("--format must be one of: human, json.")

    tz = _resolve_timezone(timezone_name=timezone_name, utc=utc)
    since_ms = parse_cli_boundary(since, tz=tz, is_until=False)
    until_ms = parse_cli_boundary(until, tz=tz, is_until=True)

    report = tool_usage_report(
        _resolve_state_db(ctx),
        period=period,
        since_ms=since_ms,
        until_ms=until_ms,
        timezone=timezone_name,
        utc=utc,
        harness=harness,
        area=area,
        limit=limit,
        config_path=_resolve_config_path(ctx),
    )

    if format_ == "json":
        typer.echo(json.dumps(report.as_dict(), indent=2))
        return

    if not report.tools:
        if report.missing_session_count:
            typer.echo(
                f"Tool usage: unavailable;"
                f" {report.missing_session_count} sessions"
                " have no persisted tool stats."
            )
        else:
            typer.echo("Tool usage: no data.")
        return

    typer.echo("Tool usage")
    max_count = max(row.count for row in report.tools)
    for row in report.tools:
        bar = _format_bar(row.count, max_count)
        typer.echo(
            f"{row.name:<14} {bar:<24}"
            f" {_format_int(row.count):>8}"
            f" ({row.percent * 100:>4.1f}%)"
        )


def _format_bar(count: int, max_count: int, *, width: int = 24) -> str:
    if max_count <= 0 or width <= 0:
        return ""
    filled = max(1, round(width * count / max_count))
    return chr(9608) * filled  # chr(9608) is the full block character
@usage_app.command("daily", help="Show daily usage breakdown.")
@usage_app.command("weekly", help="Show weekly usage breakdown.")
@usage_app.command("monthly", help="Show monthly usage breakdown.")
@usage_app.command("summary", help="Show aggregated usage summary.")
@usage_app.command("day", help="Show usage for a specific day.")
@usage_app.command("today", help="Show usage for today.")
@usage_app.command("yesterday", help="Show usage for yesterday.")
@usage_app.command("week", help="Show usage for a specific week.")
@usage_app.command("this-week", help="Show usage for the current week.")
@usage_app.command("last-week", help="Show usage for the previous week.")
@usage_app.command("this-month", help="Show usage for the current month.")
@usage_app.command("last-month", help="Show usage for the previous month.")
@usage_app.command("sessions", help="List usage sessions with token totals.")
@usage_app.command("runs", help="List tracking runs with summaries.")
@usage_app.command("machines", help="List machines with token totals.")
@usage_app.command("areas", help="List usage totals grouped by area.")
def usage(  # noqa: C901
    ctx: typer.Context,
    json_output: JsonOption = False,
    harness: HarnessOption = None,
    source_session_id: SourceSessionOption = None,
    machine: MachineOption = None,
    provider_id: ProviderOption = None,
    model_id: ModelOption = None,
    thinking_level: ThinkingOption = None,
    agent: AgentOption = None,
    area: AreaOption = None,
    area_leaf: AreaLeafOption = None,
    area_exact: AreaExactOption = False,
    unassigned_area: UnassignedAreaOption = False,
    since: TimeBoundaryOption = None,
    until: UntilBoundaryOption = None,
    session_period: UsagePeriodOption = None,
    session_today: SessionTodayOption = False,
    session_yesterday: SessionYesterdayOption = False,
    session_this_week: SessionThisWeekOption = False,
    session_last_week: SessionLastWeekOption = False,
    session_this_month: SessionThisMonthOption = False,
    session_last_month: SessionLastMonthOption = False,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
    rich_output: RichOption = False,
    split_thinking: SplitThinkingOption = False,
    price_state: PriceStateOption = "all",
    min_messages: MinMessagesOption = None,
    min_tokens: MinTokensOption = None,
    sort: ReportSortOption = "actual",
    limit: ReportLimitOption = None,
    breakdown: BreakdownOption = False,
    compact: Annotated[bool, typer.Option("--compact")] = False,
    table: SessionTableOption = False,
    with_summary: Annotated[
        bool,
        typer.Option(
            "--with-summary",
            help="Include persisted session digest summary.",
        ),
    ] = False,
    instances: Annotated[bool, typer.Option("--instances")] = False,
    order: Annotated[str, typer.Option("--order")] = "desc",
    locale: Annotated[str | None, typer.Option("--locale")] = None,
    start_of_week: Annotated[str, typer.Option("--start-of-week")] = "monday",
    archived: Annotated[bool, typer.Option("--archived")] = False,
    all_runs: Annotated[bool, typer.Option("--all")] = False,
    refresh: RefreshOption = True,
    refresh_details: RefreshDetailsOption = False,
    raw: RawModeOption = None,
    last: Annotated[
        bool, typer.Option("--last", help="Show only the newest source session.")
    ] = False,
    direct: Annotated[bool, typer.Option("--direct")] = False,
    subtree: Annotated[bool, typer.Option("--subtree")] = False,
    leaves: Annotated[bool, typer.Option("--leaves")] = False,
    percent: Annotated[bool, typer.Option("--percent")] = False,
    share_by: Annotated[str, typer.Option("--share-by")] = "tokens",
) -> None:
    if timezone_name is not None and utc:
        _exit_with_error("Use either --timezone or --utc, not both.")
    if direct and subtree:
        _exit_with_error("Use either --direct or --subtree, not both.")
    selected_area, area_match = _resolve_area_filter_inputs_or_exit(
        area=area,
        area_leaf=area_leaf,
        unassigned_area=unassigned_area,
    )
    info_name = ctx.info_name
    if info_name is None:
        _exit_with_error("Missing usage subcommand.")
    normalized_view = info_name.strip().lower()
    view_aliases = {
        "day": "today",
        "week": "this-week",
    }
    normalized_view = view_aliases.get(normalized_view, normalized_view)
    series_views = {"daily", "weekly", "monthly"}
    named_periods = {
        "today",
        "yesterday",
        "this-week",
        "last-week",
        "this-month",
        "last-month",
    }

    refresh_results = _refresh_before_report(
        ctx,
        enabled=refresh,
        details=refresh_details,
        json_output=json_output,
        harness=harness,
        include_raw_json=raw,
    )

    if normalized_view in series_views:
        payload = _usage_series(
            ctx=ctx,
            view=normalized_view,
            json_output=json_output,
            harness=harness,
            source_session_id=source_session_id,
            machine=machine,
            provider_id=provider_id,
            model_id=model_id,
            thinking_level=thinking_level,
            agent=agent,
            area=selected_area,
            area_match=area_match,
            area_exact=area_exact,
            unassigned_area=unassigned_area,
            since=since,
            until=until,
            timezone_name=timezone_name,
            utc=utc,
            split_thinking=split_thinking,
            breakdown=breakdown,
            compact=compact,
            instances=instances,
            order=order,
            locale=locale,
            start_of_week=start_of_week,
            price_state=price_state,
            min_messages=min_messages,
            min_tokens=min_tokens,
            sort=sort,
            limit=limit,
            rich_output=rich_output,
        )
        if json_output:
            if payload is None:
                msg = "Usage series payload unexpectedly missing."
                raise TypeError(msg)
            typer.echo(
                json.dumps(
                    _wrap_refresh_json_payload(
                        payload,
                        refresh_results=refresh_results,
                        include_refresh=refresh_details,
                    ),
                    indent=2,
                )
            )
        return

    if normalized_view == "summary" or normalized_view in named_periods:
        payload = _usage_aggregate(
            ctx=ctx,
            period=None if normalized_view == "summary" else normalized_view,
            json_output=json_output,
            harness=harness,
            source_session_id=source_session_id,
            machine=machine,
            provider_id=provider_id,
            model_id=model_id,
            thinking_level=thinking_level,
            agent=agent,
            area=selected_area,
            area_match=area_match,
            area_exact=area_exact,
            unassigned_area=unassigned_area,
            since=since,
            until=until,
            timezone_name=timezone_name,
            utc=utc,
            rich_output=rich_output,
            split_thinking=split_thinking,
            price_state=price_state,
            min_messages=min_messages,
            min_tokens=min_tokens,
            sort=sort,
            limit=limit,
        )
        if json_output:
            if payload is None:
                msg = "Usage aggregate payload unexpectedly missing."
                raise TypeError(msg)
            typer.echo(
                json.dumps(
                    _wrap_refresh_json_payload(
                        payload,
                        refresh_results=refresh_results,
                        include_refresh=refresh_details,
                    ),
                    indent=2,
                )
            )
        return

    if normalized_view in {"machines", "machine"}:
        machine_period = _resolve_usage_session_period_or_exit(
            period=session_period,
            today=session_today,
            yesterday=session_yesterday,
            this_week=session_this_week,
            last_week=session_last_week,
            this_month=session_this_month,
            last_month=session_last_month,
        )
        payload = _usage_machines(
            ctx=ctx,
            json_output=json_output,
            period=machine_period,
            harness=harness,
            source_session_id=source_session_id,
            machine=machine,
            provider_id=provider_id,
            model_id=model_id,
            thinking_level=thinking_level,
            agent=agent,
            area=selected_area,
            area_match=area_match,
            area_exact=area_exact,
            unassigned_area=unassigned_area,
            since=since,
            until=until,
            timezone_name=timezone_name,
            utc=utc,
            split_thinking=split_thinking,
            rich_output=rich_output,
        )
        if json_output:
            if payload is None:
                msg = "Usage machines payload unexpectedly missing."
                raise TypeError(msg)
            typer.echo(
                json.dumps(
                    _wrap_refresh_json_payload(
                        payload,
                        refresh_results=refresh_results,
                        include_refresh=refresh_details,
                    ),
                    indent=2,
                )
            )
        return

    if normalized_view in {"areas", "area"}:
        if instances:
            _exit_with_error("--instances is not supported for areas view.")
        area_period = _resolve_usage_session_period_or_exit(
            period=session_period,
            today=session_today,
            yesterday=session_yesterday,
            this_week=session_this_week,
            last_week=session_last_week,
            this_month=session_this_month,
            last_month=session_last_month,
        )
        payload = _usage_areas(
            ctx=ctx,
            json_output=json_output,
            period=area_period,
            harness=harness,
            source_session_id=source_session_id,
            machine=machine,
            provider_id=provider_id,
            model_id=model_id,
            thinking_level=thinking_level,
            agent=agent,
            area=selected_area,
            area_match=area_match,
            area_exact=area_exact,
            unassigned_area=unassigned_area,
            since=since,
            until=until,
            timezone_name=timezone_name,
            utc=utc,
            split_thinking=split_thinking,
            rich_output=rich_output,
            direct=direct,
            subtree=subtree,
            leaves=leaves,
            percent=percent,
            share_by=share_by,
        )
        if json_output:
            if payload is None:
                msg = "Usage areas payload unexpectedly missing."
                raise TypeError(msg)
            typer.echo(
                json.dumps(
                    _wrap_refresh_json_payload(
                        payload,
                        refresh_results=refresh_results,
                        include_refresh=refresh_details,
                    ),
                    indent=2,
                )
            )
        return

    if normalized_view in {"sessions", "session"}:
        if instances:
            _exit_with_error("--instances is not supported for sessions view.")
        session_period_value = _resolve_usage_session_period_or_exit(
            period=session_period,
            today=session_today,
            yesterday=session_yesterday,
            this_week=session_this_week,
            last_week=session_last_week,
            this_month=session_this_month,
            last_month=session_last_month,
        )
        payload = _usage_sessions(
            ctx=ctx,
            json_output=json_output,
            harness=harness,
            source_session_id=source_session_id,
            machine=machine,
            provider_id=provider_id,
            model_id=model_id,
            thinking_level=thinking_level,
            agent=agent,
            area=selected_area,
            area_match=area_match,
            area_exact=area_exact,
            unassigned_area=unassigned_area,
            since=since,
            until=until,
            period=session_period_value,
            timezone_name=timezone_name,
            utc=utc,
            split_thinking=split_thinking,
            breakdown=breakdown,
            compact=compact,
            table=table or compact,
            order=order,
            limit=limit,
            last=last,
            rich_output=rich_output,
            with_summary=with_summary,
        )
        if json_output:
            if payload is None:
                msg = "Usage sessions payload unexpectedly missing."
                raise TypeError(msg)
            typer.echo(
                json.dumps(
                    _wrap_refresh_json_payload(
                        payload,
                        refresh_results=refresh_results,
                        include_refresh=refresh_details,
                    ),
                    indent=2,
                )
            )
        return

    if normalized_view in {"runs", "run"}:
        if instances:
            _exit_with_error("--instances is not supported for runs view.")
        if archived and all_runs:
            _exit_with_error("Use either --archived or --all, not both.")
        payload = _usage_runs(
            ctx=ctx,
            json_output=json_output,
            provider_id=provider_id,
            machine=machine,
            model_id=model_id,
            thinking_level=thinking_level,
            agent=agent,
            area=selected_area,
            area_match=area_match,
            area_exact=area_exact,
            unassigned_area=unassigned_area,
            since=since,
            until=until,
            timezone_name=timezone_name,
            utc=utc,
            split_thinking=split_thinking,
            order=order,
            limit=limit,
            last=last,
            include_archived=all_runs,
            archived_only=archived,
            rich_output=rich_output,
        )
        if json_output:
            if payload is None:
                msg = "Usage runs payload unexpectedly missing."
                raise TypeError(msg)
            typer.echo(
                json.dumps(
                    _wrap_refresh_json_payload(
                        payload,
                        refresh_results=refresh_results,
                        include_refresh=refresh_details,
                    ),
                    indent=2,
                )
            )
        return

    _exit_with_error(
        "Unsupported usage view. Use daily, weekly, monthly, sessions, runs, "
        "machines, areas, "
        "summary, today, yesterday, this-week, last-week, this-month, or "
        "last-month."
    )


# Usage helpers moved to toktrail.cli_parts.usage.
_resolve_usage_session_period_or_exit = (
    usage_parts._resolve_usage_session_period_or_exit
)
_resolve_area_filter_inputs_or_exit = usage_parts._resolve_area_filter_inputs_or_exit
_usage_series = usage_parts._usage_series
_print_usage_series = usage_parts._print_usage_series
_print_usage_series_bucket_table = usage_parts._print_usage_series_bucket_table
_usage_machines = usage_parts._usage_machines
_print_usage_machine_rows = usage_parts._print_usage_machine_rows
_usage_sessions = usage_parts._usage_sessions
_print_usage_sessions = usage_parts._print_usage_sessions
_usage_runs = usage_parts._usage_runs
_print_usage_runs = usage_parts._print_usage_runs
_usage_areas = usage_parts._usage_areas
_print_usage_areas = usage_parts._print_usage_areas
_format_area_filter_summary = usage_parts._format_area_filter_summary
_print_unassigned_area_warning = usage_parts._print_unassigned_area_warning
_usage_aggregate = usage_parts._usage_aggregate
_format_session_model_line = usage_parts._format_session_model_line
_format_session_cost_line = usage_parts._format_session_cost_line
_format_token_usage_line = usage_parts._format_token_usage_line
_format_model_list = usage_parts._format_model_list
_print_usage_summary = usage_parts._print_usage_summary


def _filter_subscription_usage_report(
    report: SubscriptionUsageReport,
    *,
    period: str,
) -> SubscriptionUsageReport:
    if period == "all":
        return report
    subscriptions = []
    for subscription in report.subscriptions:
        periods = tuple(item for item in subscription.periods if item.period == period)
        if not periods:
            continue
        subscriptions.append(replace(subscription, periods=periods))
    return replace(report, subscriptions=tuple(subscriptions))


def _print_subscription_usage_report(
    report: SubscriptionUsageReport,
    *,
    provider_filter: str | None,
    rich_output: bool,
    display_timezone_name: str | None,
    display_utc: bool,
    now_ms: int | None = None,
) -> None:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if not report.subscriptions:
        if provider_filter:
            typer.echo(f"No subscriptions matched provider {provider_filter}.")
            return
        typer.echo("No provider subscriptions configured.")
        return

    display_tz_label = _display_timezone_label(
        timezone_name=display_timezone_name,
        utc=display_utc,
    )
    typer.echo("toktrail subscriptions")
    typer.echo(f"Display timezone: {display_tz_label}")
    for subscription in report.subscriptions:
        typer.echo("")
        providers = ",".join(subscription.usage_provider_ids)
        plan_timezone_label = subscription.timezone or "(local)"
        typer.echo(
            f"Plan: {subscription.display_name} ({subscription.subscription_id})"
        )
        typer.echo(f"  providers: {providers}")
        scope_label = (
            subscription.scope.label if subscription.scope is not None else "all areas"
        )
        typer.echo(f"  scope: {scope_label}")
        typer.echo(f"  quota basis: {subscription.quota_cost_basis}")
        typer.echo(f"  plan timezone: {plan_timezone_label}")
        if subscription.billing is not None:
            typer.echo("")
            typer.echo("Billing")
            billing = subscription.billing
            _print_table(
                [
                    {
                        "period": billing.period,
                        "window": _format_subscription_window(
                            billing.since_ms,
                            billing.until_ms,
                            timezone_name=display_timezone_name,
                            utc=display_utc,
                            status="active",
                        ),
                        "fixed": _format_cost(billing.fixed_cost_usd),
                        "value": _format_cost(billing.value_usd),
                        "basis": billing.billing_basis,
                        "net_savings": _format_cost(billing.net_savings_usd),
                        "break_even": _format_break_even(billing),
                    }
                ],
                [
                    "period",
                    "window",
                    "fixed",
                    "value",
                    "basis",
                    "net_savings",
                    "break_even",
                ],
                {
                    "period": "period",
                    "window": f"window ({display_tz_label})",
                    "fixed": "fixed",
                    "value": "value",
                    "basis": "basis",
                    "net_savings": "net savings",
                    "break_even": "break-even",
                },
                rich_output=rich_output,
                numeric_columns={"fixed", "value", "net_savings"},
                wrap_columns={"window"},
                max_widths={"window": 40},
            )
            typer.echo("")
            typer.echo("Quota windows")
        rows: list[dict[str, str]] = []
        all_warnings: list[dict[str, object]] = []
        for period in subscription.periods:
            left_value = _format_cost(period.remaining_usd)
            if period.over_limit_usd > 0:
                left_value = f"{left_value} over {_format_cost(period.over_limit_usd)}"
            rows.append(
                {
                    "period": period.period,
                    "status": _format_subscription_status(period.status),
                    "window": _format_subscription_window(
                        period.since_ms,
                        period.until_ms,
                        timezone_name=display_timezone_name,
                        utc=display_utc,
                        status=period.status,
                        last_since_ms=period.last_since_ms,
                        last_until_ms=period.last_until_ms,
                    ),
                    "reset": _format_subscription_reset(
                        period,
                        now_ms=now_ms,
                    ),
                    "limit": _format_cost(period.limit_usd),
                    "used": _format_cost(period.used_usd),
                    "left": left_value,
                    "used_pct": _format_percent(period.percent_used),
                }
            )
            all_warnings.extend(period.warnings)
        deduped_warnings: list[dict[str, object]] = []
        seen_warning_keys: set[tuple[object, ...]] = set()
        for warning in all_warnings:
            key = (
                warning.get("kind"),
                warning.get("cost_basis"),
                warning.get("provider_id"),
                warning.get("model_id"),
                warning.get("message_count"),
            )
            if key in seen_warning_keys:
                continue
            seen_warning_keys.add(key)
            deduped_warnings.append(warning)
        _print_table(
            rows,
            [
                "period",
                "status",
                "reset",
                "window",
                "limit",
                "used",
                "left",
                "used_pct",
            ],
            {
                "period": "period",
                "status": "status",
                "reset": "reset",
                "window": f"window ({display_tz_label})",
                "limit": "limit",
                "used": "used",
                "left": "left",
                "used_pct": "used%",
            },
            rich_output=rich_output,
            numeric_columns={"limit", "used", "left", "used_pct"},
            wrap_columns={"window"},
            max_widths={"reset": 14, "window": 32},
        )
        if deduped_warnings:
            typer.echo("")
            typer.echo("Warnings")
            for warning in deduped_warnings:
                if warning.get("kind") == "zero_cost_with_tokens":
                    provider = warning.get("provider_id")
                    model = warning.get("model_id")
                    msg_count = warning.get("message_count")
                    cost_basis = warning.get("cost_basis")
                    typer.echo(
                        f"  {provider}/{model} has {msg_count} messages but "
                        f"zero cost for basis={cost_basis}"
                    )


def _format_break_even(billing: SubscriptionBillingPeriod) -> str:
    remaining = billing.break_even_remaining_usd
    percent = billing.break_even_percent
    if remaining > 0:
        percent_text = _format_percent(percent)
        return f"{_format_cost(remaining)} left ({percent_text})"
    if percent is None:
        return "reached"
    return f"reached ({_format_percent(percent)})"


def _format_subscription_window_range(
    since_ms: int,
    until_ms: int,
    *,
    timezone_name: str | None,
    utc: bool,
) -> str:
    from toktrail.periods import resolve_timezone

    tz = resolve_timezone(timezone_name=timezone_name, utc=utc)
    since_dt = datetime.datetime.fromtimestamp(since_ms / 1000, tz=tz)
    until_dt = datetime.datetime.fromtimestamp(until_ms / 1000, tz=tz)

    duration_ms = until_ms - since_ms
    has_time = duration_ms < 24 * 60 * 60 * 1000 or not (
        since_dt.hour == since_dt.minute == since_dt.second == since_dt.microsecond == 0
        and until_dt.hour
        == until_dt.minute
        == until_dt.second
        == until_dt.microsecond
        == 0
    )
    if has_time:
        since_str = since_dt.strftime("%Y-%m-%d %H:%M")
        if since_dt.date() == until_dt.date():
            until_str = until_dt.strftime("%H:%M")
        else:
            until_str = until_dt.strftime("%Y-%m-%d %H:%M")
        return f"{since_str}..{until_str}"
    return f"{since_dt.date().isoformat()}..{until_dt.date().isoformat()}"


def _format_subscription_window(
    since_ms: int | None,
    until_ms: int | None,
    *,
    timezone_name: str | None,
    utc: bool = False,
    status: str,
    last_since_ms: int | None = None,
    last_until_ms: int | None = None,
) -> str:

    if since_ms is None or until_ms is None:
        if status == "waiting_for_first_use":
            return "starts on first use"
        if status == "expired_waiting_for_next_use":
            if last_since_ms is not None and last_until_ms is not None:
                return "last " + _format_subscription_window_range(
                    last_since_ms,
                    last_until_ms,
                    timezone_name=timezone_name,
                    utc=utc,
                )
            return "expired"
        return "-"

    return _format_subscription_window_range(
        since_ms,
        until_ms,
        timezone_name=timezone_name,
        utc=utc,
    )


def _display_timezone_label(*, timezone_name: str | None, utc: bool) -> str:
    from toktrail.periods import resolve_timezone

    tz = resolve_timezone(timezone_name=timezone_name, utc=utc)
    if tz is datetime.timezone.utc:
        return "UTC"
    return getattr(tz, "key", str(tz))


def _format_subscription_reset(
    period: SubscriptionUsagePeriod,
    *,
    now_ms: int,
) -> str:
    from toktrail.formatting import format_duration_seconds

    if period.until_ms is not None:
        seconds = max(0, (period.until_ms - now_ms) // 1000)
        return f"in {format_duration_seconds(seconds)}"
    if period.status == "waiting_for_first_use":
        return "on first use"
    if period.status == "expired_waiting_for_next_use":
        return "on next use"
    return "-"


def _format_subscription_status(status: str) -> str:
    return {
        "waiting_for_first_use": "waiting",
        "expired_waiting_for_next_use": "expired",
    }.get(status, status)


@config_app.command("path", help="Print resolved configuration file paths.")
def config_path(
    ctx: typer.Context,
    which: Annotated[str, typer.Option("--which")] = "all",
) -> None:
    normalized = which.strip().lower()
    if normalized not in {"all", "config", "prices", "prices-dir", "subscriptions"}:
        _exit_with_error(
            "--which must be one of: all, config, prices, prices-dir, subscriptions."
        )
    config = _resolve_config_path(ctx)
    prices = _resolve_prices_path(ctx)
    prices_dir = _resolve_prices_dir(ctx)
    subscriptions = _resolve_subscriptions_path(ctx)
    if normalized == "config":
        typer.echo(config)
        return
    if normalized == "prices":
        typer.echo(prices)
        return
    if normalized == "prices-dir":
        typer.echo(prices_dir)
        return
    if normalized == "subscriptions":
        typer.echo(subscriptions)
        return
    typer.echo(f"config:        {config}")
    typer.echo(f"prices:        {prices}")
    typer.echo(f"prices dir:    {prices_dir}")
    typer.echo(f"subscriptions: {subscriptions}")


@config_app.command("init", help="Create a default toktrail configuration file.")
def config_init(
    ctx: typer.Context,
    template: Annotated[str, typer.Option("--template")] = DEFAULT_TEMPLATE_NAME,
    only: Annotated[str, typer.Option("--only")] = "all",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    normalized_only = only.strip().lower()
    if normalized_only not in {"all", "config", "prices", "subscriptions"}:
        _exit_with_error("--only must be one of: all, config, prices, subscriptions.")
    config = _resolve_config_path(ctx)
    prices = _resolve_prices_path(ctx)
    prices_dir = _resolve_prices_dir(ctx)
    subscriptions = _resolve_subscriptions_path(ctx)
    targets = []
    if normalized_only in {"all", "config"}:
        targets.append(("config", config, render_config_template))
    if normalized_only in {"all", "prices"}:
        targets.append(("prices", prices, render_prices_template))
    if normalized_only in {"all", "subscriptions"}:
        targets.append(("subscriptions", subscriptions, render_subscriptions_template))

    if not force:
        existing = [path for _, path, _ in targets if path.exists()]
        if normalized_only in {"all", "prices"} and prices_dir.exists():
            existing.append(prices_dir)
        if existing:
            if len(existing) == 1:
                _exit_with_error(f"Toktrail config file already exists: {existing[0]}")
            _exit_with_error(
                "Toktrail config files already exist:\n"
                + "\n".join(f"- {path}" for path in existing)
            )

    written: list[tuple[str, Path]] = []
    for label, path, renderer in targets:
        try:
            content = renderer(template)
        except ValueError as exc:
            _exit_with_error(str(exc))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append((label, path))
    if normalized_only in {"all", "prices"}:
        prices_dir.mkdir(parents=True, exist_ok=True)
        written.append(("prices-dir", prices_dir))

    typer.echo("Initialized toktrail config files:")
    for label, path in written:
        typer.echo(f"  {label}: {path}")


@config_app.command("validate", help="Validate the configuration file.")
def config_validate(ctx: typer.Context) -> None:
    loaded = _load_resolved_costing_config_or_exit(ctx)
    summary = summarize_costing_config(loaded.config)
    typer.echo("Config valid:")
    typer.echo(f"  config:        {loaded.config_path}")
    typer.echo(f"  prices:        {loaded.prices_path}")
    typer.echo(f"  prices dir:    {loaded.prices_dir}")
    typer.echo(f"  subscriptions: {loaded.subscriptions_path}")
    typer.echo(f"  actual rules:   {summary.actual_rule_count}")
    typer.echo(f"  actual prices:  {summary.actual_price_count}")
    typer.echo(f"  virtual prices: {summary.virtual_price_count}")
    typer.echo(f"  subscriptions:  {summary.subscription_count}")
    typer.echo(f"  price files:    {len(loaded.price_paths)}")
    warnings = [
        price
        for price in (*loaded.config.actual_prices, *loaded.config.virtual_prices)
        if price.cached_input_usd_per_1m is not None
        and price.cached_input_usd_per_1m > price.input_usd_per_1m
    ]
    for price in warnings:
        typer.echo(
            f"  warning: cached_input exceeds input for {price.provider}/{price.model}"
        )


@config_app.command("show", help="Display current configuration.")
def config_show(ctx: typer.Context) -> None:
    loaded = _load_resolved_costing_config_or_exit(ctx)
    summary = summarize_costing_config(loaded.config)
    typer.echo(f"config path:     {loaded.config_path}")
    typer.echo(f"prices path:     {loaded.prices_path}")
    typer.echo(f"prices dir:      {loaded.prices_dir}")
    typer.echo(f"price files:     {len(loaded.price_paths)}")
    typer.echo(f"subs path:       {loaded.subscriptions_path}")
    typer.echo(f"config exists:   {'yes' if loaded.config_exists else 'no'}")
    typer.echo(f"prices exists:   {'yes' if loaded.prices_exists else 'no'}")
    typer.echo(f"manual exists:   {'yes' if loaded.manual_prices_exists else 'no'}")
    typer.echo(f"provider exists: {'yes' if loaded.provider_prices_exists else 'no'}")
    typer.echo(f"subs exists:     {'yes' if loaded.subscriptions_exists else 'no'}")
    typer.echo(f"config_version:  {summary.config_version}")
    typer.echo(f"default actual:  {summary.default_actual_mode}")
    typer.echo(f"default virtual: {summary.default_virtual_mode}")
    typer.echo(f"missing price:   {summary.missing_price}")
    typer.echo(f"price profile:   {summary.price_profile or '(none)'}")
    typer.echo(f"actual rules:    {summary.actual_rule_count}")
    typer.echo(f"actual prices:   {summary.actual_price_count}")
    typer.echo(f"virtual prices:  {summary.virtual_price_count}")
    typer.echo(f"subscriptions:   {summary.subscription_count}")
    if loaded.price_paths:
        typer.echo("price paths:")
        for path in loaded.price_paths:
            typer.echo(f"  - {path}")
    typer.echo("Run `toktrail prices list` to inspect configured price rows.")


@sources_app.callback(invoke_without_command=True)
def sources(
    ctx: typer.Context,
    harnesses: HarnessesOption = None,
    source_path: SourcePathOption = None,
    json_output: JsonOption = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _sources_list(
        ctx,
        harnesses=harnesses,
        source_path=source_path,
        json_output=json_output,
    )


@sources_app.command("list", help="Show configured harness sources.")
def sources_list(
    ctx: typer.Context,
    harnesses: HarnessesOption = None,
    source_path: SourcePathOption = None,
    json_output: JsonOption = False,
) -> None:
    _sources_list(
        ctx,
        harnesses=harnesses,
        source_path=source_path,
        json_output=json_output,
    )


def _sources_list(
    ctx: typer.Context,
    *,
    harnesses: list[str] | None,
    source_path: Path | None,
    json_output: bool,
) -> None:
    sources_parts.sources_list_impl(
        ctx=ctx,
        harnesses=harnesses,
        source_path=source_path,
        json_output=json_output,
        load_resolved_toktrail_config_or_exit_fn=_load_resolved_toktrail_config_or_exit,
        exit_with_error_fn=_exit_with_error,
        format_int_fn=_format_int,
        print_table_fn=_print_table,
    )


@prices_app.command("list", help="List configured model prices.")
def pricing_list(
    ctx: typer.Context,
    used_only: Annotated[bool, typer.Option("--used-only")] = False,
    missing_only: Annotated[bool, typer.Option("--missing-only")] = False,
    table: PriceTableOption = "virtual",
    provider: ProviderOption = None,
    model: ModelOption = None,
    query: PriceQueryOption = None,
    category: CategoryOption = None,
    release_status: ReleaseStatusOption = None,
    sort: PriceSortOption = "provider",
    limit: ReportLimitOption = None,
    aliases: AliasesOption = False,
    json_output: JsonOption = False,
    rich_output: RichOption = False,
    refresh: RefreshOption = True,
    refresh_details: RefreshDetailsOption = False,
    raw: RawModeOption = None,
) -> None:
    prices_parts.pricing_list(
        ctx,
        used_only=used_only,
        missing_only=missing_only,
        table=table,
        provider=provider,
        model=model,
        query=query,
        category=category,
        release_status=release_status,
        sort=sort,
        limit=limit,
        aliases=aliases,
        json_output=json_output,
        rich_output=rich_output,
        refresh=refresh,
        refresh_details=refresh_details,
        raw=raw,
        price_rows_fn=_price_rows,
        print_price_table_fn=_print_price_table,
    )


def _default_pricing_parse_output_path(ctx: typer.Context, provider: str) -> Path:
    return _resolve_prices_dir(ctx) / f"{normalize_identity(provider)}.toml"


def _is_provider_price_file(ctx: typer.Context, target: Path, provider: str) -> bool:
    expected = _default_pricing_parse_output_path(ctx, provider)
    try:
        return target.resolve() == expected.resolve()
    except OSError:
        return target.absolute() == expected.absolute()


@prices_app.command("parse", help="Parse a pricing page into TOML.")
def pricing_parse(
    ctx: typer.Context,
    provider: Annotated[str, typer.Option("--provider")],
    table: PriceTableOption = "virtual",
    tier: Annotated[str, typer.Option("--tier")] = "standard",
    input_path: Annotated[Path | None, typer.Option("--input")] = None,
    output_path: Annotated[
        str | None,
        typer.Option(
            "--output",
            "--out",
            help=(
                "Output TOML path, '-' for stdout. Defaults to prices/<provider>.toml."
            ),
        ),
    ] = None,
    merge: Annotated[bool, typer.Option("--merge")] = False,
    replace_provider: Annotated[bool, typer.Option("--replace-provider")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: JsonOption = False,
) -> None:
    prices_parts.pricing_parse(
        ctx,
        provider=provider,
        table=table,
        tier=tier,
        input_path=input_path,
        output_path=output_path,
        merge=merge,
        replace_provider=replace_provider,
        dry_run=dry_run,
        json_output=json_output,
    )


@app.command("refresh", help="Import usage from configured sources.")
def refresh_usage(
    ctx: typer.Context,
    harness: RequiredHarnessOption = None,
    source: RequiredSourceOption = None,
    run_id: RunOption = None,
    source_session_id: SourceSessionOption = None,
    since_run_start: SinceRunStartOption = False,
    raw: RawModeOption = None,
    no_run: NoRunOption = False,
    dry_run: DryRunOption = False,
    json_output: JsonOption = False,
    full: Annotated[
        bool,
        typer.Option(
            "--full",
            help="Scan all configured sources instead of quick refresh.",
        ),
    ] = False,
) -> None:
    refresh_parts.refresh_usage_impl(
        ctx,
        harness=harness,
        source=source,
        run_id=run_id,
        source_session_id=source_session_id,
        since_run_start=since_run_start,
        raw=raw,
        no_run=no_run,
        dry_run=dry_run,
        json_output=json_output,
        full=full,
    )


@app.command("watch", help="Continuously import usage data from configured sources.")
def watch(
    ctx: typer.Context,
    run_id: RunOption = None,
    harnesses: HarnessesOption = None,
    interval: IntervalOption = 2.0,
    raw: RawModeOption = None,
    json_output: JsonOption = False,
) -> None:
    try:
        watch_parts.watch_impl(
            ctx,
            run_id=run_id,
            harnesses=harnesses,
            interval=interval,
            raw=raw,
            json_output=json_output,
        )
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))


@sources_app.command("skipped", help="List skipped source records.")
def sources_skipped(
    ctx: typer.Context,
    clear: Annotated[
        bool,
        typer.Option(
            "--clear",
            help="Clear skipped-source cache instead of listing it.",
        ),
    ] = False,
    harness: HarnessOption = None,
    json_output: JsonOption = False,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        if clear:
            count = clear_skipped_sources(conn, harness=harness)
            conn.commit()
            if json_output:
                typer.echo(json.dumps({"cleared": count}, indent=2))
            else:
                typer.echo(f"Cleared {count} skipped source(s).")
            return
        rows = [dict(row) for row in list_skipped_sources(conn)]
    finally:
        conn.close()
    if harness is not None:
        rows = [row for row in rows if row["harness"] == harness]
    if json_output:
        typer.echo(json.dumps({"skipped_sources": rows}, indent=2))
        return
    if not rows:
        typer.echo("No skipped sources.")
        return
    _print_table(
        rows,
        ["harness", "source_path", "reason", "updated_at_ms"],
        headers={
            "harness": "harness",
            "source_path": "source path",
            "reason": "reason",
            "updated_at_ms": "updated at ms",
        },
        rich_output=False,
    )


@sources_app.command("sessions", help="List source sessions for a harness.")
def sources_sessions(
    ctx: typer.Context,
    harness: Annotated[str, typer.Argument(help="Harness name.")],
    source_session_id: SourceSessionArgument = None,
    source: SourcePathOption = None,
    last: LastOption = False,
    breakdown: BreakdownOption = False,
    json_output: JsonOption = False,
    utc: UtcOption = False,
    limit: LimitOption = None,
    sort: SortOption = "last",
    columns: ColumnsOption = None,
    rich_output: RichOption = False,
) -> None:
    _run_source_sessions_command(
        ctx,
        harness,
        source_path=source,
        source_session_id=source_session_id,
        last=last,
        breakdown=breakdown,
        json_output=json_output,
        utc=utc,
        limit=limit,
        sort=sort,
        columns=columns,
        rich_output=rich_output,
    )


@sources_app.command("session", help="Show detailed info for a single source session.")
def sources_session(
    ctx: typer.Context,
    harness: Annotated[str, typer.Argument(help="Harness name.")],
    source_session_id: Annotated[str, typer.Argument(help="Source session id.")],
    source: SourcePathOption = None,
    breakdown: BreakdownOption = False,
    json_output: JsonOption = False,
    utc: UtcOption = False,
    rich_output: RichOption = False,
) -> None:
    _run_source_sessions_command(
        ctx,
        harness,
        source_path=source,
        source_session_id=source_session_id,
        last=False,
        breakdown=breakdown,
        json_output=json_output,
        utc=utc,
        limit=None,
        sort="last",
        columns=None,
        rich_output=rich_output,
    )


@analyze_app.command("cache", help="Analyze cache hit rates for a session.")
def analyze_cache(
    ctx: typer.Context,
    harness: Annotated[str, typer.Argument(help="Harness name to analyze.")],
    source_session_id: SourceSessionArgument = None,
    source_path: SourcePathOption = None,
    last: LastOption = False,
    json_output: JsonOption = False,
    utc: UtcOption = False,
    refresh: RefreshOption = True,
    use_active_run: Annotated[
        bool,
        typer.Option(
            "--active-run/--all-runs",
            help="When enabled, constrain state analysis to the active run if present.",
        ),
    ] = False,
    cluster_tolerance: Annotated[
        float,
        typer.Option(
            "--cluster-tolerance",
            min=0.0,
            help="Prompt-like tolerance for cache-cost clustering.",
        ),
    ] = 0.05,
    include_calls: Annotated[
        bool,
        typer.Option("--calls/--no-calls", help="Include per-call rows in output."),
    ] = True,
    rich_output: RichOption = False,
) -> None:
    analyze_parts.analyze_cache_impl(
        ctx,
        harness=harness,
        source_session_id=source_session_id,
        source_path=source_path,
        last=last,
        json_output=json_output,
        utc=utc,
        refresh=refresh,
        use_active_run=use_active_run,
        cluster_tolerance=cluster_tolerance,
        include_calls=include_calls,
        rich_output=rich_output,
    )


@analyze_app.command("session", help="Show a detailed digest of a harness session.")
def analyze_session(
    ctx: typer.Context,
    harness: Annotated[str, typer.Argument(help="Harness name to analyze.")],
    source_session_id: SourceSessionArgument = None,
    source_path: SourcePathOption = None,
    last: LastOption = False,
    bad_calls: Annotated[
        bool,
        typer.Option(
            "--bad-calls/--no-bad-calls",
            help="Show failed/timed-out tool calls.",
        ),
    ] = False,
    all_tool_calls: Annotated[
        bool,
        typer.Option(
            "--all-tool-calls", help="Show all tool calls, not only bad calls."
        ),
    ] = False,
    show_output: Annotated[
        bool,
        typer.Option("--show-output", help="Include stderr/stdout snippets."),
    ] = False,
    show_args: Annotated[
        bool,
        typer.Option("--show-args/--hide-args", help="Include command/tool arguments."),
    ] = True,
    tool_limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Maximum tool calls to print."),
    ] = None,
    max_snippet_chars: Annotated[
        int,
        typer.Option(
            "--max-snippet-chars", min=80, help="Max stdout/stderr chars per call."
        ),
    ] = 1000,
    raw_tool_json: Annotated[
        bool,
        typer.Option(
            "--raw-tool-json", help="Include raw tool JSON in JSON output only."
        ),
    ] = False,
    json_output: JsonOption = False,
    utc: UtcOption = False,
    refresh: RefreshOption = True,
    persist: Annotated[
        bool,
        typer.Option("--persist/--no-persist", help="Store the generated digest."),
    ] = False,
    include_snippets: Annotated[
        bool,
        typer.Option(
            "--include-snippets",
            help="Reserved for future explicit transcript snippets.",
        ),
    ] = False,
    details: Annotated[
        bool,
        typer.Option(
            "--details",
            help="Show the full session digest instead of the compact report.",
        ),
    ] = False,
    rich_output: RichOption = False,
) -> None:
    analyze_parts.analyze_session_impl(
        ctx,
        harness=harness,
        source_session_id=source_session_id,
        source_path=source_path,
        last=last,
        bad_calls=bad_calls,
        all_tool_calls=all_tool_calls,
        show_output=show_output,
        tool_limit=tool_limit,
        max_snippet_chars=max_snippet_chars,
        raw_tool_json=raw_tool_json,
        json_output=json_output,
        utc=utc,
        refresh=refresh,
        persist=persist,
        include_snippets=include_snippets,
        details=details,
        rich_output=rich_output,
    )



@analyze_app.command(
    "digests",
    help="Backfill persisted session digests for tool-usage stats.",
)
def analyze_digests(
    ctx: typer.Context,
    period: UsagePeriodOption = None,
    since: Annotated[str | None, typer.Option("--since")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
    harness: HarnessOption = None,
    area: AreaOption = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Max sessions to process.")
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh/--no-refresh",
            help="Re-import before digesting."
        )
    ] = False,
    json_output: JsonOption = False,
) -> None:
    from toktrail.api.analysis import backfill_session_digests
    from toktrail.periods import _resolve_timezone

    _ = _resolve_timezone(timezone_name=timezone_name, utc=utc)

    report = backfill_session_digests(
        db_path=_resolve_state_db(ctx),
        config_path=_resolve_config_path(ctx),
        period=period,
        timezone=timezone_name,
        utc=utc,
        harness=harness,
        area=area,
        limit=limit,
        refresh=refresh,
    )

    if json_output:
        typer.echo(json.dumps({
            "scanned": report.scanned,
            "persisted": report.persisted,
            "skipped": report.skipped,
            "failed": report.failed,
            "warnings": list(report.warnings),
        }, indent=2))
        return

    typer.echo(f"Scanned: {report.scanned}")
    typer.echo(f"Persisted: {report.persisted}")
    typer.echo(f"Skipped: {report.skipped}")
    typer.echo(f"Failed: {report.failed}")
    if report.warnings:
        typer.echo("Warnings:")
        for w in report.warnings[:10]:
            typer.echo(f"  {w}")
        if len(report.warnings) > 10:
            typer.echo(f"  ... and {len(report.warnings) - 10} more")

@copilot_app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def copilot_run(
    ctx: typer.Context,
    run_id: RunOption = None,
    no_import: Annotated[bool, typer.Option("--no-import")] = False,
    no_raw: NoRawOption = False,
    otel_file: Annotated[Path | None, typer.Option("--otel-file")] = None,
) -> None:
    command = list(ctx.args)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        _exit_with_error("Missing command after '--'.")

    path = (otel_file or new_copilot_otel_file_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for key, value in _copilot_env_vars(path):
        env[key] = value

    completed = subprocess.run(command, env=env, check=False)
    typer.echo(f"Copilot OTEL file: {path}")

    if not no_import:
        result = _run_harness_import(
            ctx,
            harness_name="copilot",
            source_path=path,
            tracking_session_id=run_id,
            source_session_id=None,
            since_start=False,
            include_raw_json=not no_raw,
        )
        _print_refresh_result(result)

    raise typer.Exit(completed.returncode)


def _copilot_env_vars(path: Path) -> tuple[CopilotEnvVar, ...]:
    path_str = str(path)
    return (
        ("COPILOT_OTEL_ENABLED", "true"),
        ("COPILOT_OTEL_EXPORTER_TYPE", "file"),
        ("COPILOT_OTEL_FILE_EXPORTER_PATH", path_str),
        ("TOKTRAIL_COPILOT_FILE", path_str),
    )


def _quote_fish(value: str) -> str:
    return "'" + value.replace("'", "\\'") + "'"


def _quote_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _render_copilot_env_lines(
    shell: str,
    values: tuple[CopilotEnvVar, ...],
) -> list[str]:
    normalized = shell.lower()

    if normalized in {"bash", "zsh"}:
        return [f"export {key}={shlex.quote(value)}" for key, value in values]

    if normalized == "fish":
        return [f"set -gx {key} {_quote_fish(value)}" for key, value in values]

    if normalized in {"nu", "nushell"}:
        return [f"$env.{key} = {json.dumps(value)}" for key, value in values]

    if normalized in {"powershell", "pwsh"}:
        return [f"$env:{key} = {_quote_powershell(value)}" for key, value in values]

    _exit_with_error("Unsupported shell. Use bash, zsh, fish, nu, or powershell.")


def _render_copilot_env_json(values: tuple[CopilotEnvVar, ...]) -> str:
    return json.dumps(dict(values), indent=2) + "\n"


@copilot_app.command("env", help="Print env vars for Copilot CLI OTEL export.")
def copilot_env(
    shell: Annotated[str, typer.Argument()],
    otel_file: Annotated[Path | None, typer.Option("--otel-file")] = None,
    json_output: JsonOption = False,
) -> None:
    try:
        environment = prepare_api_environment(
            "copilot",
            source_path=otel_file,
            shell=shell,
        )
    except InvalidAPIUsageError as exc:
        _exit_with_error(str(exc))
    if json_output:
        typer.echo(json.dumps(environment.env, indent=2) + "\n", nl=False)
        return
    for line in environment.shell_exports:
        typer.echo(line)


def cli_main() -> None:
    app()


# Refresh/watch helpers moved to dedicated modules.
_refresh_before_report = refresh_parts.refresh_before_report
_wrap_refresh_json_payload = refresh_parts.wrap_refresh_json_payload
_run_harness_import = refresh_parts.run_harness_import
_run_harness_import_with_dry_run = refresh_parts.run_harness_import_with_dry_run
_missing_source_path_message = refresh_parts.missing_source_path_message
_print_refresh_result = refresh_parts.print_refresh_result
_print_configured_refresh_results = refresh_parts.print_configured_refresh_results

_resolve_watch_session_id = watch_parts.resolve_watch_session_id
_watch_report = watch_parts.watch_report
_message_count = watch_parts.message_count
_watch_totals_from_report = watch_parts.watch_totals_from_report
_subtract_tokens = watch_parts.subtract_tokens
_subtract_costs = watch_parts.subtract_costs
_subtract_totals = watch_parts.subtract_totals
_watch_delta_has_activity = watch_parts.watch_delta_has_activity
_by_harness_totals = watch_parts.by_harness_totals
_watch_delta = watch_parts.watch_delta
_print_watch_start = watch_parts.print_watch_start
_print_watch_delta = watch_parts.print_watch_delta
_print_watch_delta_json = watch_parts.print_watch_delta_json
_print_watch_stop = watch_parts.print_watch_stop
_watch_configured = watch_parts.watch_configured


def _run_source_sessions_command(
    ctx: typer.Context,
    harness_name: str,
    *,
    source_path: Path | None,
    source_session_id: str | None,
    last: bool,
    breakdown: bool,
    json_output: bool,
    utc: bool,
    limit: int | None,
    sort: str,
    columns: str | None,
    rich_output: bool,
) -> None:
    if source_session_id is not None and last:
        _exit_with_error("Use either a source session id or --last, not both.")

    harness = get_harness(harness_name)
    costing_config = _load_costing_config_or_exit(ctx)
    resolved_source = harness.resolve_source_path(source_path)
    if resolved_source is None or not resolved_source.exists():
        _exit_with_error(
            _missing_source_path_message(
                harness_name,
                resolved_source,
                explicit_source=source_path,
            )
        )

    summaries = _sorted_source_sessions(
        harness.list_sessions(resolved_source, costing_config=costing_config),
        sort=sort,
    )
    if not summaries:
        typer.echo(f"No importable {harness.display_name} assistant messages found.")
        return

    if source_session_id is None and not last:
        limited = summaries[:limit] if limit is not None else summaries
        _print_source_session_list(
            limited,
            json_output=json_output,
            utc=utc,
            columns=columns,
            rich_output=rich_output,
        )
        return

    selected = (
        summaries[0]
        if last
        else _find_source_session_summary(summaries, source_session_id)
    )
    if selected is None:
        _exit_with_error(
            f"{harness.display_name} source session not found: {source_session_id}"
        )

    events = harness.scan(
        resolved_source,
        source_session_id=selected.source_session_id,
        include_raw_json=False,
    ).events
    _print_source_session_detail(
        harness.display_name,
        selected,
        events,
        costing_config=costing_config,
        breakdown=breakdown,
        json_output=json_output,
        utc=utc,
        rich_output=rich_output,
    )


def _print_source_session_list(
    summaries: list[SourceSessionSummary],
    *,
    json_output: bool,
    utc: bool,
    columns: str | None,
    rich_output: bool,
) -> None:
    if json_output:
        payload = [_source_session_summary_payload(summary) for summary in summaries]
        typer.echo(json.dumps(payload, indent=2))
        return

    selected_columns = _normalize_source_session_columns(columns)
    headers = {
        "source_session_id": "source_session_id",
        "first": "first",
        "last": "last",
        "msgs": "msgs",
        "input": "input",
        "output": "output",
        "reasoning": "reasoning",
        "cache_r": "cache_r",
        "cache_w": "cache_w",
        "total": "total",
        "source_cost": "source_cost",
        "actual": "actual",
        "virtual": "virtual",
        "savings": "savings",
        "providers": "providers",
        "models": "models",
        "source_paths": "source_paths",
    }
    rows = [
        {
            "source_session_id": summary.source_session_id,
            "first": format_epoch_ms_compact(summary.first_created_ms, utc=utc),
            "last": format_epoch_ms_compact(summary.last_created_ms, utc=utc),
            "msgs": _format_int(summary.assistant_message_count),
            "input": _format_int(summary.tokens.input),
            "output": _format_int(summary.tokens.output),
            "reasoning": _format_int(summary.tokens.reasoning),
            "cache_r": _format_int(summary.tokens.cache_read),
            "cache_w": _format_int(summary.tokens.cache_write),
            "total": _format_int(summary.tokens.total),
            "source_cost": _format_cost(summary.source_cost_usd),
            "actual": _format_cost(summary.actual_cost_usd),
            "virtual": _format_cost(summary.virtual_cost_usd),
            "savings": _format_cost(summary.savings_usd),
            "providers": ",".join(summary.providers),
            "models": ",".join(summary.models),
            "source_paths": ";".join(summary.source_paths),
        }
        for summary in summaries
    ]
    _print_table(
        rows,
        selected_columns,
        headers,
        rich_output=rich_output,
        numeric_columns={
            "msgs",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "total",
            "source_cost",
            "actual",
            "virtual",
            "savings",
        },
        wrap_columns={"providers", "models", "source_paths"},
        max_widths={"providers": 24, "models": 32, "source_paths": 48},
    )


def _print_source_session_detail(
    harness_display_name: str,
    summary: SourceSessionSummary,
    events: list[UsageEvent],
    *,
    costing_config: CostingConfig,
    breakdown: bool,
    json_output: bool,
    utc: bool,
    rich_output: bool,
) -> None:
    totals = summarize_event_totals(events, costing_config=costing_config)
    by_model = summarize_events_by_model(events, costing_config=costing_config)
    by_activity = summarize_events_by_activity(events, costing_config=costing_config)

    if json_output:
        totals_payload = totals.as_dict()
        totals_payload["cache_output"] = totals.tokens.cache_output
        typer.echo(
            json.dumps(
                {
                    "harness": harness_display_name.lower(),
                    "source_session_id": summary.source_session_id,
                    "source_paths": list(summary.source_paths),
                    "first_created_ms": summary.first_created_ms,
                    "last_created_ms": summary.last_created_ms,
                    "assistant_message_count": summary.assistant_message_count,
                    "totals": totals_payload,
                    "by_model": [row.as_dict() for row in by_model],
                    "by_activity": [row.as_dict() for row in by_activity],
                },
                indent=2,
            )
        )
        return

    typer.echo(f"{harness_display_name} source session {summary.source_session_id}")
    typer.echo(
        f"first:    {format_epoch_ms_compact(summary.first_created_ms, utc=utc)}"
    )
    typer.echo(f"last:     {format_epoch_ms_compact(summary.last_created_ms, utc=utc)}")
    typer.echo(f"messages: {summary.assistant_message_count}")
    if summary.source_paths:
        typer.echo(f"source:   {', '.join(summary.source_paths)}")
    typer.echo("")
    typer.echo("Totals")
    typer.echo(f"  {_format_token_usage_line(totals.tokens)}")
    typer.echo("Costs")
    typer.echo(f"  source:   {_format_cost(totals.source_cost_usd)}")
    typer.echo(f"  actual:   {_format_cost(totals.actual_cost_usd)}")
    typer.echo(f"  virtual:  {_format_cost(totals.virtual_cost_usd)}")
    typer.echo(f"  savings:  {_format_cost(totals.savings_usd)}")
    typer.echo(f"  unpriced: {totals.unpriced_count} model groups")

    if not breakdown:
        return

    typer.echo("")
    typer.echo("By model")
    _print_model_table(by_model, rich_output=rich_output)
    if by_activity:
        typer.echo("")
        typer.echo("By activity")
        for row in by_activity:
            cache_info = ""
            if row.tokens.cache_read:
                cache_info = f"   cached input {_format_int(row.tokens.cache_read)}"
            typer.echo(
                f"  {row.agent:<12}"
                f"{_format_int(row.total_tokens):>12} tokens"
                f"{cache_info}   "
                f"actual {_format_cost(row.actual_cost_usd)}   "
                f"virtual {_format_cost(row.virtual_cost_usd)}   "
                f"savings {_format_cost(row.savings_usd)}"
            )


def _source_session_summary_payload(
    summary: SourceSessionSummary,
) -> dict[str, object]:
    return {
        "harness": summary.harness,
        "source_session_id": summary.source_session_id,
        "first_created_ms": summary.first_created_ms,
        "last_created_ms": summary.last_created_ms,
        "assistant_message_count": summary.assistant_message_count,
        "tokens": summary.tokens.as_dict(),
        **summary.costs.as_dict(),
        "providers": list(summary.providers),
        "models": list(summary.models),
        "source_paths": list(summary.source_paths),
    }


def _sorted_source_sessions(
    summaries: list[SourceSessionSummary],
    *,
    sort: str,
) -> list[SourceSessionSummary]:
    if sort == "last":
        return sorted(
            summaries,
            key=lambda summary: (summary.last_created_ms, summary.source_session_id),
            reverse=True,
        )
    if sort == "tokens":
        return sorted(
            summaries,
            key=lambda summary: (
                summary.tokens.total,
                summary.last_created_ms,
                summary.source_session_id,
            ),
            reverse=True,
        )
    if sort == "actual":
        return sorted(
            summaries,
            key=lambda summary: (
                summary.actual_cost_usd,
                summary.last_created_ms,
                summary.source_session_id,
            ),
            reverse=True,
        )
    if sort == "virtual":
        return sorted(
            summaries,
            key=lambda summary: (
                summary.virtual_cost_usd,
                summary.last_created_ms,
                summary.source_session_id,
            ),
            reverse=True,
        )
    if sort == "savings":
        return sorted(
            summaries,
            key=lambda summary: (
                summary.savings_usd,
                summary.last_created_ms,
                summary.source_session_id,
            ),
            reverse=True,
        )
    _exit_with_error("Unsupported sort. Use last, tokens, actual, virtual, or savings.")


def _find_source_session_summary(
    summaries: list[SourceSessionSummary],
    source_session_id: str | None,
) -> SourceSessionSummary | None:
    for summary in summaries:
        if summary.source_session_id == source_session_id:
            return summary
    return None


def _normalize_source_session_columns(columns: str | None) -> list[str]:
    default_columns = [
        "source_session_id",
        "first",
        "last",
        "msgs",
        "input",
        "output",
        "reasoning",
        "cache_r",
        "cache_w",
        "total",
        "actual",
        "virtual",
        "savings",
    ]
    if columns is None:
        return default_columns

    selected = [value.strip() for value in columns.split(",") if value.strip()]
    allowed = set(
        default_columns + ["source_cost", "providers", "models", "source_paths"]
    )
    invalid = [value for value in selected if value not in allowed]
    if invalid:
        _exit_with_error(f"Unsupported columns: {', '.join(invalid)}")
    return selected or default_columns


def _price_rows(config: CostingConfig, table: str) -> list[dict[str, object]]:
    tables: list[tuple[str, tuple[Price, ...]]] = []
    if table in {"virtual", "all"}:
        tables.append(("virtual", config.virtual_prices))
    if table in {"actual", "all"}:
        tables.append(("actual", config.actual_prices))

    rows: list[dict[str, object]] = []
    for table_name, prices in tables:
        for price in prices:
            rows.append(
                {
                    "table": table_name,
                    "provider": price.provider,
                    "model": price.model,
                    "aliases": list(price.aliases),
                    "input_usd_per_1m": price.input_usd_per_1m,
                    "cached_input_usd_per_1m": price.cached_input_usd_per_1m,
                    "effective_cached_input_usd_per_1m": (
                        price.cached_input_usd_per_1m
                        if price.cached_input_usd_per_1m is not None
                        else price.input_usd_per_1m
                    ),
                    "cache_write_usd_per_1m": price.cache_write_usd_per_1m,
                    "effective_cache_write_usd_per_1m": (
                        price.cache_write_usd_per_1m
                        if price.cache_write_usd_per_1m is not None
                        else price.input_usd_per_1m
                    ),
                    "cached_output_usd_per_1m": price.cached_output_usd_per_1m,
                    "effective_cached_output_usd_per_1m": (
                        price.cached_output_usd_per_1m
                        if price.cached_output_usd_per_1m is not None
                        else price.output_usd_per_1m
                    ),
                    "output_usd_per_1m": price.output_usd_per_1m,
                    "reasoning_usd_per_1m": price.reasoning_usd_per_1m,
                    "effective_reasoning_usd_per_1m": (
                        price.reasoning_usd_per_1m
                        if price.reasoning_usd_per_1m is not None
                        else price.output_usd_per_1m
                    ),
                    "context_min_tokens": price.context_min_tokens,
                    "context_max_tokens": price.context_max_tokens,
                    "context_label": price.context_label,
                    "context_basis": price.context_basis,
                    "category": price.category,
                    "release_status": price.release_status,
                }
            )
    return rows


def _print_price_table(
    rows: list[dict[str, object]],
    *,
    aliases: bool,
    rich_output: bool,
) -> None:
    headers = {
        "table": "table",
        "provider": "provider",
        "model": "model",
        "context": "context",
        "aliases": "aliases",
        "input": "input",
        "cached_input": "cached_input",
        "cache_write": "cache_write",
        "output": "output",
        "reasoning": "reasoning",
        "category": "category",
        "release": "release",
    }
    payload_rows = [
        {
            "table": str(row["table"]),
            "provider": str(row["provider"]),
            "model": str(row["model"]),
            "context": _format_price_context(row),
            "aliases": ", ".join(_aliases_from_row(row)),
            "input": _format_price(_as_float_or_none(row["input_usd_per_1m"])),
            "cached_input": _format_price(
                _as_float_or_none(row["cached_input_usd_per_1m"])
            ),
            "cache_write": _format_price(
                _as_float_or_none(row["cache_write_usd_per_1m"]),
                fallback="input",
            ),
            "output": _format_price(_as_float_or_none(row["output_usd_per_1m"])),
            "reasoning": _format_price(
                _as_float_or_none(row["reasoning_usd_per_1m"]),
                fallback="output",
            ),
            "category": str(row.get("category") or "-"),
            "release": str(row.get("release_status") or "-"),
        }
        for row in rows
    ]
    columns = ["table", "provider", "model", "context"]
    if aliases:
        columns.append("aliases")
    columns.extend(
        [
            "input",
            "cached_input",
            "cache_write",
            "output",
            "reasoning",
            "category",
            "release",
        ]
    )
    _print_table(
        payload_rows,
        columns,
        headers,
        rich_output=rich_output,
        numeric_columns={
            "input",
            "cached_input",
            "cache_write",
            "output",
            "reasoning",
        },
        wrap_columns={"aliases"},
        max_widths={"aliases": 32},
    )


def _format_price_context(row: dict[str, object]) -> str:
    label = row.get("context_label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    min_tokens = row.get("context_min_tokens")
    max_tokens = row.get("context_max_tokens")
    minimum = (
        min_tokens
        if isinstance(min_tokens, int) and not isinstance(min_tokens, bool)
        else None
    )
    maximum = (
        max_tokens
        if isinstance(max_tokens, int) and not isinstance(max_tokens, bool)
        else None
    )
    if minimum is None and maximum is None:
        return "-"
    if minimum is None:
        return f"<= {_format_int(maximum)}" if maximum is not None else "-"
    if maximum is None:
        if minimum > 0:
            return f"> {_format_int(minimum - 1)}"
        return f">= {_format_int(minimum)}"
    return f"{_format_int(minimum)}..{_format_int(maximum)}"


def _print_session_cache_analysis_report(
    report: SessionCacheAnalysisReport,
    *,
    utc: bool,
    include_calls: bool,
    rich_output: bool,
) -> None:
    typer.echo(f"{report.harness} source session {report.source_session_id}")
    if report.first_created_ms is not None and report.last_created_ms is not None:
        typer.echo(
            "window: "
            f"{format_epoch_ms_compact(report.first_created_ms, utc=utc)}.."
            f"{format_epoch_ms_compact(report.last_created_ms, utc=utc)}"
        )
    typer.echo(
        f"calls: {report.call_count}   "
        f"source {_format_cost_precise(report.source_cost_usd)}   "
        f"virtual {_format_cost_precise(report.virtual_cost_usd)}   "
        f"virtual uncached {_format_cost_precise(report.virtual_uncached_cost_usd)}"
    )
    typer.echo(
        f"cache read: {_format_int(report.cache_read_tokens)} / "
        f"{_format_int(report.prompt_like_tokens)} prompt-like tokens   "
        f"reuse {_format_ratio_percent(report.cache_reuse_ratio)}   "
        f"presence {_format_ratio_percent(report.cache_presence_ratio)}"
    )
    typer.echo(
        "estimated source cache loss: "
        f"{_format_cost_precise(report.estimated_source_cache_loss_usd)}"
    )
    if report.totals.costs.unpriced_count > 0:
        typer.echo(
            f"pricing: {report.totals.costs.unpriced_count} unpriced calls (virtual)"
        )
    if report.warnings:
        typer.echo(f"warnings: {', '.join(report.warnings)}")

    if include_calls and report.calls:
        typer.echo("")
        typer.echo("Per call")
        call_rows = [
            {
                "n": str(row.ordinal),
                "time": format_epoch_ms_compact(row.created_ms, utc=utc),
                "model": row.model_id,
                "context": _format_int(row.context_tokens),
                "tier": row.virtual_price_context_label or "-",
                "prompt": _format_int(row.prompt_like_tokens),
                "cache_r": _format_int(row.tokens.cache_read),
                "cache%": _format_ratio_percent(row.cache_reuse_ratio),
                "out": _format_int(row.tokens.output),
                "source": _format_cost_precise(row.source_cost_usd),
                "virtual": _format_cost_precise(row.virtual_cost_usd),
                "uncached": _format_cost_precise(row.virtual_uncached_cost_usd),
                "save": _format_cost_precise(row.virtual_cache_savings_usd),
                "src_1m_prompt": _format_cost_or_dash(
                    row.source_cost_per_1m_prompt_like
                ),
                "status": row.cache_status,
                "flags": ",".join(row.flags),
            }
            for row in report.calls
        ]
        _print_table(
            call_rows,
            [
                "n",
                "time",
                "model",
                "context",
                "tier",
                "prompt",
                "cache_r",
                "cache%",
                "out",
                "source",
                "virtual",
                "uncached",
                "save",
                "src_1m_prompt",
                "status",
                "flags",
            ],
            {
                "n": "#",
                "time": "time",
                "model": "model",
                "context": "context",
                "tier": "tier",
                "prompt": "prompt",
                "cache_r": "cache_r",
                "cache%": "cache%",
                "out": "out",
                "source": "source",
                "virtual": "virtual",
                "uncached": "uncached",
                "save": "save",
                "src_1m_prompt": "src$/1M prompt",
                "status": "status",
                "flags": "flags",
            },
            rich_output=rich_output,
            numeric_columns={
                "n",
                "context",
                "prompt",
                "cache_r",
                "out",
                "source",
                "virtual",
                "uncached",
                "save",
                "src_1m_prompt",
            },
            wrap_columns={"model", "flags"},
            max_widths={"model": 28, "flags": 24},
        )

    if report.clusters:
        typer.echo("")
        typer.echo("Clusters")
        cluster_rows = [
            {
                "model": row.model_id,
                "thinking": row.thinking_level or "-",
                "calls": _format_int(row.call_count),
                "hits": _format_int(row.hit_count),
                "misses": _format_int(row.miss_count),
                "range": (
                    f"{_format_int(row.prompt_like_min)}.."
                    f"{_format_int(row.prompt_like_max)}"
                ),
                "hit_median": _format_cost_or_dash(row.median_hit_source_cost_usd),
                "miss_median": _format_cost_or_dash(row.median_miss_source_cost_usd),
                "loss": _format_cost_precise(row.estimated_source_loss_usd),
                "ordinals": ",".join(str(value) for value in row.call_ordinals),
            }
            for row in report.clusters
        ]
        _print_table(
            cluster_rows,
            [
                "model",
                "thinking",
                "calls",
                "hits",
                "misses",
                "range",
                "hit_median",
                "miss_median",
                "loss",
                "ordinals",
            ],
            {
                "model": "model",
                "thinking": "thinking",
                "calls": "calls",
                "hits": "hits",
                "misses": "misses",
                "range": "prompt range",
                "hit_median": "hit median",
                "miss_median": "miss median",
                "loss": "est. loss",
                "ordinals": "call #",
            },
            rich_output=rich_output,
            numeric_columns={
                "calls",
                "hits",
                "misses",
                "hit_median",
                "miss_median",
                "loss",
            },
            wrap_columns={"ordinals"},
            max_widths={"ordinals": 28},
        )


def _print_session_digest(
    digest: SessionDigest,
    *,
    utc: bool,
    rich_output: bool,
) -> None:
    def _health_summary() -> str:
        if digest.health is None:
            return "- unknown (low)"
        score = "-" if digest.health.score is None else _format_int(digest.health.score)
        grade = digest.health.grade or "-"
        return (
            f"{grade} {score} {digest.health.outcome} "
            f"({digest.health.outcome_confidence})"
        )

    typer.echo(f"{digest.harness} source session {digest.source_session_id}")
    if digest.area_path:
        typer.echo(f"Area:       {digest.area_path}")
    if digest.machine_label:
        typer.echo(f"Machine:    {digest.machine_label}")
    if digest.cwd or digest.source_dir:
        typer.echo(f"Where:      {digest.cwd or digest.source_dir}")
    if digest.git_remote:
        typer.echo(f"Git:        {digest.git_remote}")
    if digest.models:
        typer.echo(f"Models:     {', '.join(digest.models)}")
    if digest.providers:
        typer.echo(f"Providers:  {', '.join(digest.providers)}")
    if digest.started_ms is not None and digest.last_seen_ms is not None:
        typer.echo(
            "When:       "
            f"{format_epoch_ms_compact(digest.started_ms, utc=utc)}.."
            f"{format_epoch_ms_compact(digest.last_seen_ms, utc=utc)}"
        )
    typer.echo(
        "Usage:      "
        f"messages={_format_int(digest.message_count)} "
        f"tokens={_format_int(digest.usage.tokens.total)} "
        f"actual={_format_cost(digest.usage.costs.actual_cost_usd)} "
        f"virtual={_format_cost(digest.usage.costs.virtual_cost_usd)}"
    )
    typer.echo("")
    typer.echo("Summary")
    typer.echo(f"  {digest.summary.one_line or 'No summary available.'}")
    for bullet in digest.summary.bullets:
        typer.echo(f"  - {bullet}")
    typer.echo("")
    typer.echo("Tool health")
    typer.echo(f"  Tool calls:   {_format_int(digest.tool_health.tool_call_count)}")
    typer.echo(f"  Failures:     {_format_int(digest.tool_health.tool_failure_count)}")
    if digest.tool_health.failed_tools:
        failed = ", ".join(
            f"{name}:{count}"
            for name, count in sorted(digest.tool_health.failed_tools.items())
        )
        typer.echo(f"  Failed tools: {failed}")
    if digest.tool_health.warnings:
        typer.echo(f"  Warnings:     {', '.join(digest.tool_health.warnings)}")
    typer.echo("")
    typer.echo("Health")
    typer.echo(f"  Score:        {_health_summary()}")
    if digest.health is not None:
        typer.echo(
            "  Signals:      "
            f"retry_count={_format_int(digest.health.retry_count)} "
            f"edit_churn={_format_int(digest.health.edit_churn_count)} "
            f"max_failure_streak={_format_int(digest.health.consecutive_failure_max)}"
        )
        if digest.health.penalties:
            penalty_text = ", ".join(
                f"{penalty.kind} -{_format_int(penalty.points)}"
                for penalty in digest.health.penalties
            )
            typer.echo(f"  Penalties:    {penalty_text}")
    if digest.files_mentioned:
        typer.echo("")
        typer.echo("Files/paths mentioned")
        for value in digest.files_mentioned[:8]:
            typer.echo(f"  - {value}")
    if rich_output and digest.commands_mentioned:
        typer.echo("")
        typer.echo("Commands mentioned")
        for value in digest.commands_mentioned[:8]:
            typer.echo(f"  - {value}")


def _print_session_compact_report(
    report: SessionCompactReport,
    *,
    utc: bool,
) -> None:
    def _health_summary() -> str:
        if report.health is None:
            return "- unknown (low)"
        score = "-" if report.health.score is None else _format_int(report.health.score)
        grade = report.health.grade or "-"
        return (
            f"{grade} {score} {report.health.outcome} "
            f"({report.health.outcome_confidence})"
        )

    typer.echo(f"{report.harness} source session {report.source_session_id}")
    if report.session_title:
        typer.echo(f"Title:      {report.session_title}")
    if report.area_path:
        typer.echo(f"Area:       {report.area_path}")
    if report.machine_label:
        typer.echo(f"Machine:    {report.machine_label}")
    if report.cwd or report.source_dir:
        typer.echo(f"Where:      {report.cwd or report.source_dir}")
    if report.started_ms is not None and report.last_seen_ms is not None:
        typer.echo(
            "When:       "
            f"{format_epoch_ms_compact(report.started_ms, utc=utc)}.."
            f"{format_epoch_ms_compact(report.last_seen_ms, utc=utc)}"
        )
    tokens = report.usage.tokens
    costs = report.usage.costs
    typer.echo(
        "Usage:      "
        f"messages={_format_int(report.message_count)} "
        f"total={_format_int(tokens.total)} "
        f"input={_format_int(tokens.input)} "
        f"output={_format_int(tokens.output)} "
        f"reasoning={_format_int(tokens.reasoning)}"
    )
    typer.echo(
        "Cache:      "
        f"read={_format_int(tokens.cache_read)} "
        f"write={_format_int(tokens.cache_write)} "
        f"prompt={_format_int(tokens.prompt_total)} "
        f"reuse={_format_ratio_percent(report.cache_reuse_ratio)}"
    )
    typer.echo(
        "Cost:       "
        f"source={_format_cost(costs.source_cost_usd)} "
        f"actual={_format_cost(costs.actual_cost_usd)} "
        f"virtual={_format_cost(costs.virtual_cost_usd)} "
        f"unpriced={_format_int(costs.unpriced_count)}"
    )
    if report.models:
        typer.echo(f"Models:     {', '.join(report.models[:5])}")
    if report.summary is not None and report.summary.one_line:
        typer.echo(f"Summary:    {report.summary.one_line}")
    typer.echo(f"Health:     {_health_summary()}")
    if report.tool_health is not None:
        typer.echo(
            "Signals:    "
            f"tool_calls={_format_int(report.tool_health.tool_call_count)} "
            f"failures={_format_int(report.tool_health.tool_failure_count)} "
            f"timeouts={_format_int(report.tool_health.tool_timeout_count)}"
        )
    if report.health is not None and report.health.penalties:
        penalty_text = ", ".join(
            f"{penalty.kind} -{_format_int(penalty.points)}"
            for penalty in report.health.penalties
        )
        typer.echo(f"Penalties:  {penalty_text}")


def _print_session_tool_call_report(
    report: SessionToolCallReport,
    *,
    bad_only: bool,
    utc: bool,
    show_output: bool,
    rich_output: bool,
) -> None:
    typer.echo(f"{report.harness} source session {report.source_session_id}")
    call_label = "Bad tool calls" if bad_only else "Tool calls"
    call_count = len(report.calls)
    total_label = (
        f"{call_count} of {report.tool_call_count}"
        if bad_only
        else str(report.tool_call_count)
    )
    typer.echo(f"{call_label}: {total_label}   timeouts: {report.timeout_count}")

    if report.warnings:
        typer.echo(f"Warnings: {', '.join(report.warnings)}")
    typer.echo("")

    for call in report.calls:
        status_tag = call.status.upper()
        exit_info = f" exit={call.exit_code}" if call.exit_code is not None else ""
        duration_info = (
            f" {_format_int(call.duration_ms)}ms"
            if call.duration_ms is not None
            else ""
        )
        line_info = f" line={call.line_number}"
        typer.echo(
            f"#{call.ordinal}  {status_tag} {call.tool_name}"
            f"{exit_info}{duration_info}{line_info}"
        )

        if call.command:
            cmd_text = call.command
            if len(cmd_text) > 120:
                cmd_text = cmd_text[:119] + "\u2026"
            typer.echo(f"    cmd: {cmd_text}")
        if call.error:
            err_text = call.error
            if len(err_text) > 120:
                err_text = err_text[:119] + "\u2026"
            typer.echo(f"    err: {err_text}")
        if show_output and call.stderr_snippet:
            typer.echo(f"    stderr: {call.stderr_snippet}")
        if show_output and call.stdout_snippet:
            typer.echo(f"    stdout: {call.stdout_snippet}")
        if call.cwd:
            typer.echo(f"    cwd: {call.cwd}")


def _build_statusline_cli(
    ctx: typer.Context,
    *,
    harness: str | None,
    provider_id: str | None,
    model_id: str | None,
    source_session_id: str | None,
    session_mode: str | None,
    basis: str | None,
    refresh: str | None,
    no_refresh: bool,
    refresh_details: bool,
    raw: bool | None,
    max_width: int | None,
    stale_after: int | None,
) -> tuple[
    StatuslineReport,
    tuple[ImportUsageResult, ...],
    dict[str, object] | None,
    int,
]:
    loaded_config = _load_resolved_toktrail_config_or_exit(ctx)
    statusline_config = loaded_config.config.statusline
    effective_harness = harness
    if effective_harness is None and statusline_config.default_harness != "auto":
        effective_harness = statusline_config.default_harness
    effective_session_mode = _normalize_statusline_session_mode(
        session_mode or statusline_config.session
    )
    effective_basis = basis or statusline_config.basis
    requested_refresh = _normalize_statusline_refresh(
        refresh=refresh or statusline_config.refresh,
        no_refresh=no_refresh,
    )
    effective_max_width = max_width or statusline_config.max_width
    effective_stale_after = (
        stale_after
        if stale_after is not None
        else statusline_config.cache.stale_after_secs
    )
    refresh_harness = effective_harness
    report_harness = effective_harness
    if effective_harness == "harnessbridge":
        report_harness = None
    payload = _read_statusline_stdin_payload()
    request = StatuslineRequest(
        harness=effective_harness,
        provider_id=provider_id,
        model_id=model_id,
        source_session_id=source_session_id,
        session_mode=effective_session_mode,
        basis=effective_basis,
        max_width=effective_max_width,
        stale_after_seconds=effective_stale_after,
        active_session_window_minutes=statusline_config.active_session_window_minutes,
        elements=statusline_config.elements,
        stdin_payload=payload,
    )
    state_db_path = _resolve_state_db(ctx)
    resolved_source_path = _configured_statusline_source_path(
        loaded_config,
        refresh_harness,
    )
    cache_dir = statusline_cache_dir()
    cache_key = statusline_cache_key(
        state_db_path,
        request=request,
        json_output=False,
    )
    cached = None
    if requested_refresh != "always" and not refresh_details:
        cached = load_statusline_output_cache(
            cache_dir=cache_dir,
            cache_key=cache_key,
            state_db_path=state_db_path,
            config_path=loaded_config.config_path,
            source_path=resolved_source_path,
            max_age_seconds=statusline_config.cache.output_cache_secs,
        )
    if cached is not None:
        return cached, (), payload, 0
    effective_refresh = requested_refresh
    if requested_refresh == "auto" and _should_skip_statusline_auto_refresh(
        state_db_path=state_db_path,
        source_path=resolved_source_path,
        cache_metadata=load_statusline_cache_metadata(
            cache_dir=cache_dir,
            cache_key=cache_key,
        ),
        min_refresh_interval_secs=statusline_config.cache.min_refresh_interval_secs,
    ):
        effective_refresh = "never"
    started = time.perf_counter()
    refresh_results = _refresh_for_statusline(
        ctx,
        mode=effective_refresh,
        harness=refresh_harness,
        details=refresh_details,
        raw=raw,
    )
    report = statusline_report_api(
        state_db_path,
        harness=report_harness,
        provider_id=provider_id,
        model_id=model_id,
        source_session_id=source_session_id,
        session_mode=effective_session_mode,
        basis=effective_basis,
        max_width=effective_max_width,
        stale_after_seconds=effective_stale_after,
        active_session_window_minutes=statusline_config.active_session_window_minutes,
        elements=statusline_config.elements,
        stdin_payload=payload,
        config_path=loaded_config.config_path,
    )
    report = replace(
        report,
        cache=replace(
            report.cache or StatuslineCache(cached_tokens=0, cache_reuse_ratio=None),
            output_cache="miss",
        ),
    )
    write_statusline_output_cache(
        cache_dir=cache_dir,
        cache_key=cache_key,
        report=report,
        state_db_path=state_db_path,
        config_path=loaded_config.config_path,
        source_path=resolved_source_path,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return report, refresh_results, payload, elapsed_ms


def _normalize_statusline_refresh(*, refresh: str, no_refresh: bool) -> str:
    normalized = refresh.strip().lower()
    if normalized not in {"never", "auto", "always"}:
        _exit_with_error("--refresh must be one of: never, auto, always.")
    if no_refresh and normalized != "auto":
        _exit_with_error("Use either --refresh or --no-refresh, not both.")
    if no_refresh:
        return "never"
    return normalized


def _normalize_statusline_session_mode(session_mode: str) -> str:
    normalized = session_mode.strip().lower()
    if normalized not in {"auto", "latest", "none"}:
        _exit_with_error("--session must be one of: auto, latest, none.")
    return normalized


def _read_statusline_stdin_payload() -> dict[str, object] | None:
    stream = typer.get_text_stream("stdin")
    if stream.isatty():
        return None
    text = stream.read()
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        _exit_with_error(f"Invalid JSON from stdin: {exc}")
    if not isinstance(payload, dict):
        _exit_with_error("Statusline stdin payload must be a JSON object.")
    return cast(dict[str, object], payload)


def _refresh_for_statusline(
    ctx: typer.Context,
    *,
    mode: str,
    harness: str | None,
    details: bool,
    raw: bool | None,
) -> tuple[ImportUsageResult, ...]:
    if mode == "never":
        return ()
    try:
        results = import_configured_usage_api(
            _resolve_state_db(ctx),
            harnesses=[harness] if harness is not None else None,
            include_raw_json=raw,
            config_path=_resolve_config_path(ctx),
            refresh_mode="full" if mode == "always" else "quick",
        )
    except (OSError, ValueError, ToktrailError) as exc:
        _exit_with_error(str(exc))
    if details:
        _print_configured_refresh_results(results)
    return results


def _render_statusline_quota_label(report: StatuslineReport) -> str:
    quota = report.quota
    if quota is None:
        return "-"
    if quota.over_limit_usd > 0:
        return f"{quota.period} over ${float(quota.over_limit_usd):.2f}"
    if quota.percent_used is None:
        return quota.period
    return f"{quota.period} {_format_percent(quota.percent_used)}"


def _configured_statusline_source_path(
    loaded_config: LoadedToktrailConfig,
    harness: str | None,
) -> Path | None:
    if harness is None:
        return None
    sources = loaded_config.config.imports.sources or {}
    configured = sources.get(harness)
    if isinstance(configured, list):
        return configured[0] if configured else None
    if configured is not None:
        return configured
    return get_harness(harness).resolve_source_path(None)


def _should_skip_statusline_auto_refresh(
    *,
    state_db_path: Path,
    source_path: Path | None,
    cache_metadata: dict[str, object] | None,
    min_refresh_interval_secs: int,
) -> bool:
    if source_path is not None and not source_path.exists():
        return True
    if source_path is not None and not source_path.is_dir():
        state_mtime_ns = _path_mtime_ns(state_db_path)
        source_mtime_ns = _path_mtime_ns(source_path)
        if (
            state_mtime_ns is not None
            and source_mtime_ns is not None
            and source_mtime_ns <= state_mtime_ns
        ):
            return True
    if cache_metadata is None:
        return False
    created_ms = cache_metadata.get("created_ms")
    if not isinstance(created_ms, int):
        return False
    return int(time.time() * 1000) - created_ms < min_refresh_interval_secs * 1000


def _path_mtime_ns(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _statusline_install_instructions(target: str) -> str:
    if target == "starship":
        return (
            "[custom.toktrail]\n"
            'command = "toktrail statusline --no-refresh 2>/dev/null"\n'
            'when = "true"\n'
            'format = "[$output]($style) "\n'
            'style = "dimmed"'
        )
    if target == "tmux":
        return "set -g status-right '#(toktrail statusline --no-refresh 2>/dev/null)'"
    if target == "bash":
        return "export PS1='$(toktrail statusline --no-refresh 2>/dev/null) \\w $ '"
    if target == "zsh":
        return (
            "precmd() { TOKTRAIL_STATUSLINE=$(toktrail statusline --no-refresh "
            "2>/dev/null); }\nPROMPT='${TOKTRAIL_STATUSLINE} %~ %# '"
        )
    if target in {"codex", "opencode", "pi"}:
        return (
            f"Native {target} statusline installation is not auto-writing config yet.\n"
            "Use a generic shell, tmux, or starship integration for now."
        )
    _exit_with_error(
        "--target must be one of: starship, tmux, bash, zsh, codex, opencode, pi."
    )


def _statusline_config_with_override(
    config: StatuslineConfig,
    key: str,
    value: str,
) -> StatuslineConfig:
    normalized = key.strip().lower()
    updated = _statusline_config_base_override(config, normalized, value)
    if updated is not None:
        return updated
    updated = _statusline_config_cache_override(config, normalized, value)
    if updated is not None:
        return updated
    _exit_with_error(
        "Unsupported key. Use one of: basis, refresh, session, max-width, "
        "show-emojis, color, empty, default-harness, active-session-window-minutes, "
        "elements, cache.output-cache-secs, cache.min-refresh-interval-secs, "
        "cache.stale-after-secs."
    )


def _statusline_config_base_override(
    config: StatuslineConfig,
    normalized: str,
    value: str,
) -> StatuslineConfig | None:
    if normalized == "basis":
        if value not in {"source", "actual", "virtual"}:
            _exit_with_error("basis must be one of: source, actual, virtual.")
        return replace(
            config,
            basis=cast(Literal["source", "actual", "virtual"], value),
        )
    if normalized == "refresh":
        if value not in {"never", "auto", "always"}:
            _exit_with_error("refresh must be one of: never, auto, always.")
        return replace(
            config,
            refresh=cast(Literal["never", "auto", "always"], value),
        )
    if normalized == "session":
        if value not in {"auto", "latest", "none"}:
            _exit_with_error("session must be one of: auto, latest, none.")
        return replace(
            config,
            session=cast(Literal["auto", "latest", "none"], value),
        )
    if normalized == "max-width":
        return replace(config, max_width=_parse_positive_cli_int(value, "max-width"))
    if normalized == "show-emojis":
        return replace(config, show_emojis=_parse_bool_text(value, "show-emojis"))
    if normalized == "color":
        if value not in {"auto", "always", "never"}:
            _exit_with_error("color must be one of: auto, always, never.")
        return replace(
            config,
            color=cast(Literal["auto", "always", "never"], value),
        )
    if normalized == "empty":
        if value not in {"silent", "message"}:
            _exit_with_error("empty must be one of: silent, message.")
        return replace(config, empty=cast(Literal["silent", "message"], value))
    if normalized == "default-harness":
        harness = value.strip().lower()
        if harness != "auto" and harness not in {
            "opencode",
            "pi",
            "copilot",
            "codex",
            "goose",
            "droid",
            "amp",
            "claude",
            "vibe",
        }:
            _exit_with_error("default-harness must be auto or a supported harness.")
        return replace(config, default_harness=harness)
    if normalized == "active-session-window-minutes":
        return replace(
            config,
            active_session_window_minutes=_parse_positive_cli_int(
                value,
                "active-session-window-minutes",
            ),
        )
    if normalized == "elements":
        elements = tuple(
            item.strip().lower() for item in value.split(",") if item.strip()
        )
        if not elements:
            _exit_with_error(
                "elements must contain at least one comma-separated value."
            )
        return replace(config, elements=elements)
    return None


def _statusline_config_cache_override(
    config: StatuslineConfig,
    normalized: str,
    value: str,
) -> StatuslineConfig | None:
    if normalized == "cache.output-cache-secs":
        return replace(
            config,
            cache=replace(
                config.cache,
                output_cache_secs=_parse_non_negative_cli_int(
                    value,
                    "cache.output-cache-secs",
                ),
            ),
        )
    if normalized == "cache.min-refresh-interval-secs":
        return replace(
            config,
            cache=replace(
                config.cache,
                min_refresh_interval_secs=_parse_non_negative_cli_int(
                    value,
                    "cache.min-refresh-interval-secs",
                ),
            ),
        )
    if normalized == "cache.stale-after-secs":
        return replace(
            config,
            cache=replace(
                config.cache,
                stale_after_secs=_parse_non_negative_cli_int(
                    value,
                    "cache.stale-after-secs",
                ),
            ),
        )
    return None


def _parse_positive_cli_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        _exit_with_error(f"{label} must be an integer.")
    if parsed <= 0:
        _exit_with_error(f"{label} must be positive.")
    return parsed


def _parse_non_negative_cli_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        _exit_with_error(f"{label} must be an integer.")
    if parsed < 0:
        _exit_with_error(f"{label} must be non-negative.")
    return parsed


def _parse_bool_text(value: str, label: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    _exit_with_error(f"{label} must be true or false.")


def _strip_statusline_sections(text: str) -> str:
    lines = text.splitlines()
    stripped: list[str] = []
    skip_mode: str | None = None
    statusline_section_headers = {
        "[statusline]",
        "[statusline.cache]",
        "[statusline.thresholds]",
    }
    for line in lines:
        stripped_line = line.strip()
        if stripped_line in statusline_section_headers:
            skip_mode = "statusline"
            continue
        if stripped_line == "[[context_window]]":
            skip_mode = "context_window"
            continue
        if stripped_line.startswith("[") and skip_mode is not None:
            skip_mode = None
        if skip_mode is None:
            stripped.append(line)
    return "\n".join(stripped).strip()


def _render_statusline_config_sections(
    config: StatuslineConfig,
    *,
    context_windows: tuple[ContextWindowConfig, ...],
) -> str:
    lines = [
        "[statusline]",
        f'default_harness = "{config.default_harness}"',
        f'basis = "{config.basis}"',
        f'refresh = "{config.refresh}"',
        f'session = "{config.session}"',
        f"max_width = {config.max_width}",
        f"show_emojis = {'true' if config.show_emojis else 'false'}",
        f'color = "{config.color}"',
        f'empty = "{config.empty}"',
        f"active_session_window_minutes = {config.active_session_window_minutes}",
        "elements = [",
    ]
    lines.extend(f'  "{element}",' for element in config.elements)
    lines.extend(
        [
            "]",
            "",
            "[statusline.cache]",
            f"output_cache_secs = {config.cache.output_cache_secs}",
            f"min_refresh_interval_secs = {config.cache.min_refresh_interval_secs}",
            f"stale_after_secs = {config.cache.stale_after_secs}",
            "",
            "[statusline.thresholds]",
            f"quota_warning_percent = {config.thresholds.quota_warning_percent}",
            f"quota_danger_percent = {config.thresholds.quota_danger_percent}",
            f"burn_warning_percent = {config.thresholds.burn_warning_percent}",
            f"burn_danger_percent = {config.thresholds.burn_danger_percent}",
            f"context_warning_percent = {config.thresholds.context_warning_percent}",
            f"context_danger_percent = {config.thresholds.context_danger_percent}",
        ]
    )
    for window in context_windows:
        lines.extend(
            [
                "",
                "[[context_window]]",
                f'provider = "{window.provider}"',
                f'model = "{window.model}"',
                f"tokens = {window.tokens}",
            ]
        )
    return "\n".join(lines)


def _refresh_before_report(
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
    refresh_mode = _resolve_report_refresh_mode(ctx, enabled=enabled)
    if refresh_mode == "never":
        return ()
    if refresh_mode == "auto" and not details:
        loaded_config = _load_resolved_toktrail_config_or_exit(ctx)
        if _should_skip_report_auto_refresh(
            state_db_path=_resolve_state_db(ctx),
            min_refresh_interval_secs=loaded_config.config.reports.min_refresh_interval_secs,
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
        _print_configured_refresh_results(results)
    return results


def _resolve_report_refresh_mode(ctx: typer.Context, *, enabled: bool) -> str:
    source = ctx.get_parameter_source("refresh")
    if source is not None and source.name != "DEFAULT":
        return "always" if enabled else "never"
    loaded_config = _load_resolved_toktrail_config_or_exit(ctx)
    return loaded_config.config.reports.refresh


def _should_skip_report_auto_refresh(
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


def _wrap_refresh_json_payload(
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


def _resolve_config_path(ctx: typer.Context) -> Path:
    return resolve_toktrail_config_path(_config_cli_path(ctx))


def _resolve_machine_config_path(ctx: typer.Context) -> Path:
    return resolve_toktrail_machine_path(_machine_cli_path(ctx))


def _config_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    config_path = root_obj.get("config_path")
    if config_path is not None and not isinstance(config_path, Path):
        msg = "Unexpected CLI state for --config."
        raise TypeError(msg)
    return config_path


def _resolve_prices_path(ctx: typer.Context) -> Path:
    return _load_resolved_toktrail_config_for_paths(ctx).prices_path


def _prices_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    prices_path = root_obj.get("prices_path")
    if prices_path is not None and not isinstance(prices_path, Path):
        msg = "Unexpected CLI state for --prices."
        raise TypeError(msg)
    return prices_path


def _resolve_prices_dir(ctx: typer.Context) -> Path:
    return _load_resolved_toktrail_config_for_paths(ctx).prices_dir


def _prices_dir_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    prices_dir_path = root_obj.get("prices_dir_path")
    if prices_dir_path is not None and not isinstance(prices_dir_path, Path):
        msg = "Unexpected CLI state for --prices-dir."
        raise TypeError(msg)
    return prices_dir_path


def _resolve_subscriptions_path(ctx: typer.Context) -> Path:
    return _load_resolved_toktrail_config_for_paths(ctx).subscriptions_path


def _subscriptions_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    subscriptions_path = root_obj.get("subscriptions_path")
    if subscriptions_path is not None and not isinstance(subscriptions_path, Path):
        msg = "Unexpected CLI state for --subscriptions."
        raise TypeError(msg)
    return subscriptions_path


def _load_resolved_costing_config_or_exit(ctx: typer.Context) -> LoadedCostingConfig:
    try:
        return load_resolved_costing_config(
            config_cli_value=_config_cli_path(ctx),
            prices_cli_value=_prices_cli_path(ctx),
            prices_dir_cli_value=_prices_dir_cli_path(ctx),
            subscriptions_cli_value=_subscriptions_cli_path(ctx),
        )
    except ValueError as exc:
        _exit_with_error(str(exc))


def _load_resolved_toktrail_config_for_paths(
    ctx: typer.Context,
) -> LoadedToktrailConfig:
    return _load_resolved_toktrail_config_or_exit(ctx)


def _load_resolved_toktrail_config_or_exit(ctx: typer.Context) -> LoadedToktrailConfig:
    try:
        return load_resolved_toktrail_config(
            config_cli_value=_config_cli_path(ctx),
            prices_cli_value=_prices_cli_path(ctx),
            prices_dir_cli_value=_prices_dir_cli_path(ctx),
            subscriptions_cli_value=_subscriptions_cli_path(ctx),
        )
    except ValueError as exc:
        _exit_with_error(str(exc))


def _load_costing_config_or_exit(ctx: typer.Context) -> CostingConfig:
    return _load_resolved_costing_config_or_exit(ctx).config


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
    loaded_machine = _load_machine_config_or_exit(ctx)
    apply_local_machine_config(conn, loaded_machine.config)
    return conn


def _resolve_machine_id_or_exit(
    conn: sqlite3.Connection,
    machine: str | None,
) -> str | None:
    if machine is None:
        return None
    try:
        return resolve_machine_selector(conn, machine).machine_id
    except ValueError as exc:
        _exit_with_error(str(exc))


def _load_machine_config_or_exit(ctx: typer.Context) -> LoadedMachineConfig:
    try:
        return load_machine_config(_machine_cli_path(ctx))
    except ValueError as exc:
        _exit_with_error(str(exc))


def _machine_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    machine_path = root_obj.get("machine_config_path")
    if machine_path is not None and not isinstance(machine_path, Path):
        msg = "Unexpected CLI state for --machine-config."
        raise TypeError(msg)
    return machine_path


def _exit_with_error(message: str) -> NoReturn:
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


usage_parts.configure_usage_runtime(
    usage_parts.UsageRuntime(
        refresh_before_report=_refresh_before_report,
        wrap_refresh_json_payload=_wrap_refresh_json_payload,
        load_costing_config_or_exit=_load_costing_config_or_exit,
        open_toktrail_connection=_open_toktrail_connection,
        resolve_machine_id_or_exit=_resolve_machine_id_or_exit,
        load_resolved_toktrail_config_or_exit=_load_resolved_toktrail_config_or_exit,
        exit_with_error=_exit_with_error,
    )
)

prices_parts.configure_prices_runtime(
    prices_parts.PricesRuntime(
        refresh_before_report=_refresh_before_report,
        load_costing_config_or_exit=_load_costing_config_or_exit,
        open_toktrail_connection=_open_toktrail_connection,
        wrap_refresh_json_payload=_wrap_refresh_json_payload,
        load_resolved_costing_config_or_exit=_load_resolved_costing_config_or_exit,
        resolve_prices_dir=_resolve_prices_dir,
        exit_with_error=_exit_with_error,
    )
)

refresh_parts.configure_refresh_runtime(
    refresh_parts.RefreshRuntime(
        resolve_state_db=_resolve_state_db,
        resolve_config_path=_resolve_config_path,
        open_toktrail_connection=_open_toktrail_connection,
        load_resolved_toktrail_config_or_exit=_load_resolved_toktrail_config_or_exit,
        exit_with_error=_exit_with_error,
        format_int=_format_int,
        print_table=_print_table,
    )
)

watch_parts.configure_watch_runtime(
    watch_parts.WatchRuntime(
        resolve_state_db=_resolve_state_db,
        resolve_config_path=_resolve_config_path,
        open_toktrail_connection=_open_toktrail_connection,
        load_costing_config_or_exit=_load_costing_config_or_exit,
        load_resolved_toktrail_config_or_exit=_load_resolved_toktrail_config_or_exit,
        exit_with_error=_exit_with_error,
        format_int=_format_int,
        format_signed_int=_format_signed_int,
        format_token_delta=_format_token_delta,
        format_cost=_format_cost,
    )
)

analyze_parts.configure_analyze_runtime(
    analyze_parts.AnalyzeRuntime(
        resolve_state_db=_resolve_state_db,
        resolve_config_path=_resolve_config_path,
        exit_with_error=_exit_with_error,
        print_session_cache_analysis_report=_print_session_cache_analysis_report,
        print_session_digest=_print_session_digest,
        print_session_compact_report=_print_session_compact_report,
        print_session_tool_call_report=_print_session_tool_call_report,
    )
)

session_parts.configure_session_runtime(
    session_parts.SessionRuntime(
        resolve_state_db=_resolve_state_db,
        resolve_config_path=_resolve_config_path,
        open_toktrail_connection=_open_toktrail_connection,
        exit_with_error=_exit_with_error,
        print_session_digest=_print_session_digest,
        print_session_compact_report=_print_session_compact_report,
        print_session_tool_call_report=_print_session_tool_call_report,
    )
)

statusline_parts.configure_statusline_runtime(
    statusline_parts.StatuslineRuntime(
        build_statusline_cli=_build_statusline_cli,
        wrap_refresh_json_payload=_wrap_refresh_json_payload,
        statusline_install_instructions=_statusline_install_instructions,
        load_resolved_toktrail_config_or_exit=_load_resolved_toktrail_config_or_exit,
        render_statusline_quota_label=_render_statusline_quota_label,
        statusline_config_with_override=_statusline_config_with_override,
        strip_statusline_sections=_strip_statusline_sections,
        render_statusline_config_sections=_render_statusline_config_sections,
    )
)


register_machine_commands(
    machine_app,
    load_machine_config_or_exit=_load_machine_config_or_exit,
    open_toktrail_connection=_open_toktrail_connection,
    resolve_machine_config_path=_resolve_machine_config_path,
    exit_with_error=_exit_with_error,
)

register_area_commands(
    area_app,
    open_toktrail_connection=_open_toktrail_connection,
    exit_with_error=_exit_with_error,
    print_table=_print_table,
    format_int=_format_int,
    resolve_config_path=_resolve_config_path,
    load_costing_config_or_exit=_load_costing_config_or_exit,
    load_resolved_toktrail_config_or_exit=_load_resolved_toktrail_config_or_exit,
    resolve_machine_id_or_exit=_resolve_machine_id_or_exit,
)

insights_parts.configure_insights_runtime(
    insights_parts.InsightsRuntime(
        resolve_state_db=_resolve_state_db,
        resolve_config_path=_resolve_config_path,
        exit_with_error=_exit_with_error,
    )
)
