from __future__ import annotations

import datetime
import fnmatch
import json
import os
import re
import shlex
import sqlite3
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, NoReturn, cast

import typer

if TYPE_CHECKING:
    from toktrail.reporting import UsageSeriesBucket

from toktrail.adapters.base import SourceSessionSummary
from toktrail.adapters.registry import get_harness
from toktrail.adapters.summary import (
    summarize_event_totals,
    summarize_events_by_activity,
    summarize_events_by_model,
)
from toktrail.api.analysis import session_cache_analysis as session_cache_analysis_api
from toktrail.api.environment import prepare_environment as prepare_api_environment
from toktrail.api.imports import import_configured_usage as import_configured_usage_api
from toktrail.api.models import (
    ImportUsageResult,
    SessionCacheAnalysisReport,
    StatuslineCache,
    StatuslineReport,
)
from toktrail.api.models import (
    RunScope as PublicRunScope,
)
from toktrail.api.reports import stats_report as stats_report_api
from toktrail.api.sessions import list_runs
from toktrail.api.sources import capture_source_snapshot
from toktrail.api.statusline import statusline_report as statusline_report_api
from toktrail.cli_parts.filters import (
    _aliases_from_row,
    _as_float_or_none,
    _filter_model_rows,
    _filter_price_rows,
    _filter_series_buckets,
    _filter_unconfigured_models,
    _normalize_price_display_filter,
    _normalize_report_display_filter,
    _sort_series_buckets,
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
    _print_unconfigured_model_table,
)
from toktrail.cli_parts.types import (
    ImportExecutionResult,
    WatchDelta,
    WatchTotals,
)
from toktrail.cli_sync import maybe_auto_export_to_git_repo, sync_app
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
    InsertUsageResult,
    apply_local_machine_config,
    archive_area_path,
    archive_tracking_session,
    assign_area_to_source_session,
    clear_skipped_sources,
    connect,
    create_tracking_session,
    end_tracking_session,
    ensure_area,
    get_active_area_status,
    get_active_tracking_session,
    get_area_by_path,
    get_local_machine_id,
    get_tracking_session,
    insert_usage_events,
    list_areas,
    list_skipped_sources,
    merge_area_paths,
    migrate,
    move_area_path,
    normalize_area_path,
    persist_source_session_metadata,
    resolve_machine_selector,
    set_active_area,
    summarize_subscription_usage,
    summarize_usage,
    summarize_usage_areas,
    unarchive_tracking_session,
    unassign_area_from_source_session,
)
from toktrail.errors import InvalidAPIUsageError, ToktrailError
from toktrail.formatting import format_epoch_ms_compact
from toktrail.models import (
    RunScope,
    TokenBreakdown,
    UsageEvent,
    normalize_thinking_level,
)
from toktrail.paths import (
    new_copilot_otel_file_path,
    resolve_toktrail_config_path,
    resolve_toktrail_db_path,
    resolve_toktrail_machine_path,
)
from toktrail.periods import resolve_time_range
from toktrail.price_parser import (
    merge_prices_document,
    parse_price_document,
    render_prices_toml,
)
from toktrail.reporting import (
    CostTotals,
    ModelSummaryRow,
    ProviderSummaryRow,
    SubscriptionBillingPeriod,
    SubscriptionUsagePeriod,
    SubscriptionUsageReport,
    UnconfiguredModelRow,
    UsageReportFilter,
)
from toktrail.reporting import (
    RunReport as InternalRunReport,
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

app.add_typer(run_app, name="run")
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


@app.command()
def init(ctx: typer.Context) -> None:
    db_path = _resolve_state_db(ctx)
    conn = connect(db_path)
    migrate(conn)
    loaded_machine = _load_machine_config_or_exit(ctx)
    apply_local_machine_config(conn, loaded_machine.config)
    conn.close()
    typer.echo(f"Initialized toktrail database: {db_path}")


@area_app.command("create")
def area_create(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Area path.")],
    name: NameOption = None,
    json_output: JsonOption = False,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        area = ensure_area(conn, path, name=name)
        conn.commit()
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "area_id": area.id,
                    "local_id": area.id,
                    "sync_id": area.sync_id,
                    "stable_id": area.sync_id,
                    "path": area.path,
                    "name": area.name,
                    "parent_id": area.parent_id,
                    "archived_at_ms": area.archived_at_ms,
                },
                indent=2,
            )
        )
        return
    typer.echo(f"Created area: {area.path}")


@area_app.command("list")
def area_list(
    ctx: typer.Context,
    json_output: JsonOption = False,
    rich_output: RichOption = False,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        areas = list_areas(conn)
        active_status = get_active_area_status(conn)
    finally:
        conn.close()
    if json_output:
        typer.echo(
            json.dumps(
                [
                    {
                        "area_id": area.id,
                        "local_id": area.id,
                        "sync_id": area.sync_id,
                        "stable_id": area.sync_id,
                        "path": area.path,
                        "name": area.name,
                        "parent_id": area.parent_id,
                        "archived_at_ms": area.archived_at_ms,
                    }
                    for area in areas
                ],
                indent=2,
            )
        )
        return
    if not areas:
        typer.echo("No areas defined.")
        return
    active_area_id = active_status.area.id if active_status.area is not None else None
    if not verbose:
        typer.echo("area")
        for area in areas:
            depth = area.path.count("/")
            suffix = " *" if active_area_id == area.id else ""
            typer.echo(f"{'  ' * depth}{area.path}{suffix}")
        return
    _print_table(
        [
            {
                "area": f"{'  ' * area.path.count('/')}{area.path}",
                "stable_id": area.sync_id[:12],
                "local_id": _format_int(area.id),
                "active": "*" if active_area_id == area.id else "",
            }
            for area in areas
        ],
        ["area", "stable_id", "local_id", "active"],
        {
            "area": "area",
            "stable_id": "stable id",
            "local_id": "local id",
            "active": "active",
        },
        rich_output=rich_output,
        numeric_columns={"local_id"},
        wrap_columns={"area"},
        max_widths={"area": 52},
    )


@area_app.command("use")
def area_use(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Area path.")],
    create: Annotated[bool, typer.Option("--create/--no-create")] = True,
    ttl: Annotated[
        str | None,
        typer.Option(
            "--ttl",
            help="Auto-expire active area after duration like 30m, 4h, or 1d.",
        ),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option(
            "--until",
            help="Auto-expire active area at ISO local/UTC timestamp.",
        ),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    if ttl is not None and until is not None:
        _exit_with_error("Use either --ttl or --until, not both.")
    expires_at_ms = _parse_area_expiry_or_exit(ttl=ttl, until=until)
    conn = _open_toktrail_connection(ctx)
    try:
        area = get_area_by_path(conn, path)
        if area is None:
            if not create:
                _exit_with_error(f"Area not found: {path}")
            area = ensure_area(conn, path)
        set_active_area(conn, area.id, expires_at_ms=expires_at_ms)
        conn.commit()
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "active_area": {
                        "area_id": area.id,
                        "local_id": area.id,
                        "sync_id": area.sync_id,
                        "stable_id": area.sync_id,
                        "path": area.path,
                        "name": area.name,
                        "expires_at_ms": expires_at_ms,
                    }
                },
                indent=2,
            )
        )
        return
    typer.echo(f"Active area: {area.path}")
    typer.echo(
        "New source sessions imported on this machine will be assigned to this area."
    )
    typer.echo(
        "Existing imported sessions are unchanged; use "
        "`toktrail area assign --last` to move the latest session."
    )


@area_app.command("clear")
def area_clear(ctx: typer.Context) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        set_active_area(conn, None)
        conn.commit()
    finally:
        conn.close()
    typer.echo(
        "Cleared active area. New source sessions will be unassigned "
        "until another area is selected."
    )


@area_app.command("status")
def area_status(ctx: typer.Context, json_output: JsonOption = False) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        status = get_active_area_status(conn)
    finally:
        conn.close()
    active = status.area
    if json_output:
        payload: dict[str, object] = {
            "machine_id": status.machine_id,
            "machine_label": status.machine_label,
            "updated_at_ms": status.updated_at_ms,
            "expires_at_ms": status.expires_at_ms,
            "active_area": None,
        }
        if active is not None:
            payload["active_area"] = {
                "area_id": active.id,
                "local_id": active.id,
                "sync_id": active.sync_id,
                "stable_id": active.sync_id,
                "path": active.path,
                "name": active.name,
            }
        typer.echo(json.dumps(payload, indent=2))
        return
    if active is None:
        typer.echo(f"Active area ({status.machine_label}): none")
        return
    if status.expires_at_ms is None:
        typer.echo(f"Active area ({status.machine_label}): {active.path}")
        return
    expiry_text = format_epoch_ms_compact(status.expires_at_ms, utc=False)
    typer.echo(
        f"Active area ({status.machine_label}): {active.path}, expires {expiry_text}"
    )


@area_app.command("assign")
def area_assign(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Area path.")],
    session: Annotated[
        str | None,
        typer.Option(
            "--session",
            help="Session key machine/harness/source_session_id from usage sessions.",
        ),
    ] = None,
    harness: HarnessOption = None,
    source_session_id: Annotated[
        str | None,
        typer.Option("--source-session-id", "--source-session"),
    ] = None,
    machine: MachineOption = None,
    last: LastOption = False,
    all_machines: Annotated[bool, typer.Option("--all-machines")] = False,
) -> None:
    if all_machines and machine is not None:
        _exit_with_error("Use either --machine or --all-machines, not both.")
    if session is not None and (
        harness is not None or source_session_id is not None or last
    ):
        _exit_with_error(
            "Use --session by itself, or use --harness/--source-session-id, or --last."
        )
    conn = _open_toktrail_connection(ctx)
    try:
        area = ensure_area(conn, path)
        if session is not None:
            selected_machine_id, selected_harness, selected_source_session = (
                _resolve_session_key_or_exit(conn, session)
            )
        elif last:
            if source_session_id is not None:
                _exit_with_error("Use either --last or --source-session-id, not both.")
            from toktrail.db import summarize_usage_sessions
            from toktrail.reporting import UsageSessionsFilter

            costing_config = _load_costing_config_or_exit(ctx)
            default_machine_id = (
                None
                if all_machines
                else (
                    _resolve_machine_id_or_exit(conn, machine)
                    if machine is not None
                    else get_local_machine_id(conn)
                )
            )
            latest = summarize_usage_sessions(
                conn,
                UsageSessionsFilter(
                    machine_id=default_machine_id,
                    harness=harness,
                    limit=1,
                    order="desc",
                ),
                costing_config=costing_config,
            ).sessions
            if not latest:
                _exit_with_error("No source session matched --last.")
            target = latest[0]
            selected_machine_id = target.origin_machine_id
            selected_harness = target.harness
            selected_source_session = target.source_session_id
        else:
            if harness is None or source_session_id is None:
                _exit_with_error(
                    "Provide --harness and --source-session-id, "
                    "or use --last or --session."
                )
            selected_machine_id = _resolve_assignment_machine_id_or_exit(
                conn,
                harness=harness,
                source_session_id=source_session_id,
                machine=machine,
            )
            selected_harness = harness
            selected_source_session = source_session_id
        if selected_machine_id is None:
            _exit_with_error("Source session has no origin machine id.")
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=selected_machine_id,
            harness=selected_harness,
            source_session_id=selected_source_session,
        )
        conn.commit()
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    typer.echo(
        f"Assigned {area.path} to "
        f"{selected_harness}/{selected_source_session} ({selected_machine_id[:8]})."
    )


@area_app.command("unassign")
def area_unassign(
    ctx: typer.Context,
    session: Annotated[
        str | None,
        typer.Option(
            "--session",
            help="Session key machine/harness/source_session_id from usage sessions.",
        ),
    ] = None,
    harness: HarnessOption = None,
    source_session_id: Annotated[
        str | None,
        typer.Option("--source-session-id", "--source-session"),
    ] = None,
    machine: MachineOption = None,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        if session is not None:
            if harness is not None or source_session_id is not None:
                _exit_with_error(
                    "Use --session by itself, or use --harness "
                    "with --source-session-id."
                )
            machine_id, resolved_harness, resolved_source_session = (
                _resolve_session_key_or_exit(conn, session)
            )
        else:
            if harness is None or source_session_id is None:
                _exit_with_error(
                    "Provide --harness and --source-session-id, or use --session."
                )
            machine_id = _resolve_assignment_machine_id_or_exit(
                conn,
                harness=harness,
                source_session_id=source_session_id,
                machine=machine,
            )
            resolved_harness = harness
            resolved_source_session = source_session_id
        unassign_area_from_source_session(
            conn,
            origin_machine_id=machine_id,
            harness=resolved_harness,
            source_session_id=resolved_source_session,
        )
        conn.commit()
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    typer.echo(
        "Unassigned area from "
        f"{resolved_harness}/{resolved_source_session} ({machine_id[:8]})."
    )


@area_app.command("sessions")
def area_sessions(
    ctx: typer.Context,
    path: Annotated[
        str | None,
        typer.Argument(help="Area path.", show_default=False),
    ] = None,
    exact: Annotated[bool, typer.Option("--exact")] = False,
    unassigned: Annotated[bool, typer.Option("--unassigned")] = False,
    recent: Annotated[int, typer.Option("--recent")] = 20,
    harness: HarnessOption = None,
    machine: MachineOption = None,
    today: Annotated[bool, typer.Option("--today")] = False,
    yesterday: Annotated[bool, typer.Option("--yesterday")] = False,
    this_week: Annotated[bool, typer.Option("--this-week")] = False,
    last_week: Annotated[bool, typer.Option("--last-week")] = False,
    this_month: Annotated[bool, typer.Option("--this-month")] = False,
    last_month: Annotated[bool, typer.Option("--last-month")] = False,
    period: UsagePeriodOption = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
    order: Annotated[str, typer.Option("--order")] = "desc",
    json_output: JsonOption = False,
    rich_output: RichOption = False,
) -> None:
    if path is not None and unassigned:
        _exit_with_error("Use either an area path or --unassigned, not both.")
    if recent < 1:
        _exit_with_error("--recent must be positive.")
    selected_period = _resolve_usage_session_period_or_exit(
        period=period,
        today=today,
        yesterday=yesterday,
        this_week=this_week,
        last_week=last_week,
        this_month=this_month,
        last_month=last_month,
    )
    try:
        resolved_range = resolve_time_range(
            period=selected_period,
            timezone_name=timezone_name,
            utc=utc,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))

    from toktrail.db import summarize_usage_sessions
    from toktrail.reporting import UsageSessionsFilter

    conn = _open_toktrail_connection(ctx)
    try:
        machine_id = _resolve_machine_id_or_exit(conn, machine)
        report = summarize_usage_sessions(
            conn,
            UsageSessionsFilter(
                machine_id=machine_id,
                harness=harness,
                area=path,
                area_exact=exact,
                unassigned_area=unassigned,
                since_ms=resolved_range.since_ms,
                until_ms=resolved_range.until_ms,
                limit=recent,
                order=order,
            ),
            costing_config=_load_costing_config_or_exit(ctx),
        )
    finally:
        conn.close()
    if json_output:
        payload = report.as_dict()
        filters = payload.get("filters")
        if isinstance(filters, dict) and resolved_range.period is not None:
            filters["period"] = resolved_range.period
        typer.echo(json.dumps(payload, indent=2))
        return
    _print_usage_sessions(
        report,
        compact=True,
        breakdown=False,
        utc=utc,
        rich_output=rich_output,
        table=True,
        period=resolved_range.period,
    )


@area_app.command("detect")
def area_detect(
    ctx: typer.Context,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_toktrail_config_or_exit(ctx)
    rules = loaded.config.areas.rules
    cwd = Path.cwd()
    cwd_text = str(cwd)
    git_remote = _git_remote_origin(cwd)
    matched: list[tuple[int, str, str]] = []
    for index, rule in enumerate(rules):
        for pattern in rule.cwd_globs:
            expanded = str(Path(pattern).expanduser())
            if fnmatch.fnmatch(cwd_text, expanded):
                matched.append((rule.priority, rule.area, f"cwd matched {pattern}"))
                break
        else:
            for remote_pattern in rule.git_remotes:
                if git_remote and fnmatch.fnmatch(git_remote, remote_pattern):
                    matched.append(
                        (
                            rule.priority,
                            rule.area,
                            f"git remote matched {remote_pattern}",
                        )
                    )
                    break
        if matched and matched[-1][1] == rule.area:
            matched[-1] = (
                matched[-1][0],
                matched[-1][1],
                f"{matched[-1][2]} (rule {index + 1})",
            )
    detected_area: str | None = None
    reason: str | None = None
    if matched:
        matched.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _, detected_area, reason = matched[0]
    conn = _open_toktrail_connection(ctx)
    try:
        active = get_active_area_status(conn)
    finally:
        conn.close()
    active_path = active.area.path if active.area is not None else None
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "cwd": cwd_text,
                    "git_remote": git_remote,
                    "detected_area": detected_area,
                    "reason": reason,
                    "active_area": active_path,
                    "suggested_command": (
                        f"toktrail area use {detected_area}"
                        if detected_area and detected_area != active_path
                        else None
                    ),
                },
                indent=2,
            )
        )
        return
    if detected_area is None:
        typer.echo("Detected area: none")
        return
    typer.echo(f"Detected area: {detected_area}")
    if reason is not None:
        typer.echo(f"Reason: {reason}")
    typer.echo(f"Active area: {active_path or 'none'}")
    if detected_area != active_path:
        typer.echo(f"Suggestion: toktrail area use {detected_area}")


@area_app.command("bind-cwd")
def area_bind_cwd(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Area path.")],
    recursive: Annotated[bool, typer.Option("--recursive/--no-recursive")] = True,
    git_root: Annotated[bool, typer.Option("--git-root")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    base = _resolve_git_root(Path.cwd()) if git_root else Path.cwd()
    if base is None:
        _exit_with_error("Could not resolve git root.")
    glob = f"{base.expanduser()}/**" if recursive else str(base.expanduser())
    config_path = _resolve_config_path(ctx)
    rendered = (
        "\n[areas]\n"
        "auto_detect = true\n"
        "warn_on_mismatch = true\n\n"
        "[[areas.rules]]\n"
        f'area = "{path}"\n'
        f'cwd_globs = ["{glob}"]\n'
        "priority = 100\n"
    )
    if dry_run:
        typer.echo(rendered.strip())
        return
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + rendered
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(updated, encoding="utf-8")
    typer.echo(f"Added cwd rule for {path} in {config_path}")


@area_app.command("bulk-assign")
def area_bulk_assign(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Area path.")],
    unassigned: Annotated[bool, typer.Option("--unassigned")] = True,
    harness: HarnessOption = None,
    machine: MachineOption = None,
    today: Annotated[bool, typer.Option("--today")] = False,
    yesterday: Annotated[bool, typer.Option("--yesterday")] = False,
    this_week: Annotated[bool, typer.Option("--this-week")] = False,
    last_week: Annotated[bool, typer.Option("--last-week")] = False,
    this_month: Annotated[bool, typer.Option("--this-month")] = False,
    last_month: Annotated[bool, typer.Option("--last-month")] = False,
    period: UsagePeriodOption = None,
    apply: Annotated[bool, typer.Option("--apply")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = True,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    if apply and dry_run:
        _exit_with_error("Use either --apply or --dry-run.")
    selected_period = _resolve_usage_session_period_or_exit(
        period=period,
        today=today,
        yesterday=yesterday,
        this_week=this_week,
        last_week=last_week,
        this_month=this_month,
        last_month=last_month,
    )
    try:
        resolved_range = resolve_time_range(period=selected_period)
    except ValueError as exc:
        _exit_with_error(str(exc))
    from toktrail.db import summarize_usage_sessions
    from toktrail.reporting import UsageSessionsFilter

    conn = _open_toktrail_connection(ctx)
    try:
        machine_id = _resolve_machine_id_or_exit(conn, machine)
        area = ensure_area(conn, path)
        report = summarize_usage_sessions(
            conn,
            UsageSessionsFilter(
                machine_id=machine_id,
                harness=harness,
                unassigned_area=unassigned,
                since_ms=resolved_range.since_ms,
                until_ms=resolved_range.until_ms,
                limit=None,
                order="desc",
            ),
            costing_config=_load_costing_config_or_exit(ctx),
        )
        candidates = list(report.sessions)
        if dry_run or not apply:
            typer.echo(
                f"Would assign {len(candidates)} source sessions to {area.path}:"
            )
            for session in candidates:
                typer.echo(
                    f"- {session.key}  "
                    f"last={format_epoch_ms_compact(session.last_ms)}  "
                    f"total={_format_int(session.tokens.total)}"
                )
            typer.echo("Use --apply to write changes.")
            return
        assigned = 0
        skipped = 0
        for session in candidates:
            if session.origin_machine_id is None:
                skipped += 1
                continue
            if session.area_id is not None and not overwrite:
                skipped += 1
                continue
            assign_area_to_source_session(
                conn,
                area_id=area.id,
                origin_machine_id=session.origin_machine_id,
                harness=session.harness,
                source_session_id=session.source_session_id,
            )
            assigned += 1
        conn.commit()
    finally:
        conn.close()
    typer.echo(f"Assigned {assigned} sessions to {path}; skipped {skipped}.")


@area_app.command("archive")
def area_archive(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Area path to archive.")],
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        archived = archive_area_path(conn, path)
        conn.commit()
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    typer.echo(f"Archived {archived} area rows under {path}.")


@area_app.command("move")
def area_move(
    ctx: typer.Context,
    old_path: Annotated[str, typer.Argument(help="Current area path.")],
    new_path: Annotated[str, typer.Argument(help="New area path.")],
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        moved_assignments, moved_events = move_area_path(conn, old_path, new_path)
        conn.commit()
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    typer.echo(
        f"Moved {old_path} -> {new_path}; reassigned {moved_assignments} assignments "
        f"and {moved_events} events."
    )


@area_app.command("rename")
def area_rename(
    ctx: typer.Context,
    old_path: Annotated[str, typer.Argument(help="Current area path.")],
    new_name: Annotated[str, typer.Argument(help="New leaf name or replacement path.")],
) -> None:
    normalized_old, _ = normalize_area_path(old_path)
    if "/" in new_name:
        destination = new_name
    else:
        parent = normalized_old.rsplit("/", 1)[0] if "/" in normalized_old else ""
        destination = f"{parent}/{new_name}" if parent else new_name
    area_move(ctx, old_path=normalized_old, new_path=destination)


@area_app.command("merge")
def area_merge(
    ctx: typer.Context,
    target_path: Annotated[str, typer.Argument(help="Target area path.")],
    source_path: Annotated[str, typer.Argument(help="Source area path to merge.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    if dry_run:
        typer.echo(f"Would merge {source_path} into {target_path}.")
        return
    conn = _open_toktrail_connection(ctx)
    try:
        moved_assignments, moved_events = merge_area_paths(
            conn,
            target_path,
            source_path,
        )
        conn.commit()
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()
    typer.echo(
        f"Merged {source_path} into {target_path}; reassigned {moved_assignments} "
        f"assignments and {moved_events} events."
    )


@run_app.command()
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
    maybe_auto_export_to_git_repo(ctx, reason="run start")
    if run is None:
        _exit_with_error(f"Run not found after creation: {session_id}")
    if json_output:
        typer.echo(json.dumps(run.as_dict(), indent=2))
        return
    typer.echo(f"Started run {session_id}: {name or '(unnamed)'}")
    typer.echo(f"Scope: {_format_scope_summary(run.scope)}")


@run_app.command()
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
    maybe_auto_export_to_git_repo(ctx, reason="run stop")
    typer.echo(f"Stopped run {selected_session_id}: {session.name or '(unnamed)'}")
    excluded_total = sum(result.rows_scope_excluded for result in refresh_results)
    if excluded_total > 0:
        typer.echo(f"Linked events excluded by scope: {excluded_total}")


@run_app.command()
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


@run_app.command("archive")
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
    maybe_auto_export_to_git_repo(ctx, reason="run archive")
    if run is None:
        _exit_with_error(f"Run not found: {run_id}")
    if json_output:
        typer.echo(json.dumps(run.as_dict(), indent=2))
        return
    typer.echo(f"Archived run {run.id}: {run.name or '(unnamed)'}")


@run_app.command("unarchive")
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
    maybe_auto_export_to_git_repo(ctx, reason="run unarchive")
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


@subscriptions_app.command("status")
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
    if timezone_name is not None and utc:
        _exit_with_error("Use either --timezone or --utc, not both.")

    normalized_period = period.strip().lower()
    if normalized_period not in {"all", "5h", "daily", "weekly", "monthly", "yearly"}:
        _exit_with_error(
            "--period must be one of: all, 5h, daily, weekly, monthly, yearly."
        )

    refresh_results = _refresh_before_report(
        ctx,
        enabled=refresh,
        details=refresh_details,
        json_output=json_output,
        include_raw_json=raw,
    )
    costing_config = _load_costing_config_or_exit(ctx)
    conn = _open_toktrail_connection(ctx)
    try:
        report = summarize_subscription_usage(
            conn,
            costing_config,
            provider_id=provider_id,
            now_ms=now_ms,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()

    filtered_report = _filter_subscription_usage_report(
        report,
        period=normalized_period,
    )

    if json_output:
        typer.echo(
            json.dumps(
                _wrap_refresh_json_payload(
                    filtered_report.as_dict(),
                    refresh_results=refresh_results,
                    include_refresh=refresh_details,
                ),
                indent=2,
            )
        )
        return

    _print_subscription_usage_report(
        filtered_report,
        provider_filter=provider_id,
        rich_output=rich_output,
        display_timezone_name=timezone_name,
        display_utc=utc,
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
    report, refresh_results, _payload, _elapsed_ms = _build_statusline_cli(
        ctx,
        harness=harness,
        provider_id=provider_id,
        model_id=model_id,
        source_session_id=source_session_id,
        session_mode=session,
        basis=basis,
        refresh=refresh,
        no_refresh=no_refresh,
        refresh_details=refresh_details,
        raw=raw,
        max_width=max_width,
        stale_after=stale_after,
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


@statusline_app.command("test")
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
    report, refresh_results, payload, elapsed_ms = _build_statusline_cli(
        ctx,
        harness=harness,
        provider_id=provider_id,
        model_id=model_id,
        source_session_id=source_session_id,
        session_mode=session,
        basis=basis,
        refresh=refresh,
        no_refresh=no_refresh,
        refresh_details=False,
        raw=raw,
        max_width=max_width,
        stale_after=stale_after,
    )
    typer.echo(f"Source: {report.source_session_id or '(today fallback)'}")
    typer.echo(f"Model: {(report.provider_id or '-')} / {(report.model_id or '-')}")
    typer.echo(f"Timing: {elapsed_ms}ms")
    typer.echo("Output cache: miss")
    typer.echo(
        "Refresh: "
        + ("none" if not refresh_results else f"{len(refresh_results)} source(s)")
    )
    typer.echo(f"Quota: {_render_statusline_quota_label(report)}")
    if payload is not None:
        typer.echo("Payload:")
        typer.echo(json.dumps(payload, indent=2))
    typer.echo("Line:")
    typer.echo(report.line)


@statusline_app.command("install")
def statusline_install(
    target: Annotated[str, typer.Option("--target")] = "starship",
) -> None:
    normalized = target.strip().lower()
    typer.echo(_statusline_install_instructions(normalized))


@statusline_config_app.command("show")
def statusline_config_show(ctx: typer.Context) -> None:
    loaded = _load_resolved_toktrail_config_or_exit(ctx)
    statusline_config = loaded.config.statusline
    typer.echo(f"config path:   {loaded.config_path}")
    typer.echo(f"config exists: {'yes' if loaded.config_exists else 'no'}")
    typer.echo(f"default harness: {statusline_config.default_harness}")
    typer.echo(f"basis:         {statusline_config.basis}")
    typer.echo(f"refresh:       {statusline_config.refresh}")
    typer.echo(f"session:       {statusline_config.session}")
    typer.echo(f"max width:     {statusline_config.max_width}")
    typer.echo(f"stale after:   {statusline_config.cache.stale_after_secs}")
    typer.echo("elements:      " + ", ".join(statusline_config.elements))
    typer.echo(f"context windows: {len(loaded.config.context_windows)}")


@statusline_config_app.command("set")
def statusline_config_set(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument()],
    value: Annotated[str, typer.Argument()],
) -> None:
    loaded = _load_resolved_toktrail_config_or_exit(ctx)
    updated = _statusline_config_with_override(loaded.config.statusline, key, value)
    config_path = loaded.config_path
    existing_text = (
        config_path.read_text(encoding="utf-8")
        if config_path.exists()
        else "config_version = 1\n"
    )
    stripped = _strip_statusline_sections(existing_text).strip()
    rendered = _render_statusline_config_sections(
        updated,
        context_windows=loaded.config.context_windows,
    )
    output = stripped
    if output:
        output += "\n\n"
    output += rendered + "\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(output, encoding="utf-8")
    typer.echo(f"Updated statusline config: {config_path}")


@usage_app.command("statusline")
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


@usage_app.command("daily")
@usage_app.command("weekly")
@usage_app.command("monthly")
@usage_app.command("summary")
@usage_app.command("today")
@usage_app.command("yesterday")
@usage_app.command("this-week")
@usage_app.command("last-week")
@usage_app.command("this-month")
@usage_app.command("last-month")
@usage_app.command("sessions")
@usage_app.command("runs")
@usage_app.command("machines")
@usage_app.command("areas")
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
    if area is not None and unassigned_area:
        _exit_with_error("Use either --area or --unassigned-area, not both.")
    if direct and subtree:
        _exit_with_error("Use either --direct or --subtree, not both.")
    info_name = ctx.info_name
    if info_name is None:
        _exit_with_error("Missing usage subcommand.")
    normalized_view = info_name.strip().lower()
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
            area=area,
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
            area=area,
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
            area=area,
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
            area=area,
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
            area=area,
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
            area=area,
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


def _resolve_usage_session_period_or_exit(
    *,
    period: str | None,
    today: bool,
    yesterday: bool,
    this_week: bool,
    last_week: bool,
    this_month: bool,
    last_month: bool,
) -> str | None:
    requested: list[str] = []
    if period is not None:
        requested.append(period.strip().lower())
    if today:
        requested.append("today")
    if yesterday:
        requested.append("yesterday")
    if this_week:
        requested.append("this-week")
    if last_week:
        requested.append("last-week")
    if this_month:
        requested.append("this-month")
    if last_month:
        requested.append("last-month")

    if len(requested) > 1:
        _exit_with_error(
            "Use only one session period: --period, --today, --yesterday, "
            "--this-week, --last-week, --this-month, or --last-month."
        )
    if not requested:
        return None

    value = requested[0]
    allowed = {
        "today",
        "yesterday",
        "this-week",
        "last-week",
        "this-month",
        "last-month",
    }
    if value not in allowed:
        _exit_with_error(
            "Unsupported session period. Use today, yesterday, this-week, "
            "last-week, this-month, or last-month."
        )
    return value


def _usage_series(
    *,
    ctx: typer.Context,
    view: str,
    json_output: bool,
    harness: str | None,
    source_session_id: str | None,
    machine: str | None,
    provider_id: str | None,
    model_id: str | None,
    thinking_level: str | None,
    agent: str | None,
    area: str | None,
    area_exact: bool,
    unassigned_area: bool,
    since: str | None,
    until: str | None,
    timezone_name: str | None,
    utc: bool,
    split_thinking: bool,
    breakdown: bool,
    compact: bool,
    instances: bool,
    order: str,
    locale: str | None,
    start_of_week: str,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
    sort: str,
    limit: int | None,
    rich_output: bool,
) -> dict[str, object] | None:
    from toktrail.db import summarize_usage_series
    from toktrail.periods import _resolve_timezone, parse_cli_boundary
    from toktrail.reporting import UsageSeriesFilter

    tz = _resolve_timezone(timezone_name=timezone_name, utc=utc)
    since_ms = parse_cli_boundary(since, tz=tz, is_until=False)
    until_ms = parse_cli_boundary(until, tz=tz, is_until=True)

    costing_config = _load_costing_config_or_exit(ctx)
    conn = _open_toktrail_connection(ctx)
    try:
        machine_id = _resolve_machine_id_or_exit(conn, machine)
        series_report = summarize_usage_series(
            conn,
            UsageSeriesFilter(
                granularity=view,
                tracking_session_id=None,
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=area,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=since_ms,
                until_ms=until_ms,
                split_thinking=split_thinking,
                instances=instances,
                breakdown=breakdown,
                start_of_week=start_of_week,
                locale=locale,
                order=order,
                timezone_name=timezone_name,
                utc=utc,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    if json_output:
        return series_report.as_dict()

    _print_usage_series(
        series_report,
        compact=compact,
        breakdown=breakdown,
        instances=instances,
        price_state=price_state,
        min_messages=min_messages,
        min_tokens=min_tokens,
        sort=sort,
        limit=limit,
        rich_output=rich_output,
    )
    return None


def _print_usage_series(
    report: object,
    *,
    compact: bool,
    breakdown: bool,
    instances: bool,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
    sort: str,
    limit: int | None,
    rich_output: bool,
) -> None:
    from toktrail.reporting import UsageSeriesReport

    if not isinstance(report, UsageSeriesReport):
        msg = "Expected UsageSeriesReport."
        raise TypeError(msg)

    typer.echo(f"toktrail usage {report.granularity}")
    if instances:
        for instance in report.instances:
            typer.echo(f"\nInstance: {instance.instance_label}")
            filtered = _filter_series_buckets(
                instance.buckets,
                price_state=price_state,
                min_messages=min_messages,
                min_tokens=min_tokens,
            )
            filtered = _sort_series_buckets(filtered, sort=sort)
            if limit is not None:
                filtered = filtered[:limit]
            _print_usage_series_bucket_table(
                tuple(filtered),
                compact=compact,
                breakdown=breakdown,
                rich_output=rich_output,
            )
        return
    filtered = _filter_series_buckets(
        report.buckets,
        price_state=price_state,
        min_messages=min_messages,
        min_tokens=min_tokens,
    )
    filtered = _sort_series_buckets(filtered, sort=sort)
    if limit is not None:
        filtered = filtered[:limit]
    _print_usage_series_bucket_table(
        tuple(filtered),
        compact=compact,
        breakdown=breakdown,
        rich_output=rich_output,
    )


def _print_usage_series_bucket_table(
    buckets: tuple[UsageSeriesBucket, ...],
    *,
    compact: bool,
    breakdown: bool,
    rich_output: bool,
) -> None:
    if compact:
        rows = [
            {
                "period": bucket.label,
                "msgs": _format_int(bucket.message_count),
                "total": _format_int(bucket.tokens.total),
                "actual": _format_cost(bucket.costs.actual_cost_usd),
                "virtual": _format_cost(bucket.costs.virtual_cost_usd),
                "savings": _format_cost(bucket.costs.savings_usd),
                "models": _format_model_list(bucket.models, rich_output=rich_output),
            }
            for bucket in buckets
        ]
        _print_table(
            rows,
            ["period", "msgs", "total", "actual", "virtual", "savings", "models"],
            {
                "period": "period",
                "msgs": "msgs",
                "total": "total",
                "actual": "actual",
                "virtual": "virtual",
                "savings": "savings",
                "models": "models",
            },
            rich_output=rich_output,
            numeric_columns={"msgs", "total", "actual", "virtual", "savings"},
            wrap_columns={"models"},
            max_widths={"models": 48},
        )
    else:
        rows = [
            {
                "period": bucket.label,
                "msgs": _format_int(bucket.message_count),
                "models": _format_model_list(bucket.models, rich_output=rich_output),
                "input": _format_int(bucket.tokens.input),
                "output": _format_int(bucket.tokens.output),
                "reasoning": _format_int(bucket.tokens.reasoning),
                "cache_r": _format_int(bucket.tokens.cache_read),
                "cache_w": _format_int(bucket.tokens.cache_write),
                "cache_o": _format_int(bucket.tokens.cache_output),
                "total": _format_int(bucket.tokens.total),
                "source": _format_cost(bucket.costs.source_cost_usd),
                "actual": _format_cost(bucket.costs.actual_cost_usd),
                "virtual": _format_cost(bucket.costs.virtual_cost_usd),
                "savings": _format_cost(bucket.costs.savings_usd),
                "unpriced": _format_int(bucket.costs.unpriced_count),
            }
            for bucket in buckets
        ]
        _print_table(
            rows,
            [
                "period",
                "msgs",
                "models",
                "input",
                "output",
                "reasoning",
                "cache_r",
                "cache_w",
                "cache_o",
                "total",
                "source",
                "actual",
                "virtual",
                "savings",
                "unpriced",
            ],
            {
                "period": "period",
                "msgs": "msgs",
                "models": "models",
                "input": "input",
                "output": "output",
                "reasoning": "reasoning",
                "cache_r": "cache_r",
                "cache_w": "cache_w",
                "cache_o": "cache_o",
                "total": "total",
                "source": "source",
                "actual": "actual",
                "virtual": "virtual",
                "savings": "savings",
                "unpriced": "unpriced",
            },
            rich_output=rich_output,
            numeric_columns={
                "msgs",
                "input",
                "output",
                "reasoning",
                "cache_r",
                "cache_w",
                "cache_o",
                "total",
                "source",
                "actual",
                "virtual",
                "savings",
                "unpriced",
            },
            wrap_columns={"models"},
            max_widths={"models": 48},
        )
    if breakdown:
        breakdown_rows = [
            {
                "period": bucket.label,
                "provider_model": f"{row.provider_id}/{row.model_id}",
                "msgs": _format_int(row.message_count),
                "input": _format_int(row.tokens.input),
                "output": _format_int(row.tokens.output),
                "reasoning": _format_int(row.tokens.reasoning),
                "cache_r": _format_int(row.tokens.cache_read),
                "cache_w": _format_int(row.tokens.cache_write),
                "cache_o": _format_int(row.tokens.cache_output),
                "total": _format_int(row.tokens.total),
                "actual": _format_cost(row.costs.actual_cost_usd),
                "virtual": _format_cost(row.costs.virtual_cost_usd),
            }
            for bucket in buckets
            for row in bucket.by_model
        ]
        if breakdown_rows:
            typer.echo("")
            typer.echo("Breakdown by provider/model")
            _print_table(
                breakdown_rows,
                [
                    "period",
                    "provider_model",
                    "msgs",
                    "input",
                    "output",
                    "reasoning",
                    "cache_r",
                    "cache_w",
                    "cache_o",
                    "total",
                    "actual",
                    "virtual",
                ],
                {
                    "period": "period",
                    "provider_model": "provider/model",
                    "msgs": "msgs",
                    "input": "input",
                    "output": "output",
                    "reasoning": "reasoning",
                    "cache_r": "cache_r",
                    "cache_w": "cache_w",
                    "cache_o": "cache_o",
                    "total": "total",
                    "actual": "actual",
                    "virtual": "virtual",
                },
                rich_output=rich_output,
                numeric_columns={
                    "msgs",
                    "input",
                    "output",
                    "reasoning",
                    "cache_r",
                    "cache_w",
                    "cache_o",
                    "total",
                    "actual",
                    "virtual",
                },
            )


def _usage_machines(
    *,
    ctx: typer.Context,
    json_output: bool,
    period: str | None,
    harness: str | None,
    source_session_id: str | None,
    machine: str | None,
    provider_id: str | None,
    model_id: str | None,
    thinking_level: str | None,
    agent: str | None,
    area: str | None,
    area_exact: bool,
    unassigned_area: bool,
    since: str | None,
    until: str | None,
    timezone_name: str | None,
    utc: bool,
    split_thinking: bool,
    rich_output: bool,
) -> dict[str, object] | None:
    try:
        resolved_range = resolve_time_range(
            period=period,
            timezone_name=timezone_name,
            utc=utc,
            since_text=since,
            until_text=until,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))

    costing_config = _load_costing_config_or_exit(ctx)
    conn = _open_toktrail_connection(ctx)
    try:
        machine_id = _resolve_machine_id_or_exit(conn, machine)
        report = summarize_usage(
            conn,
            UsageReportFilter(
                tracking_session_id=None,
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=area,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=resolved_range.since_ms,
                until_ms=resolved_range.until_ms,
                split_thinking=split_thinking,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    if json_output:
        payload = report.as_dict()
        filters = payload.get("filters")
        if isinstance(filters, dict):
            if resolved_range.period is not None:
                filters["period"] = resolved_range.period
            if (
                resolved_range.period is not None
                or timezone_name is not None
                or utc
                or since is not None
                or until is not None
            ):
                filters["timezone"] = resolved_range.timezone
        return payload

    title = "toktrail usage machines"
    if resolved_range.period is not None:
        title = f"{title} ({resolved_range.period})"
    typer.echo(title)
    area_filter_summary = _format_area_filter_summary(report.filters)
    if area_filter_summary is not None:
        typer.echo(area_filter_summary)
    _print_usage_machine_rows(report, rich_output=rich_output)
    return None


def _print_usage_machine_rows(report: InternalRunReport, *, rich_output: bool) -> None:
    typer.echo("")
    if not report.by_machine:
        typer.echo("No usage data.")
        return
    _print_table(
        [
            {
                "machine": row.machine_label,
                "id": row.machine_id[:8] if row.machine_id is not None else "-",
                "msgs": _format_int(row.message_count),
                "input": _format_int(row.tokens.input),
                "output": _format_int(row.tokens.output),
                "reasoning": _format_int(row.tokens.reasoning),
                "cache_r": _format_int(row.tokens.cache_read),
                "cache_w": _format_int(row.tokens.cache_write),
                "total": _format_int(row.tokens.total),
                "actual": _format_cost(row.costs.actual_cost_usd),
                "virtual": _format_cost(row.costs.virtual_cost_usd),
                "savings": _format_cost(row.costs.savings_usd),
                "unpriced": _format_int(row.costs.unpriced_count),
            }
            for row in report.by_machine
        ],
        [
            "machine",
            "id",
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
            "unpriced",
        ],
        {
            "machine": "machine",
            "id": "id",
            "msgs": "msgs",
            "input": "input",
            "output": "output",
            "reasoning": "reasoning",
            "cache_r": "cache_r",
            "cache_w": "cache_w",
            "total": "total",
            "actual": "actual",
            "virtual": "virtual",
            "savings": "savings",
            "unpriced": "unpriced",
        },
        rich_output=rich_output,
        numeric_columns={
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
            "unpriced",
        },
    )


def _usage_sessions(
    *,
    ctx: typer.Context,
    json_output: bool,
    harness: str | None,
    source_session_id: str | None,
    machine: str | None,
    provider_id: str | None,
    model_id: str | None,
    thinking_level: str | None,
    agent: str | None,
    area: str | None,
    area_exact: bool,
    unassigned_area: bool,
    since: str | None,
    until: str | None,
    period: str | None,
    timezone_name: str | None,
    utc: bool,
    split_thinking: bool,
    breakdown: bool,
    compact: bool,
    table: bool,
    order: str,
    limit: int | None,
    last: bool,
    rich_output: bool,
) -> dict[str, object] | None:
    from toktrail.db import summarize_usage_sessions
    from toktrail.reporting import UsageSessionsFilter

    if last and limit is not None and limit != 1:
        _exit_with_error("Use either --last or --limit, not both.")
    if last:
        effective_limit = 1
    elif limit is not None:
        effective_limit = limit
    elif period is not None:
        effective_limit = None
    else:
        effective_limit = 10
    if effective_limit is not None and effective_limit < 0:
        _exit_with_error("--limit must be non-negative.")

    try:
        resolved_range = resolve_time_range(
            period=period,
            timezone_name=timezone_name,
            utc=utc,
            since_text=since,
            until_text=until,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))

    costing_config = _load_costing_config_or_exit(ctx)
    conn = _open_toktrail_connection(ctx)
    try:
        machine_id = _resolve_machine_id_or_exit(conn, machine)
        report = summarize_usage_sessions(
            conn,
            UsageSessionsFilter(
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=area,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=resolved_range.since_ms,
                until_ms=resolved_range.until_ms,
                split_thinking=split_thinking,
                limit=effective_limit,
                order=order,
                breakdown=breakdown,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    if json_output:
        payload = report.as_dict()
        filters = payload.get("filters")
        if not isinstance(filters, dict):
            msg = "Usage sessions payload unexpectedly missing filters."
            raise TypeError(msg)
        if resolved_range.period is not None:
            filters["period"] = resolved_range.period
        if (
            resolved_range.period is not None
            or timezone_name is not None
            or utc
            or since is not None
            or until is not None
        ):
            filters["timezone"] = resolved_range.timezone
        return payload

    _print_usage_sessions(
        report,
        compact=compact,
        breakdown=breakdown,
        utc=utc,
        rich_output=rich_output,
        table=table,
        period=resolved_range.period,
    )
    return None


def _print_usage_sessions(
    report: object,
    *,
    compact: bool,
    breakdown: bool,
    utc: bool,
    rich_output: bool,
    table: bool,
    period: str | None,
) -> None:
    from toktrail.formatting import format_epoch_ms_compact
    from toktrail.reporting import UsageSessionsReport

    if not isinstance(report, UsageSessionsReport):
        msg = "Expected UsageSessionsReport."
        raise TypeError(msg)

    title = "toktrail usage sessions"
    if period is not None:
        title += f" ({period})"
    typer.echo(title)
    area_filter_summary = _format_area_filter_summary(report.filters)
    if area_filter_summary is not None:
        typer.echo(area_filter_summary)

    if not report.sessions:
        typer.echo("No usage data.")
        return

    if not table:
        for idx, session in enumerate(report.sessions):
            if idx:
                typer.echo("")
            session_time = format_epoch_ms_compact(session.last_ms, utc=utc)
            typer.echo(
                f"{session_time}  {session.machine_label}  "
                f"{session.harness}/{session.source_session_id}"
            )
            typer.echo(f"   Area: {session.area_path or 'unassigned'}")
            if session.cwd:
                typer.echo(f"   CWD:  {session.cwd}")
            elif session.source_dir:
                typer.echo(f"   CWD:  {session.source_dir}")
            if session.source_paths:
                first_source = session.source_paths[0]
                extra_count = len(session.source_paths) - 1
                extra_suffix = f" (+{extra_count} more)" if extra_count > 0 else ""
                typer.echo(f"   Source: {first_source}{extra_suffix}")
            model_line = _format_session_model_line(session, rich_output=rich_output)
            typer.echo(f"   {model_line}")
            token_line = _format_token_usage_line(
                session.tokens,
                label="Token usage",
            )
            typer.echo(f"   {token_line}")
            typer.echo(f"   {_format_session_cost_line(session.costs)}")
            if breakdown and session.by_model:
                typer.echo("   Breakdown:")
                for row in session.by_model:
                    typer.echo(
                        "     "
                        f"{row.provider_id}/{row.model_id} "
                        f"msgs={_format_int(row.message_count)} "
                        f"total={_format_int(row.tokens.total)} "
                        f"input={_format_int(row.tokens.input)} "
                        f"output={_format_int(row.tokens.output)} "
                        f"reasoning={_format_int(row.tokens.reasoning)} "
                        f"cache_read={_format_int(row.tokens.cache_read)} "
                        f"actual={_format_cost(row.costs.actual_cost_usd)} "
                        f"virtual={_format_cost(row.costs.virtual_cost_usd)}"
                    )
        return

    if compact:
        rows = [
            {
                "machine": session.machine_label,
                "session": session.key,
                "area": session.area_path or "unassigned",
                "last": format_epoch_ms_compact(session.last_ms, utc=utc),
                "msgs": _format_int(session.message_count),
                "total": _format_int(session.tokens.total),
                "actual": _format_cost(session.costs.actual_cost_usd),
                "virtual": _format_cost(session.costs.virtual_cost_usd),
                "savings": _format_cost(session.costs.savings_usd),
                "models": _format_model_list(session.models, rich_output=rich_output),
            }
            for session in report.sessions
        ]
        _print_table(
            rows,
            [
                "machine",
                "session",
                "area",
                "last",
                "msgs",
                "total",
                "actual",
                "virtual",
                "savings",
                "models",
            ],
            {
                "machine": "machine",
                "session": "session",
                "area": "area",
                "last": "last",
                "msgs": "msgs",
                "total": "total",
                "actual": "actual",
                "virtual": "virtual",
                "savings": "savings",
                "models": "models",
            },
            rich_output=rich_output,
            numeric_columns={"msgs", "total", "actual", "virtual", "savings"},
            wrap_columns={"area", "models"},
            max_widths={"area": 36, "models": 48, "session": 48},
        )
    else:
        rows = [
            {
                "machine": session.machine_label,
                "session": session.key,
                "area": session.area_path or "unassigned",
                "last": format_epoch_ms_compact(session.last_ms, utc=utc),
                "msgs": _format_int(session.message_count),
                "models": _format_model_list(session.models, rich_output=rich_output),
                "input": _format_int(session.tokens.input),
                "output": _format_int(session.tokens.output),
                "reasoning": _format_int(session.tokens.reasoning),
                "cache_r": _format_int(session.tokens.cache_read),
                "cache_w": _format_int(session.tokens.cache_write),
                "cache_o": _format_int(session.tokens.cache_output),
                "total": _format_int(session.tokens.total),
                "source": _format_cost(session.costs.source_cost_usd),
                "actual": _format_cost(session.costs.actual_cost_usd),
                "virtual": _format_cost(session.costs.virtual_cost_usd),
                "savings": _format_cost(session.costs.savings_usd),
                "unpriced": _format_int(session.costs.unpriced_count),
            }
            for session in report.sessions
        ]
        _print_table(
            rows,
            [
                "machine",
                "session",
                "area",
                "last",
                "msgs",
                "models",
                "input",
                "output",
                "reasoning",
                "cache_r",
                "cache_w",
                "cache_o",
                "total",
                "source",
                "actual",
                "virtual",
                "savings",
                "unpriced",
            ],
            {
                "machine": "machine",
                "session": "session",
                "area": "area",
                "last": "last",
                "msgs": "msgs",
                "models": "models",
                "input": "input",
                "output": "output",
                "reasoning": "reasoning",
                "cache_r": "cache_r",
                "cache_w": "cache_w",
                "cache_o": "cache_o",
                "total": "total",
                "source": "source",
                "actual": "actual",
                "virtual": "virtual",
                "savings": "savings",
                "unpriced": "unpriced",
            },
            rich_output=rich_output,
            numeric_columns={
                "msgs",
                "input",
                "output",
                "reasoning",
                "cache_r",
                "cache_w",
                "cache_o",
                "total",
                "source",
                "actual",
                "virtual",
                "savings",
                "unpriced",
            },
            wrap_columns={"session", "area", "models"},
            max_widths={"session": 48, "area": 36, "models": 48},
        )

    if breakdown:
        breakdown_rows = [
            {
                "session": session.key,
                "provider_model": f"{row.provider_id}/{row.model_id}",
                "msgs": _format_int(row.message_count),
                "input": _format_int(row.tokens.input),
                "output": _format_int(row.tokens.output),
                "reasoning": _format_int(row.tokens.reasoning),
                "cache_r": _format_int(row.tokens.cache_read),
                "cache_w": _format_int(row.tokens.cache_write),
                "cache_o": _format_int(row.tokens.cache_output),
                "total": _format_int(row.tokens.total),
                "actual": _format_cost(row.costs.actual_cost_usd),
                "virtual": _format_cost(row.costs.virtual_cost_usd),
            }
            for session in report.sessions
            for row in session.by_model
        ]
        if breakdown_rows:
            typer.echo("")
            typer.echo("Breakdown by provider/model")
            _print_table(
                breakdown_rows,
                [
                    "session",
                    "provider_model",
                    "msgs",
                    "input",
                    "output",
                    "reasoning",
                    "cache_r",
                    "cache_w",
                    "cache_o",
                    "total",
                    "actual",
                    "virtual",
                ],
                {
                    "session": "session",
                    "provider_model": "provider/model",
                    "msgs": "msgs",
                    "input": "input",
                    "output": "output",
                    "reasoning": "reasoning",
                    "cache_r": "cache_r",
                    "cache_w": "cache_w",
                    "cache_o": "cache_o",
                    "total": "total",
                    "actual": "actual",
                    "virtual": "virtual",
                },
                rich_output=rich_output,
                numeric_columns={
                    "msgs",
                    "input",
                    "output",
                    "reasoning",
                    "cache_r",
                    "cache_w",
                    "cache_o",
                    "total",
                    "actual",
                    "virtual",
                },
            )


def _usage_runs(
    *,
    ctx: typer.Context,
    json_output: bool,
    machine: str | None,
    provider_id: str | None,
    model_id: str | None,
    thinking_level: str | None,
    agent: str | None,
    area: str | None,
    area_exact: bool,
    unassigned_area: bool,
    since: str | None,
    until: str | None,
    timezone_name: str | None,
    utc: bool,
    split_thinking: bool,
    order: str,
    limit: int | None,
    last: bool,
    include_archived: bool,
    archived_only: bool,
    rich_output: bool,
) -> dict[str, object] | None:
    from toktrail.db import summarize_usage_runs
    from toktrail.periods import _resolve_timezone, parse_cli_boundary
    from toktrail.reporting import UsageRunsFilter

    tz = _resolve_timezone(timezone_name=timezone_name, utc=utc)
    since_ms = parse_cli_boundary(since, tz=tz, is_until=False)
    until_ms = parse_cli_boundary(until, tz=tz, is_until=True)

    costing_config = _load_costing_config_or_exit(ctx)
    conn = _open_toktrail_connection(ctx)
    try:
        machine_id = _resolve_machine_id_or_exit(conn, machine)
        runs_report = summarize_usage_runs(
            conn,
            UsageRunsFilter(
                machine_id=machine_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=area,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=since_ms,
                until_ms=until_ms,
                split_thinking=split_thinking,
                order=order,
                limit=limit,
                last=last,
                include_archived=include_archived,
                archived_only=archived_only,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    if json_output:
        return runs_report.as_dict()

    _print_usage_runs(runs_report, utc=utc, rich_output=rich_output)
    return None


def _print_usage_runs(
    report: object,
    *,
    utc: bool,
    rich_output: bool,
) -> None:
    from toktrail.formatting import format_epoch_ms_compact
    from toktrail.reporting import UsageRunsReport

    if not isinstance(report, UsageRunsReport):
        msg = "Expected UsageRunsReport."
        raise TypeError(msg)

    typer.echo("toktrail usage runs")
    area_filter_summary = _format_area_filter_summary(report.filters)
    if area_filter_summary is not None:
        typer.echo(area_filter_summary)

    if not report.runs:
        typer.echo("No usage data.")
        return

    rows = [
        {
            "run": _format_int(run.run_id),
            "machine": run.machine_label,
            "name": run.name or "-",
            "started": format_epoch_ms_compact(run.started_at_ms, utc=utc),
            "ended": format_epoch_ms_compact(run.ended_at_ms, utc=utc)
            if run.ended_at_ms is not None
            else "-",
            "msgs": _format_int(run.message_count),
            "models": _format_model_list(run.models, rich_output=rich_output),
            "input": _format_int(run.tokens.input),
            "output": _format_int(run.tokens.output),
            "reasoning": _format_int(run.tokens.reasoning),
            "cache_r": _format_int(run.tokens.cache_read),
            "cache_w": _format_int(run.tokens.cache_write),
            "cache_o": _format_int(run.tokens.cache_output),
            "total": _format_int(run.tokens.total),
            "source": _format_cost(run.costs.source_cost_usd),
            "actual": _format_cost(run.costs.actual_cost_usd),
            "virtual": _format_cost(run.costs.virtual_cost_usd),
            "savings": _format_cost(run.costs.savings_usd),
            "unpriced": _format_int(run.costs.unpriced_count),
        }
        for run in report.runs
    ]
    _print_table(
        rows,
        [
            "run",
            "machine",
            "name",
            "started",
            "ended",
            "msgs",
            "models",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "cache_o",
            "total",
            "source",
            "actual",
            "virtual",
            "savings",
            "unpriced",
        ],
        {
            "run": "run",
            "machine": "machine",
            "name": "name",
            "started": "started",
            "ended": "ended",
            "msgs": "msgs",
            "models": "models",
            "input": "input",
            "output": "output",
            "reasoning": "reasoning",
            "cache_r": "cache_r",
            "cache_w": "cache_w",
            "cache_o": "cache_o",
            "total": "total",
            "source": "source",
            "actual": "actual",
            "virtual": "virtual",
            "savings": "savings",
            "unpriced": "unpriced",
        },
        rich_output=rich_output,
        numeric_columns={
            "run",
            "msgs",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "cache_o",
            "total",
            "source",
            "actual",
            "virtual",
            "savings",
            "unpriced",
        },
        wrap_columns={"name", "models"},
        max_widths={"name": 24, "models": 48},
    )


def _usage_areas(
    *,
    ctx: typer.Context,
    json_output: bool,
    period: str | None,
    harness: str | None,
    source_session_id: str | None,
    machine: str | None,
    provider_id: str | None,
    model_id: str | None,
    thinking_level: str | None,
    agent: str | None,
    area: str | None,
    area_exact: bool,
    unassigned_area: bool,
    since: str | None,
    until: str | None,
    timezone_name: str | None,
    utc: bool,
    split_thinking: bool,
    rich_output: bool,
    direct: bool,
    subtree: bool,
    leaves: bool,
    percent: bool,
    share_by: str,
) -> dict[str, object] | None:
    try:
        resolved_range = resolve_time_range(
            period=period,
            timezone_name=timezone_name,
            utc=utc,
            since_text=since,
            until_text=until,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))

    costing_config = _load_costing_config_or_exit(ctx)
    conn = _open_toktrail_connection(ctx)
    try:
        machine_id = _resolve_machine_id_or_exit(conn, machine)
        report = summarize_usage_areas(
            conn,
            UsageReportFilter(
                tracking_session_id=None,
                machine_id=machine_id,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                area=area,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=resolved_range.since_ms,
                until_ms=resolved_range.until_ms,
                split_thinking=split_thinking,
            ),
            costing_config=costing_config,
        )
    finally:
        conn.close()

    if json_output:
        payload = report.as_dict()
        filters = payload.get("filters")
        if isinstance(filters, dict):
            if resolved_range.period is not None:
                filters["period"] = resolved_range.period
            if (
                resolved_range.period is not None
                or timezone_name is not None
                or utc
                or since is not None
                or until is not None
            ):
                filters["timezone"] = resolved_range.timezone
        return payload

    _print_usage_areas(
        report,
        period=resolved_range.period,
        rich_output=rich_output,
        direct=direct,
        subtree=subtree,
        leaves=leaves,
        percent=percent,
        share_by=share_by,
        unassigned_warning_threshold=_load_resolved_toktrail_config_or_exit(
            ctx
        ).config.areas.unassigned_warning_threshold,
    )
    return None


def _print_usage_areas(  # noqa: C901
    report: object,
    *,
    period: str | None,
    rich_output: bool,
    direct: bool,
    subtree: bool,
    leaves: bool,
    percent: bool,
    share_by: str,
    unassigned_warning_threshold: float,
) -> None:
    from toktrail.reporting import UsageAreasReport

    if not isinstance(report, UsageAreasReport):
        msg = "Expected UsageAreasReport."
        raise TypeError(msg)

    title = "toktrail usage areas"
    if period is not None:
        title += f" ({period})"
    typer.echo(title)
    area_filter_summary = _format_area_filter_summary(report.filters)
    if area_filter_summary is not None:
        typer.echo(area_filter_summary)

    if not report.areas:
        typer.echo("No usage data.")
        return

    if share_by not in {"tokens", "actual", "virtual", "messages"}:
        _exit_with_error(
            "--share-by must be one of: tokens, actual, virtual, messages."
        )

    def _direct_msg(area_row) -> int:
        return area_row.direct_message_count or 0

    def _tree_msg(area_row) -> int:
        return area_row.subtree_message_count or area_row.message_count

    def _direct_tokens(area_row) -> TokenBreakdown:
        return area_row.direct_tokens or TokenBreakdown()

    def _tree_tokens(area_row) -> TokenBreakdown:
        return area_row.subtree_tokens or area_row.tokens

    def _direct_costs(area_row) -> CostTotals:
        return area_row.direct_costs or CostTotals()

    def _tree_costs(area_row) -> CostTotals:
        return area_row.subtree_costs or area_row.costs

    if leaves:
        filtered_areas = [
            row for row in report.areas if row.path is None or _direct_msg(row) > 0
        ]
    else:
        filtered_areas = list(report.areas)

    def _share_value(area_row) -> float:
        if share_by == "messages":
            return float(_tree_msg(area_row))
        if share_by == "actual":
            return float(_tree_costs(area_row).actual_cost_usd)
        if share_by == "virtual":
            return float(_tree_costs(area_row).virtual_cost_usd)
        return float(_tree_tokens(area_row).total)

    share_total = sum(_share_value(row) for row in filtered_areas)
    rows = []
    for area_row in filtered_areas:
        direct_tokens = _direct_tokens(area_row)
        tree_tokens = _tree_tokens(area_row)
        direct_costs = _direct_costs(area_row)
        tree_costs = _tree_costs(area_row)
        row = {
            "area": (
                "  " * area_row.depth + area_row.path
                if area_row.path is not None
                else "unassigned"
            ),
            "msgs_self": _format_int(_direct_msg(area_row)),
            "msgs_tree": _format_int(_tree_msg(area_row)),
            "total_self": _format_int(direct_tokens.total),
            "total_tree": _format_int(tree_tokens.total),
            "actual_tree": _format_cost(tree_costs.actual_cost_usd),
            "virtual_tree": _format_cost(tree_costs.virtual_cost_usd),
            "share": (
                f"{(_share_value(area_row) / share_total * 100):.1f}%"
                if share_total > 0
                else "0.0%"
            ),
            "input": _format_int(tree_tokens.input),
            "output": _format_int(tree_tokens.output),
            "reasoning": _format_int(tree_tokens.reasoning),
            "cache_r": _format_int(tree_tokens.cache_read),
            "cache_w": _format_int(tree_tokens.cache_write),
            "cache_o": _format_int(tree_tokens.cache_output),
            "actual": _format_cost(tree_costs.actual_cost_usd),
            "virtual": _format_cost(tree_costs.virtual_cost_usd),
            "savings": _format_cost(tree_costs.savings_usd),
            "unpriced": _format_int(tree_costs.unpriced_count),
            "direct_actual": _format_cost(direct_costs.actual_cost_usd),
        }
        rows.append(row)

    if direct:
        columns = ["area", "msgs_self", "total_self", "direct_actual"]
        labels = {
            "area": "area",
            "msgs_self": "msgs",
            "total_self": "total",
            "direct_actual": "actual",
        }
    elif subtree:
        columns = [
            "area",
            "msgs_tree",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "cache_o",
            "total_tree",
            "actual",
            "virtual",
            "savings",
            "unpriced",
        ]
        labels = {
            "area": "area",
            "msgs_tree": "msgs",
            "input": "input",
            "output": "output",
            "reasoning": "reasoning",
            "cache_r": "cache_r",
            "cache_w": "cache_w",
            "cache_o": "cache_o",
            "total_tree": "total",
            "actual": "actual",
            "virtual": "virtual",
            "savings": "savings",
            "unpriced": "unpriced",
        }
    else:
        columns = [
            "area",
            "msgs_self",
            "msgs_tree",
            "total_self",
            "total_tree",
            "actual_tree",
            "virtual_tree",
        ]
        labels = {
            "area": "area",
            "msgs_self": "msgs self",
            "msgs_tree": "msgs tree",
            "total_self": "total self",
            "total_tree": "total tree",
            "actual_tree": "actual tree",
            "virtual_tree": "virtual tree",
        }
    if percent and "share" not in columns:
        columns.append("share")
        labels["share"] = "share"
    _print_table(
        rows,
        columns,
        labels,
        rich_output=rich_output,
        numeric_columns={
            "msgs",
            "msgs_self",
            "msgs_tree",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "cache_o",
            "total",
            "total_self",
            "total_tree",
            "actual",
            "actual_tree",
            "direct_actual",
            "virtual",
            "virtual_tree",
            "savings",
            "unpriced",
        },
        wrap_columns={"area"},
        max_widths={"area": 48},
    )
    _print_unassigned_area_warning(
        report=report,
        threshold=unassigned_warning_threshold,
    )


def _format_area_filter_summary(filters: object) -> str | None:
    def _value(key: str) -> object:
        if isinstance(filters, dict):
            return filters.get(key)
        return getattr(filters, key, None)

    if bool(_value("unassigned_area")):
        return "Area filter: unassigned only"
    area_value = _value("area")
    if not isinstance(area_value, str) or not area_value:
        return None
    if bool(_value("area_exact")):
        return f"Area filter: {area_value} (exact only)"
    return f"Area filter: {area_value} (including descendants)"


def _print_unassigned_area_warning(
    *,
    report: object,
    threshold: float,
) -> None:
    from toktrail.reporting import UsageAreasReport

    if not isinstance(report, UsageAreasReport):
        return
    if threshold <= 0:
        return
    unassigned_total = 0
    for row in report.areas:
        if row.path is None:
            unassigned_total = (row.subtree_tokens or row.tokens).total
            break
    total = report.totals.tokens.total
    if total <= 0:
        return
    ratio = unassigned_total / total
    if ratio < threshold:
        return
    percent = int(round(ratio * 100))
    typer.echo(
        "Warning: "
        f"{percent}% of this report's usage is unassigned. Run "
        "`toktrail area sessions --unassigned --today` to classify it."
    )


def _usage_aggregate(
    *,
    ctx: typer.Context,
    period: str | None,
    json_output: bool,
    harness: str | None,
    source_session_id: str | None,
    machine: str | None,
    provider_id: str | None,
    model_id: str | None,
    thinking_level: str | None,
    agent: str | None,
    area: str | None,
    area_exact: bool,
    unassigned_area: bool,
    since: str | None,
    until: str | None,
    timezone_name: str | None,
    utc: bool,
    rich_output: bool,
    split_thinking: bool,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
    sort: str,
    limit: int | None,
) -> dict[str, object] | None:
    try:
        resolved_range = resolve_time_range(
            period=period,
            timezone_name=timezone_name,
            utc=utc,
            since_text=since,
            until_text=until,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))

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
    conn = _open_toktrail_connection(ctx)
    unassigned_total = 0
    try:
        machine_id = _resolve_machine_id_or_exit(conn, machine)
        base_filter = UsageReportFilter(
            tracking_session_id=None,
            machine_id=machine_id,
            harness=harness,
            source_session_id=source_session_id,
            provider_id=provider_id,
            model_id=model_id,
            thinking_level=thinking_level,
            agent=agent,
            area=area,
            area_exact=area_exact,
            unassigned_area=unassigned_area,
            since_ms=resolved_range.since_ms,
            until_ms=resolved_range.until_ms,
            split_thinking=split_thinking,
        )
        report = summarize_usage(
            conn,
            base_filter,
            costing_config=costing_config,
        )
        if area is None and not unassigned_area:
            unassigned_report = summarize_usage(
                conn,
                replace(base_filter, unassigned_area=True),
                costing_config=costing_config,
            )
            unassigned_total = unassigned_report.totals.tokens.total
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
        filters = payload.get("filters")
        if not isinstance(filters, dict):
            msg = "Usage report payload unexpectedly missing filters."
            raise TypeError(msg)
        if resolved_range.period is not None:
            filters["period"] = resolved_range.period
        if (
            resolved_range.period is not None
            or timezone_name is not None
            or utc
            or since is not None
            or until is not None
        ):
            filters["timezone"] = resolved_range.timezone
        return payload

    title = "toktrail usage"
    if resolved_range.period is not None:
        title = f"{title} ({resolved_range.period})"
    typer.echo(title)
    area_filter_summary = _format_area_filter_summary(report.filters)
    if area_filter_summary is not None:
        typer.echo(area_filter_summary)
    if (
        resolved_range.period == "today"
        and report.totals.tokens.total > 0
        and area is None
        and not unassigned_area
    ):
        threshold = _load_resolved_toktrail_config_or_exit(
            ctx
        ).config.areas.unassigned_warning_threshold
        ratio = unassigned_total / report.totals.tokens.total
        if threshold > 0 and ratio >= threshold:
            typer.echo(
                "Warning: "
                f"{int(round(ratio * 100))}% of today's usage is unassigned. "
                "Run `toktrail area sessions --unassigned --today` to classify it."
            )
    _print_usage_summary(
        report,
        rich_output=rich_output,
        by_model=filtered_by_model,
        unconfigured_models=filtered_unconfigured,
        missing_price_mode=costing_config.missing_price,
    )
    return None


def _format_session_model_line(session: object, *, rich_output: bool) -> str:
    from toktrail.reporting import UsageSessionRow

    if not isinstance(session, UsageSessionRow):
        msg = "Expected UsageSessionRow."
        raise TypeError(msg)
    label = "Model" if len(session.models) == 1 else "Models"
    models = _format_model_list(session.models, rich_output=rich_output)
    return f"{label}: {models} with {_format_int(session.message_count)} msgs"


def _format_session_cost_line(costs: CostTotals) -> str:
    return (
        "Costs: "
        f"source={_format_cost(costs.source_cost_usd)} "
        f"actual={_format_cost(costs.actual_cost_usd)} "
        f"virtual={_format_cost(costs.virtual_cost_usd)} "
        f"savings={_format_cost(costs.savings_usd)} "
        f"unpriced={_format_int(costs.unpriced_count)}"
    )


def _format_token_usage_line(
    tokens: TokenBreakdown, *, label: str = "token usage"
) -> str:
    input_suffixes: list[str] = []
    if tokens.cache_read:
        input_suffixes.append(f"+{_format_int(tokens.cache_read)} cached")
    if tokens.cache_write:
        input_suffixes.append(f"+{_format_int(tokens.cache_write)} cache write")

    output_suffixes: list[str] = []
    if tokens.cache_output:
        output_suffixes.append(f"+{_format_int(tokens.cache_output)} cached output")

    input_part = f"input={_format_int(tokens.input)}"
    if input_suffixes:
        input_part += f" ({', '.join(input_suffixes)})"

    output_part = f"output={_format_int(tokens.output)}"
    if output_suffixes:
        output_part += f" ({', '.join(output_suffixes)})"

    reasoning_part = (
        f" (reasoning {_format_int(tokens.reasoning)})" if tokens.reasoning else ""
    )
    return (
        f"{label}: total={_format_int(tokens.total)}"
        f" {input_part} {output_part}{reasoning_part}"
    )


def _format_model_list(models: tuple[str, ...], *, rich_output: bool) -> str:
    if not models:
        return "-"
    if rich_output or len(models) <= 3:
        return ", ".join(models)
    shown = ", ".join(models[:2])
    return f"{len(models)} models ({shown}, ...)"


def _print_usage_summary(
    report: InternalRunReport,
    *,
    rich_output: bool,
    by_model: list[ModelSummaryRow] | None = None,
    unconfigured_models: list[UnconfiguredModelRow] | None = None,
    missing_price_mode: str = "warn",
) -> None:
    typer.echo("")
    typer.echo("Totals")
    totals = report.totals
    typer.echo(f"  {_format_token_usage_line(totals.tokens)}")
    typer.echo("")
    typer.echo("Costs")
    typer.echo(f"  source:   {_format_cost(totals.source_cost_usd)}")
    typer.echo(f"  actual:   {_format_cost(totals.actual_cost_usd)}")
    typer.echo(f"  virtual:  {_format_cost(totals.virtual_cost_usd)}")
    typer.echo(f"  savings:  {_format_cost(totals.savings_usd)}")
    typer.echo(f"  unpriced: {totals.unpriced_count} model groups")

    unconfigured = (
        report.unconfigured_models
        if unconfigured_models is None
        else unconfigured_models
    )
    if unconfigured:
        typer.echo("")
        typer.echo(
            "Unconfigured models (warning)"
            if missing_price_mode == "warn"
            else "Unconfigured models"
        )
        _print_unconfigured_model_table(unconfigured, rich_output=rich_output)

    typer.echo("")
    typer.echo("By provider")
    by_provider: list[ProviderSummaryRow] = report.by_provider
    if by_provider:
        _print_table(
            [
                {
                    "provider": provider_row.provider_id,
                    "tokens": _format_int(provider_row.total_tokens),
                    "cached_input": _format_int(provider_row.tokens.cache_read)
                    if provider_row.tokens.cache_read
                    else "",
                    "source": _format_cost(provider_row.source_cost_usd),
                    "actual": _format_cost(provider_row.actual_cost_usd),
                    "virtual": _format_cost(provider_row.virtual_cost_usd),
                    "savings": _format_cost(provider_row.savings_usd),
                }
                for provider_row in by_provider
            ],
            [
                "provider",
                "tokens",
                "cached_input",
                "source",
                "actual",
                "virtual",
                "savings",
            ],
            {
                "provider": "provider",
                "tokens": "tokens",
                "cached_input": "cached_input",
                "source": "source",
                "actual": "actual",
                "virtual": "virtual",
                "savings": "savings",
            },
            rich_output=rich_output,
            numeric_columns={
                "tokens",
                "cached_input",
                "source",
                "actual",
                "virtual",
                "savings",
            },
        )
    else:
        typer.echo("  (none)")

    typer.echo("")
    typer.echo("By harness")
    by_harness = report.by_harness
    if by_harness:
        _print_table(
            [
                {
                    "harness": harness_row.harness,
                    "tokens": _format_int(harness_row.total_tokens),
                    "cached_input": _format_int(harness_row.tokens.cache_read)
                    if harness_row.tokens.cache_read
                    else "",
                    "actual": _format_cost(harness_row.actual_cost_usd),
                    "virtual": _format_cost(harness_row.virtual_cost_usd),
                    "savings": _format_cost(harness_row.savings_usd),
                }
                for harness_row in by_harness
            ],
            ["harness", "tokens", "cached_input", "actual", "virtual", "savings"],
            {
                "harness": "harness",
                "tokens": "tokens",
                "cached_input": "cached_input",
                "actual": "actual",
                "virtual": "virtual",
                "savings": "savings",
            },
            rich_output=rich_output,
            numeric_columns={"tokens", "cached_input", "actual", "virtual", "savings"},
        )
    else:
        typer.echo("  (none)")

    typer.echo("")
    typer.echo("By machine")
    by_machine = report.by_machine
    if by_machine:
        _print_table(
            [
                {
                    "machine": machine_row.machine_label,
                    "id": machine_row.machine_id[:8]
                    if machine_row.machine_id is not None
                    else "-",
                    "tokens": _format_int(machine_row.total_tokens),
                    "cached_input": _format_int(machine_row.tokens.cache_read)
                    if machine_row.tokens.cache_read
                    else "",
                    "actual": _format_cost(machine_row.costs.actual_cost_usd),
                    "virtual": _format_cost(machine_row.costs.virtual_cost_usd),
                    "savings": _format_cost(machine_row.costs.savings_usd),
                }
                for machine_row in by_machine
            ],
            ["machine", "id", "tokens", "cached_input", "actual", "virtual", "savings"],
            {
                "machine": "machine",
                "id": "id",
                "tokens": "tokens",
                "cached_input": "cached_input",
                "actual": "actual",
                "virtual": "virtual",
                "savings": "savings",
            },
            rich_output=rich_output,
            numeric_columns={"tokens", "cached_input", "actual", "virtual", "savings"},
        )
    else:
        typer.echo("  (none)")

    typer.echo("")
    typer.echo("By model")
    model_rows = report.by_model if by_model is None else by_model
    if model_rows:
        _print_model_table(model_rows, rich_output=rich_output)
    else:
        typer.echo("  (none)")

    typer.echo("")
    typer.echo("By activity")
    by_activity = report.by_activity
    if by_activity:
        _print_table(
            [
                {
                    "activity": agent_row.agent or "-",
                    "tokens": _format_int(agent_row.total_tokens),
                    "cached_input": _format_int(agent_row.tokens.cache_read)
                    if agent_row.tokens.cache_read
                    else "",
                    "actual": _format_cost(agent_row.actual_cost_usd),
                    "virtual": _format_cost(agent_row.virtual_cost_usd),
                    "savings": _format_cost(agent_row.savings_usd),
                }
                for agent_row in by_activity
            ],
            ["activity", "tokens", "cached_input", "actual", "virtual", "savings"],
            {
                "activity": "activity",
                "tokens": "tokens",
                "cached_input": "cached_input",
                "actual": "actual",
                "virtual": "virtual",
                "savings": "savings",
            },
            rich_output=rich_output,
            numeric_columns={"tokens", "cached_input", "actual", "virtual", "savings"},
        )
    else:
        typer.echo("  (none)")


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
) -> None:
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
                    "status": period.status,
                    "window": _format_subscription_window(
                        period.since_ms,
                        period.until_ms,
                        timezone_name=display_timezone_name,
                        utc=display_utc,
                        status=period.status,
                        last_since_ms=period.last_since_ms,
                        last_until_ms=period.last_until_ms,
                    ),
                    "resets": _format_subscription_resets(
                        period,
                        timezone_name=display_timezone_name,
                        utc=display_utc,
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
                "resets",
                "window",
                "limit",
                "used",
                "left",
                "used_pct",
            ],
            {
                "period": "period",
                "status": "status",
                "resets": f"resets ({display_tz_label})",
                "window": f"window ({display_tz_label})",
                "limit": "limit",
                "used": "used",
                "left": "left",
                "used_pct": "used%",
            },
            rich_output=rich_output,
            numeric_columns={"limit", "used", "left", "used_pct"},
            wrap_columns={"resets", "window"},
            max_widths={"resets": 24, "window": 40},
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
    from toktrail.periods import resolve_timezone

    tz = resolve_timezone(timezone_name=timezone_name, utc=utc)

    if since_ms is None or until_ms is None:
        if status == "waiting_for_first_use":
            return "starts on first use"
        if status == "expired_waiting_for_next_use":
            if last_since_ms is not None and last_until_ms is not None:
                last_since_dt = datetime.datetime.fromtimestamp(
                    last_since_ms / 1000,
                    tz=tz,
                )
                last_until_dt = datetime.datetime.fromtimestamp(
                    last_until_ms / 1000,
                    tz=tz,
                )
                return (
                    "expired; last "
                    f"{_format_subscription_dt(last_since_dt, force_time=True)}"
                    f"..{_format_subscription_dt(last_until_dt, force_time=True)}; "
                    "next starts on first use"
                )
            return "expired; next starts on first use"
        return "(none)"

    since_dt = datetime.datetime.fromtimestamp(since_ms / 1000, tz=tz)
    until_dt = datetime.datetime.fromtimestamp(until_ms / 1000, tz=tz)

    duration_ms = until_ms - since_ms
    force_time = duration_ms < 24 * 60 * 60 * 1000 or not (
        since_dt.hour == since_dt.minute == since_dt.second == since_dt.microsecond == 0
        and until_dt.hour
        == until_dt.minute
        == until_dt.second
        == until_dt.microsecond
        == 0
    )
    return (
        f"{_format_subscription_dt(since_dt, force_time=force_time)}"
        f"..{_format_subscription_dt(until_dt, force_time=force_time)}"
    )


def _format_subscription_dt(value: datetime.datetime, *, force_time: bool) -> str:
    if force_time:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.date().isoformat()


def _display_timezone_label(*, timezone_name: str | None, utc: bool) -> str:
    from toktrail.periods import resolve_timezone

    tz = resolve_timezone(timezone_name=timezone_name, utc=utc)
    if tz is datetime.timezone.utc:
        return "UTC"
    return getattr(tz, "key", str(tz))


def _format_subscription_resets(
    period: SubscriptionUsagePeriod,
    *,
    timezone_name: str | None,
    utc: bool,
) -> str:
    from toktrail.periods import resolve_timezone

    if period.until_ms is not None:
        tz = resolve_timezone(timezone_name=timezone_name, utc=utc)
        dt = datetime.datetime.fromtimestamp(period.until_ms / 1000, tz=tz)
        return dt.strftime("%Y-%m-%d %H:%M")
    if period.status == "waiting_for_first_use":
        return "on first use"
    if period.status == "expired_waiting_for_next_use":
        return "on next use"
    return "-"


@config_app.command("path")
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


@config_app.command("init")
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


@config_app.command("validate")
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


@config_app.command("show")
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


@sources_app.command("list")
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
    loaded = _load_resolved_toktrail_config_or_exit(ctx)
    selected_harnesses = tuple(harnesses or loaded.config.imports.harnesses)
    configured_sources = loaded.config.imports.sources or {}
    if source_path is not None and len(selected_harnesses) != 1:
        _exit_with_error("--source can only be used with exactly one --harness.")

    rows: list[dict[str, object]] = []
    for harness in sorted(selected_harnesses):
        try:
            configured_source = configured_sources.get(harness)
            selected_source = (
                source_path if source_path is not None else configured_source
            )
            if isinstance(selected_source, list):
                selected_source = selected_source[0] if selected_source else None
            snapshot = capture_source_snapshot(
                harness,
                source_path=selected_source,
                config_path=loaded.config_path,
            )
        except (OSError, ValueError, ToktrailError) as exc:
            rows.append(
                {
                    "harness": harness,
                    "source_path": str(source_path or ""),
                    "exists": False,
                    "sessions": 0,
                    "messages": 0,
                    "tokens": 0,
                    "warning": str(exc),
                }
            )
            continue
        resolved = snapshot.source_path
        exists = bool(resolved is not None and resolved.exists())
        rows.append(
            {
                "harness": harness,
                "source_path": str(resolved) if resolved is not None else "",
                "exists": exists,
                "sessions": len(snapshot.sessions),
                "messages": sum(
                    summary.assistant_message_count for summary in snapshot.sessions
                ),
                "tokens": sum(summary.tokens.total for summary in snapshot.sessions),
                "warning": "" if exists else "source not found",
                "config_key": get_harness(harness).config_key,
                "id_prefix": get_harness(harness).id_prefix,
                "watch_subdirs": list(get_harness(harness).watch_subdirs),
                "file_based": get_harness(harness).file_based,
                "effective_roots": [str(resolved)] if resolved is not None else [],
            }
        )

    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return

    payload_rows = [
        {
            "harness": str(row["harness"]),
            "exists": "yes" if bool(row["exists"]) else "no",
            "sessions": _format_int(cast(int, row["sessions"])),
            "messages": _format_int(cast(int, row["messages"])),
            "tokens": _format_int(cast(int, row["tokens"])),
            "source_path": str(row["source_path"]),
            "warning": str(row["warning"]),
            "config_key": str(row.get("config_key") or ""),
            "id_prefix": str(row.get("id_prefix") or ""),
        }
        for row in rows
    ]
    _print_table(
        payload_rows,
        [
            "harness",
            "exists",
            "sessions",
            "messages",
            "tokens",
            "source_path",
            "config_key",
            "id_prefix",
            "warning",
        ],
        {
            "harness": "harness",
            "exists": "exists",
            "sessions": "sessions",
            "messages": "messages",
            "tokens": "tokens",
            "source_path": "source_path",
            "config_key": "config_key",
            "id_prefix": "id_prefix",
            "warning": "warning",
        },
        rich_output=False,
    )


@prices_app.command("list")
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
    if used_only and missing_only:
        _exit_with_error("Use either --used-only or --missing-only, not both.")
    if used_only or missing_only:
        refresh_results = _refresh_before_report(
            ctx,
            enabled=refresh,
            details=refresh_details,
            json_output=json_output,
            include_raw_json=raw,
        )
        costing_config = _load_costing_config_or_exit(ctx)
        conn = _open_toktrail_connection(ctx)
        try:
            report = summarize_usage(
                conn,
                UsageReportFilter(tracking_session_id=None),
                costing_config=costing_config,
            )
        finally:
            conn.close()
        if missing_only:
            rows = _filter_unconfigured_models(
                report.unconfigured_models,
                price_state="unpriced",
                min_messages=None,
                min_tokens=None,
            )
            if json_output:
                typer.echo(
                    json.dumps(
                        _wrap_refresh_json_payload(
                            [row.as_dict() for row in rows],
                            refresh_results=refresh_results,
                            include_refresh=refresh_details,
                        ),
                        indent=2,
                    )
                )
                return
            _print_unconfigured_model_table(rows, rich_output=rich_output)
            return
        model_rows = _filter_model_rows(
            report.by_model,
            price_state="all",
            min_messages=None,
            min_tokens=None,
            sort="provider",
            limit=limit,
        )
        if json_output:
            typer.echo(
                json.dumps(
                    _wrap_refresh_json_payload(
                        [row.as_dict() for row in model_rows],
                        refresh_results=refresh_results,
                        include_refresh=refresh_details,
                    ),
                    indent=2,
                )
            )
            return
        _print_model_table(model_rows, rich_output=rich_output)
        return

    loaded = _load_resolved_costing_config_or_exit(ctx)
    try:
        filters = _normalize_price_display_filter(
            table=table,
            provider=provider,
            model=model,
            query=query,
            category=category,
            release_status=release_status,
            sort=sort,
            limit=limit,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
    price_rows = _filter_price_rows(_price_rows(loaded.config, filters.table), filters)
    if json_output:
        typer.echo(json.dumps(price_rows, indent=2))
        return
    _print_price_table(price_rows, aliases=aliases, rich_output=rich_output)


def _default_pricing_parse_output_path(ctx: typer.Context, provider: str) -> Path:
    return _resolve_prices_dir(ctx) / f"{normalize_identity(provider)}.toml"


def _is_provider_price_file(ctx: typer.Context, target: Path, provider: str) -> bool:
    expected = _default_pricing_parse_output_path(ctx, provider)
    try:
        return target.resolve() == expected.resolve()
    except OSError:
        return target.absolute() == expected.absolute()


@prices_app.command("parse")
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
    if table not in {"virtual", "actual"}:
        _exit_with_error("--table must be one of: virtual, actual.")
    if merge and replace_provider:
        _exit_with_error("Use either --merge or --replace-provider, not both.")
    if input_path is None:
        text = typer.get_text_stream("stdin").read()
    else:
        text = input_path.read_text(encoding="utf-8")

    try:
        parsed = parse_price_document(
            text,
            provider=provider,
            table=cast(Literal["virtual", "actual"], table),
            tier=tier,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))

    if output_path is None:
        target = _default_pricing_parse_output_path(ctx, provider)
    elif output_path == "-":
        target = None
    else:
        raw = Path(output_path).expanduser()
        target = (
            raw / f"{normalize_identity(provider)}.toml"
            if raw.exists() and raw.is_dir()
            else raw
        )

    if target is None and (merge or replace_provider):
        _exit_with_error("--merge and --replace-provider require file output.")

    write_mode = "stdout" if target is None else "render"
    if target is not None and target.exists():
        if merge:
            write_mode = "merge"
        elif (
            replace_provider
            or output_path is None
            or _is_provider_price_file(ctx, target, provider)
        ):
            write_mode = "replace-provider"
        else:
            _exit_with_error(
                f"Refusing to overwrite existing {target}; pass --merge, "
                "--replace-provider, or --output - for stdout."
            )
    elif target is not None:
        if merge:
            write_mode = "merge"
        elif replace_provider:
            write_mode = "replace-provider"

    include_metadata = target is not None and _is_provider_price_file(
        ctx, target, provider
    )
    source_label = str(input_path) if input_path is not None else "stdin"
    metadata = (
        {
            "generated_by": "toktrail prices parse",
            "provider": normalize_identity(provider),
            "source": source_label,
            "tier": tier,
        }
        if include_metadata
        else None
    )

    if write_mode in {"merge", "replace-provider"}:
        existing_text = (
            target.read_text(encoding="utf-8")
            if target is not None and target.exists()
            else None
        )
        output_text = merge_prices_document(
            existing_text=existing_text,
            parsed=parsed,
            replace_provider=(write_mode == "replace-provider"),
            metadata=metadata,
        )
    else:
        output_text = render_prices_toml(
            virtual_prices=parsed.prices if parsed.table == "virtual" else (),
            actual_prices=parsed.prices if parsed.table == "actual" else (),
            metadata=metadata,
        )

    wrote = False
    if target is not None and not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output_text, encoding="utf-8")
        wrote = True

    if json_output:
        payload = {
            "provider": parsed.provider,
            "table": parsed.table,
            "price_count": len(parsed.prices),
            "warnings": list(parsed.warnings),
            "output": str(target) if target is not None else "-",
            "out": str(target) if target is not None else "-",
            "wrote": wrote,
            "dry_run": dry_run,
            "mode": write_mode,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    if target is None or dry_run:
        typer.echo(output_text)
    else:
        typer.echo(f"Wrote prices TOML: {target}")
    for warning in parsed.warnings:
        typer.echo(f"warning: {warning}", err=True)


@app.command("refresh")
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
    """Refresh usage from configured sources or a single explicit harness.

    Can operate in two modes:
    - Explicit: with --harness and --source parameters
    - Config-based: from configuration file (when neither parameter is provided)
    """
    # If both harness and source are provided, use explicit refresh mode
    if harness is not None and source is not None:
        explicit_include_raw = False if raw is None else raw
        try:
            result = _run_harness_import_with_dry_run(
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
            from dataclasses import asdict

            output = asdict(result)
            # Convert Path to string for JSON serialization and normalize harness name
            if "source_path" in output:
                output["source_path"] = str(output["source_path"])
            if "harness" in output:
                output["harness"] = output["harness"].lower()
            if dry_run:
                output["dry_run"] = True
            typer.echo(json.dumps([output], indent=2))
            return

        if not dry_run and result.rows_imported > 0:
            maybe_auto_export_to_git_repo(ctx, reason="refresh explicit")

        _print_refresh_result(result)
        if dry_run:
            typer.echo("\n[dry-run: changes were not persisted]")

    # Otherwise, use config-based refresh mode
    elif harness is None and source is None:
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
        if _refresh_results_changed(results):
            maybe_auto_export_to_git_repo(ctx, reason="refresh configured")
        _print_configured_refresh_results(results)

    else:
        _exit_with_error(
            "Either provide both --harness and --source, "
            "or neither for config-based refresh"
        )


@app.command("watch")
def watch(
    ctx: typer.Context,
    run_id: RunOption = None,
    harnesses: HarnessesOption = None,
    interval: IntervalOption = 2.0,
    raw: RawModeOption = None,
    json_output: JsonOption = False,
) -> None:
    """Watch configured harnesses and print token usage deltas for the active run."""
    harness_list: list[str] | None = harnesses
    try:
        _watch_configured(
            ctx,
            tracking_session_id=run_id,
            harnesses=harness_list,
            interval=interval,
            include_raw_json=raw,
            json_output=json_output,
        )
    except (ToktrailError, OSError, ValueError) as exc:
        _exit_with_error(str(exc))


@sources_app.command("skipped")
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


@sources_app.command("sessions")
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


@sources_app.command("session")
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


@analyze_app.command("cache")
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

    _print_session_cache_analysis_report(
        report,
        utc=utc,
        include_calls=include_calls,
        rich_output=rich_output,
    )


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


@copilot_app.command("env")
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


def _run_harness_import(
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
                _missing_source_path_message(
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
        source_path=resolved_source,
        run_id=selected_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=insert_result.rows_inserted,
        rows_skipped=rows_skipped,
    )


def _run_harness_import_with_dry_run(
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
    """Run harness import with optional dry-run mode.

    In dry-run mode, changes are rolled back before connection closes.
    Can operate with or without an active tracking session.
    """
    harness = get_harness(harness_name)
    conn = _open_toktrail_connection(ctx)
    try:
        resolved_source = harness.resolve_source_path(source_path)
        if resolved_source is None or not resolved_source.exists():
            _exit_with_error(
                _missing_source_path_message(
                    harness_name,
                    resolved_source,
                    explicit_source=source_path,
                )
            )

        selected_session_id = tracking_session_id
        if selected_session_id is None and not no_session:
            selected_session_id = get_active_tracking_session(conn)

        # If no session is provided and no active session exists, it's OK
        # Just scan without filtering by session start time
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

        # Insert events if not in dry-run mode
        # Works with or without a tracking session (selected_session_id can be None)
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
        source_path=resolved_source,
        run_id=selected_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=insert_result.rows_inserted,
        rows_skipped=rows_skipped,
    )


def _resolve_watch_session_id(
    ctx: typer.Context,
    tracking_session_id: int | None,
) -> int:
    conn = _open_toktrail_connection(ctx)
    try:
        selected = tracking_session_id
        if selected is None:
            selected = get_active_tracking_session(conn)
        if selected is None:
            _exit_with_error(
                "No active run found. Start one with "
                "`toktrail run start --name <name>`."
            )
        session = get_tracking_session(conn, selected)
        if session is None:
            _exit_with_error(f"Run not found: {selected}")
        if session.ended_at_ms is not None:
            _exit_with_error(f"Run is already stopped: {selected}")
        return selected
    finally:
        conn.close()


def _watch_report(
    ctx: typer.Context,
    *,
    session_id: int,
    costing_config: CostingConfig,
) -> InternalRunReport:
    conn = _open_toktrail_connection(ctx)
    try:
        return summarize_usage(
            conn,
            UsageReportFilter(tracking_session_id=session_id),
            costing_config=costing_config,
        )
    finally:
        conn.close()


def _message_count(report: InternalRunReport) -> int:
    return sum(row.message_count for row in report.by_harness)


def _watch_totals_from_report(report: InternalRunReport) -> WatchTotals:
    return WatchTotals(
        message_count=_message_count(report),
        tokens=report.totals.tokens,
        costs=report.totals.costs,
    )


def _subtract_tokens(after: TokenBreakdown, before: TokenBreakdown) -> TokenBreakdown:
    return TokenBreakdown(
        input=after.input - before.input,
        output=after.output - before.output,
        reasoning=after.reasoning - before.reasoning,
        cache_read=after.cache_read - before.cache_read,
        cache_write=after.cache_write - before.cache_write,
        cache_output=after.cache_output - before.cache_output,
    )


def _subtract_costs(after: CostTotals, before: CostTotals) -> CostTotals:
    return CostTotals(
        source_cost_usd=after.source_cost_usd - before.source_cost_usd,
        actual_cost_usd=after.actual_cost_usd - before.actual_cost_usd,
        virtual_cost_usd=after.virtual_cost_usd - before.virtual_cost_usd,
        unpriced_count=after.unpriced_count - before.unpriced_count,
    )


def _subtract_totals(after: WatchTotals, before: WatchTotals) -> WatchTotals:
    return WatchTotals(
        message_count=after.message_count - before.message_count,
        tokens=_subtract_tokens(after.tokens, before.tokens),
        costs=_subtract_costs(after.costs, before.costs),
    )


def _watch_delta_has_activity(delta: WatchDelta) -> bool:
    totals = delta.totals
    return any(
        [
            totals.message_count != 0,
            totals.tokens.input != 0,
            totals.tokens.output != 0,
            totals.tokens.reasoning != 0,
            totals.tokens.cache_read != 0,
            totals.tokens.cache_write != 0,
            totals.tokens.cache_output != 0,
            totals.costs.source_cost_usd != 0,
            totals.costs.actual_cost_usd != 0,
            totals.costs.virtual_cost_usd != 0,
            totals.costs.unpriced_count != 0,
        ]
    )


def _by_harness_totals(report: InternalRunReport) -> dict[str, WatchTotals]:
    return {
        row.harness: WatchTotals(
            message_count=row.message_count,
            tokens=row.tokens,
            costs=row.costs,
        )
        for row in report.by_harness
    }


def _watch_delta(previous: InternalRunReport, current: InternalRunReport) -> WatchDelta:
    before_totals = _watch_totals_from_report(previous)
    after_totals = _watch_totals_from_report(current)
    before_by_harness = _by_harness_totals(previous)
    after_by_harness = _by_harness_totals(current)

    by_harness_delta: dict[str, WatchTotals] = {}
    for harness_name in {*before_by_harness, *after_by_harness}:
        before_h = before_by_harness.get(
            harness_name,
            WatchTotals(message_count=0, tokens=TokenBreakdown(), costs=CostTotals()),
        )
        after_h = after_by_harness.get(
            harness_name,
            WatchTotals(message_count=0, tokens=TokenBreakdown(), costs=CostTotals()),
        )
        delta = _subtract_totals(after_h, before_h)
        if _watch_delta_has_activity(WatchDelta(totals=delta, by_harness={})):
            by_harness_delta[harness_name] = delta

    return WatchDelta(
        totals=_subtract_totals(after_totals, before_totals),
        by_harness=by_harness_delta,
    )


def _print_watch_start(
    ctx: typer.Context,
    session_id: int,
    harnesses: list[str] | None,
) -> None:
    conn = _open_toktrail_connection(ctx)
    try:
        session = get_tracking_session(conn, session_id)
    finally:
        conn.close()
    name = session.name if session and session.name else str(session_id)
    typer.echo(f"Watching configured harnesses for run {session_id}: {name}")

    if harnesses is not None:
        harness_names = sorted(set(harnesses))
    else:
        loaded = _load_resolved_toktrail_config_or_exit(ctx)
        harness_names = sorted(loaded.config.imports.harnesses)
    typer.echo(f"Sources: {', '.join(harness_names)}")
    typer.echo("")


def _print_watch_delta(
    delta: WatchDelta,
    current_report: InternalRunReport,
) -> None:
    now = datetime.datetime.now().strftime("%H:%M:%S")
    totals = delta.totals
    line = (
        f"{now}  "
        f"{_format_signed_int(totals.message_count)} msgs  "
        f"{_format_token_delta(totals.tokens)}  "
        f"actual={_format_cost(totals.costs.actual_cost_usd)} "
        f"virtual={_format_cost(totals.costs.virtual_cost_usd)} "
        f"savings={_format_cost(totals.costs.savings_usd)}"
    )
    typer.echo(line)

    for harness_name in sorted(delta.by_harness):
        h_total = delta.by_harness[harness_name]
        h_line = (
            f"  {harness_name:<10} "
            f"{_format_signed_int(h_total.message_count)} msg   "
            f"{_format_signed_int(h_total.tokens.total)} tokens  "
            f"actual={_format_cost(h_total.costs.actual_cost_usd)} "
            f"virtual={_format_cost(h_total.costs.virtual_cost_usd)}"
        )
        typer.echo(h_line)


def _print_watch_delta_json(
    session_id: int,
    delta: WatchDelta,
    current_report: InternalRunReport,
) -> None:
    totals = delta.totals
    event: dict[str, object] = {
        "type": "usage_delta",
        "run_id": session_id,
        "created_ms": int(time.time() * 1000),
        "delta": {
            "message_count": totals.message_count,
            **totals.tokens.as_dict(),
            **totals.costs.as_dict(),
        },
        "cumulative": {
            "message_count": _message_count(current_report),
            **current_report.totals.tokens.as_dict(),
            **current_report.totals.costs.as_dict(),
        },
        "by_harness": [
            {
                "harness": harness_name,
                "message_count": h_total.message_count,
                **h_total.tokens.as_dict(),
                **h_total.costs.as_dict(),
            }
            for harness_name in sorted(delta.by_harness)
            for h_total in [delta.by_harness[harness_name]]
        ],
    }
    typer.echo(json.dumps(event))


def _print_watch_stop(observed: WatchDelta) -> None:
    typer.echo("Stopped watching.")
    typer.echo("Observed during watch:")
    totals = observed.totals
    typer.echo(f"  messages:   {_format_int(totals.message_count)}")
    typer.echo(f"  tokens:     {_format_int(totals.tokens.total)}")
    typer.echo(f"  input:      {_format_int(totals.tokens.input)}")
    typer.echo(f"  output:     {_format_int(totals.tokens.output)}")
    typer.echo(f"  reasoning:  {_format_int(totals.tokens.reasoning)}")
    typer.echo(f"  cache_r:    {_format_int(totals.tokens.cache_read)}")
    typer.echo(f"  cache_w:    {_format_int(totals.tokens.cache_write)}")
    typer.echo(f"  cache_o:    {_format_int(totals.tokens.cache_output)}")
    typer.echo(f"  actual:     {_format_cost(totals.costs.actual_cost_usd)}")
    typer.echo(f"  virtual:    {_format_cost(totals.costs.virtual_cost_usd)}")
    typer.echo(f"  savings:    {_format_cost(totals.costs.savings_usd)}")


def _watch_configured(
    ctx: typer.Context,
    *,
    tracking_session_id: int | None,
    harnesses: list[str] | None,
    interval: float,
    include_raw_json: bool | None,
    json_output: bool,
) -> None:
    selected_session_id = _resolve_watch_session_id(ctx, tracking_session_id)
    costing_config = _load_costing_config_or_exit(ctx)

    previous_report = _watch_report(
        ctx,
        session_id=selected_session_id,
        costing_config=costing_config,
    )
    baseline_report = previous_report

    if not json_output:
        _print_watch_start(ctx, selected_session_id, harnesses)

    try:
        while True:
            import_configured_usage_api(
                _resolve_state_db(ctx),
                harnesses=harnesses,
                source_path=None,
                session_id=selected_session_id,
                use_active_session=False,
                include_raw_json=include_raw_json,
                config_path=_resolve_config_path(ctx),
                since_start=True,
            )
            current_report = _watch_report(
                ctx,
                session_id=selected_session_id,
                costing_config=costing_config,
            )
            delta = _watch_delta(previous_report, current_report)
            if _watch_delta_has_activity(delta):
                if json_output:
                    _print_watch_delta_json(selected_session_id, delta, current_report)
                else:
                    _print_watch_delta(delta, current_report)
            previous_report = current_report
            time.sleep(interval)
    except KeyboardInterrupt:
        final_report = _watch_report(
            ctx,
            session_id=selected_session_id,
            costing_config=costing_config,
        )
        observed = _watch_delta(baseline_report, final_report)
        if not json_output:
            typer.echo("")
            _print_watch_stop(observed)


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
    if mode == "auto" and harness is None:
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
    if _refresh_results_changed(results):
        maybe_auto_export_to_git_repo(ctx, reason="statusline refresh")
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
    if source_path is None or not source_path.exists():
        return True
    if source_path.is_dir():
        if cache_metadata is None:
            return False
        created_ms = cache_metadata.get("created_ms")
        if not isinstance(created_ms, int):
            return False
        return int(time.time() * 1000) - created_ms < min_refresh_interval_secs * 1000
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
    if not enabled:
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
            refresh_mode="quick",
            since_start=since_start,
        )
    except (OSError, ValueError, ToktrailError) as exc:
        _exit_with_error(str(exc))

    if _refresh_results_changed(results):
        maybe_auto_export_to_git_repo(ctx, reason="report refresh")

    if details and not json_output:
        _print_configured_refresh_results(results)
    return results


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


def _missing_source_path_message(
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


def _print_refresh_result(
    result: ImportExecutionResult,
) -> None:
    typer.echo(f"Refreshed {result.harness} usage:")
    typer.echo(f"  source path: {result.source_path}")
    typer.echo(f"  run: {result.run_id}")
    typer.echo(f"  rows seen: {result.rows_seen}")
    typer.echo(f"  rows imported: {result.rows_imported}")
    typer.echo(f"  rows skipped: {result.rows_skipped}")


def _print_configured_refresh_results(results: tuple[ImportUsageResult, ...]) -> None:
    typer.echo("Refreshed usage")
    rows = []
    for result in results:
        rows.append(
            {
                "harness": result.harness,
                "inserted": _format_int(result.rows_imported),
                "linked": _format_int(result.rows_linked),
                "scope_excluded": _format_int(result.rows_scope_excluded),
                "skipped": _format_int(result.rows_skipped),
                "status": result.status,
            }
        )
    _print_table(
        rows,
        ["harness", "inserted", "linked", "scope_excluded", "skipped", "status"],
        {
            "harness": "harness",
            "inserted": "inserted",
            "linked": "linked",
            "scope_excluded": "scope excl",
            "skipped": "skipped",
            "status": "status",
        },
        rich_output=False,
        numeric_columns={"inserted", "linked", "scope_excluded", "skipped"},
    )


def _refresh_results_changed(results: tuple[ImportUsageResult, ...]) -> bool:
    for result in results:
        if result.rows_imported > 0 or result.rows_linked > 0:
            return True
    return False


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


def _resolve_assignment_machine_id_or_exit(
    conn: sqlite3.Connection,
    *,
    harness: str,
    source_session_id: str,
    machine: str | None,
) -> str:
    if machine is not None:
        resolved = _resolve_machine_id_or_exit(conn, machine)
        if resolved is None:
            msg = "Machine selector did not resolve to a machine id."
            raise TypeError(msg)
        return resolved
    rows = conn.execute(
        """
        SELECT DISTINCT origin_machine_id
        FROM usage_events
        WHERE harness = ?
          AND source_session_id = ?
          AND origin_machine_id IS NOT NULL
        ORDER BY origin_machine_id
        """,
        (harness, source_session_id),
    ).fetchall()
    if not rows:
        _exit_with_error(
            f"No imported source session matched {harness}/{source_session_id}."
        )
    if len(rows) > 1:
        _exit_with_error("Source session matched multiple machines. Specify --machine.")
    value = rows[0]["origin_machine_id"]
    if not isinstance(value, str):
        msg = "Expected origin_machine_id to be a string."
        raise TypeError(msg)
    return value


def _resolve_session_key_or_exit(
    conn: sqlite3.Connection,
    session_key: str,
) -> tuple[str, str, str]:
    parts = session_key.split("/", 2)
    if len(parts) != 3:
        _exit_with_error(
            "Session key must be machine/harness/source_session_id, "
            f"got {session_key!r}."
        )
    machine_selector, harness, source_session_id = (
        parts[0].strip(),
        parts[1].strip(),
        parts[2].strip(),
    )
    if not machine_selector or not harness or not source_session_id:
        _exit_with_error(
            "Session key must be machine/harness/source_session_id "
            "with non-empty segments."
        )
    selector_candidates = [machine_selector]
    if machine_selector.startswith("machine:"):
        selector_candidates.append(machine_selector.split(":", 1)[1])
    if machine_selector.endswith(")") and "(" in machine_selector:
        selector_candidates.append(machine_selector.rsplit("(", 1)[1].rstrip(")"))
    machine_id: str | None = None
    last_error: ValueError | None = None
    for candidate in selector_candidates:
        try:
            machine_id = resolve_machine_selector(conn, candidate).machine_id
            break
        except ValueError as exc:
            last_error = exc
    if machine_id is None:
        _exit_with_error(
            str(last_error)
            if last_error is not None
            else "Invalid machine selector in session key."
        )
    return machine_id, harness, source_session_id


def _parse_area_expiry_or_exit(*, ttl: str | None, until: str | None) -> int | None:
    if ttl is None and until is None:
        return None
    now_ms = int(time.time() * 1000)
    if ttl is not None:
        ttl_text = ttl.strip().lower()
        if not ttl_text:
            _exit_with_error("--ttl must not be empty.")
        token_pattern = re.compile(r"(\d+)([smhd])")
        offset_ms = 0
        consumed = 0
        for match in token_pattern.finditer(ttl_text):
            value = int(match.group(1))
            unit = match.group(2)
            consumed += len(match.group(0))
            multiplier = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]
            offset_ms += value * multiplier
        if consumed != len(ttl_text) or offset_ms <= 0:
            _exit_with_error("--ttl must look like 30m, 4h, 1d, or 1h30m.")
        return now_ms + offset_ms
    assert until is not None
    raw = until.strip()
    if not raw:
        _exit_with_error("--until must not be empty.")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        _exit_with_error("--until must be an ISO datetime, e.g. 2026-05-16T18:00.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
    expires_at_ms = int(parsed.timestamp() * 1000)
    if expires_at_ms <= now_ms:
        _exit_with_error("--until must be in the future.")
    return expires_at_ms


def _resolve_git_root(cwd: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = completed.stdout.strip()
    return Path(text) if text else None


def _git_remote_origin(cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "config", "--get", "remote.origin.url"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value or None


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


register_machine_commands(
    machine_app,
    load_machine_config_or_exit=_load_machine_config_or_exit,
    open_toktrail_connection=_open_toktrail_connection,
    resolve_machine_config_path=_resolve_machine_config_path,
    exit_with_error=_exit_with_error,
)
