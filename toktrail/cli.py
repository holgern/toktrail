from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from toktrail.adapters.base import SourceSessionSummary
from toktrail.adapters.registry import get_harness
from toktrail.adapters.summary import (
    summarize_event_totals,
    summarize_events_by_agent,
    summarize_events_by_model,
)
from toktrail.api.environment import prepare_environment as prepare_api_environment
from toktrail.api.imports import import_configured_usage as import_configured_usage_api
from toktrail.api.models import ImportUsageResult
from toktrail.config import (
    DEFAULT_TEMPLATE_NAME,
    CostingConfig,
    LoadedCostingConfig,
    Price,
    load_resolved_costing_config,
    normalize_identity,
    render_config_template,
    summarize_costing_config,
)
from toktrail.db import (
    connect,
    create_tracking_session,
    end_tracking_session,
    get_active_tracking_session,
    get_tracking_session,
    insert_usage_events,
    list_tracking_sessions,
    migrate,
    summarize_usage,
)
from toktrail.errors import InvalidAPIUsageError, ToktrailError
from toktrail.formatting import format_epoch_ms_compact
from toktrail.models import UsageEvent
from toktrail.paths import (
    new_copilot_otel_file_path,
    resolve_toktrail_config_path,
    resolve_toktrail_db_path,
)
from toktrail.periods import resolve_time_range
from toktrail.reporting import (
    ModelSummaryRow,
    UnconfiguredModelRow,
    UsageReportFilter,
)
from toktrail.reporting import (
    TrackingSessionReport as InternalTrackingSessionReport,
)

app = typer.Typer(help="Track harness token usage in local SQLite sessions.")
import_app = typer.Typer(help="Import usage from external harnesses.")
watch_app = typer.Typer(help="Watch external harnesses and import new usage.")
sessions_app = typer.Typer(
    invoke_without_command=True,
    help="List toktrail tracking sessions and raw source sessions.",
)
copilot_app = typer.Typer(help="Inspect and run GitHub Copilot CLI tracking.")
config_app = typer.Typer(help="Inspect toktrail pricing config.")

app.add_typer(import_app, name="import")
app.add_typer(watch_app, name="watch")
app.add_typer(sessions_app, name="sessions")
app.add_typer(copilot_app, name="copilot")
app.add_typer(config_app, name="config")

_VALID_REPORT_PRICE_STATES = {"all", "priced", "unpriced"}
_VALID_REPORT_SORTS = {
    "actual",
    "virtual",
    "savings",
    "tokens",
    "messages",
    "provider",
    "model",
    "unpriced",
}
_VALID_PRICE_TABLES = {"virtual", "actual", "all"}
_VALID_PRICE_SORTS = {
    "provider",
    "model",
    "input",
    "cached",
    "cache-write",
    "output",
    "reasoning",
    "category",
    "release",
}


@dataclass(frozen=True)
class ImportExecutionResult:
    harness: str
    source_path: Path
    tracking_session_id: int
    rows_seen: int
    rows_imported: int
    rows_skipped: int


@dataclass(frozen=True)
class ReportDisplayFilter:
    price_state: str = "all"
    min_messages: int | None = None
    min_tokens: int | None = None
    sort: str = "actual"
    limit: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "price_state": self.price_state,
            "min_messages": self.min_messages,
            "min_tokens": self.min_tokens,
            "sort": self.sort,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class PriceDisplayFilter:
    table: str = "virtual"
    provider: str | None = None
    model: str | None = None
    query: str | None = None
    category: str | None = None
    release_status: str | None = None
    sort: str = "provider"
    limit: int | None = None


CopilotEnvVar = tuple[str, str]


DbPathOption = Annotated[
    Path | None,
    typer.Option("--db", help="Override toktrail DB path."),
]
ConfigPathOption = Annotated[
    Path | None,
    typer.Option("--config", help="Override toktrail config TOML path."),
]
SessionArgument = Annotated[int | None, typer.Argument()]
SessionOption = Annotated[int | None, typer.Option("--session")]
SourceSessionOption = Annotated[str | None, typer.Option("--source-session")]
NameOption = Annotated[str | None, typer.Option("--name")]
JsonOption = Annotated[bool, typer.Option("--json")]
HarnessOption = Annotated[str | None, typer.Option("--harness")]
HarnessesOption = Annotated[list[str] | None, typer.Option("--harness")]
ProviderOption = Annotated[str | None, typer.Option("--provider")]
ModelOption = Annotated[str | None, typer.Option("--model")]
ThinkingOption = Annotated[str | None, typer.Option("--thinking")]
AgentOption = Annotated[str | None, typer.Option("--agent")]
SinceMsOption = Annotated[int | None, typer.Option("--since-ms")]
UntilMsOption = Annotated[int | None, typer.Option("--until-ms")]
SourceSessionArgument = Annotated[str | None, typer.Argument()]
LastOption = Annotated[bool, typer.Option("--last")]
BreakdownOption = Annotated[bool, typer.Option("--breakdown")]
UtcOption = Annotated[bool, typer.Option("--utc")]
LimitOption = Annotated[int | None, typer.Option("--limit", min=1)]
SortOption = Annotated[str, typer.Option("--sort")]
ColumnsOption = Annotated[str | None, typer.Option("--columns")]
RichOption = Annotated[bool, typer.Option("--rich")]
CollapseThinkingOption = Annotated[bool, typer.Option("--collapse-thinking")]
TimeBoundaryOption = Annotated[str | None, typer.Option("--since")]
UntilBoundaryOption = Annotated[str | None, typer.Option("--until")]
TimezoneOption = Annotated[str | None, typer.Option("--timezone")]
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
SinceStartOption = Annotated[bool, typer.Option("--since-start")]
NoRawOption = Annotated[bool, typer.Option("--no-raw")]
IntervalOption = Annotated[float, typer.Option("--interval", min=0.1)]
CopilotRunArgs = Annotated[list[str], typer.Argument(help="Command to run after --.")]
SourcePathOption = Annotated[Path | None, typer.Option("--source")]
RawOption = Annotated[bool | None, typer.Option("--raw/--no-raw")]


@app.callback()
def main(
    ctx: typer.Context,
    db_path: DbPathOption = None,
    config_path: ConfigPathOption = None,
) -> None:
    ctx.obj = {"db_path": db_path, "config_path": config_path}


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
    harness: HarnessOption = None,
    source_session_id: SourceSessionOption = None,
    provider_id: ProviderOption = None,
    model_id: ModelOption = None,
    thinking_level: ThinkingOption = None,
    agent: AgentOption = None,
    since_ms: SinceMsOption = None,
    until_ms: UntilMsOption = None,
    rich_output: RichOption = False,
    collapse_thinking: CollapseThinkingOption = False,
    price_state: PriceStateOption = "all",
    min_messages: MinMessagesOption = None,
    min_tokens: MinTokensOption = None,
    sort: ReportSortOption = "actual",
    limit: ReportLimitOption = None,
) -> None:
    costing_config = _load_costing_config_or_exit(ctx)
    display_filters = _normalize_report_display_filter(
        price_state=price_state,
        min_messages=min_messages,
        min_tokens=min_tokens,
        sort=sort,
        limit=limit,
    )
    conn = _open_toktrail_connection(ctx)
    try:
        selected_session_id = session_id
        if selected_session_id is None:
            selected_session_id = get_active_tracking_session(conn)
        if selected_session_id is None:
            _exit_with_error("No active tracking session found.")

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
                split_thinking=not collapse_thinking,
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
        typer.echo(json.dumps(payload, indent=2))
        return

    session = report.session
    if session is None:
        msg = "Tracking session report unexpectedly has no session."
        raise TypeError(msg)
    typer.echo(f"toktrail session {session.id}: {session.name or '(unnamed)'}")
    _print_usage_summary(
        report,
        rich_output=rich_output,
        by_model=filtered_by_model,
        unconfigured_models=filtered_unconfigured,
        missing_price_mode=costing_config.missing_price,
    )


@app.command()
def usage(
    ctx: typer.Context,
    period: Annotated[str | None, typer.Argument()] = None,
    json_output: JsonOption = False,
    harness: HarnessOption = None,
    source_session_id: SourceSessionOption = None,
    provider_id: ProviderOption = None,
    model_id: ModelOption = None,
    thinking_level: ThinkingOption = None,
    agent: AgentOption = None,
    since: TimeBoundaryOption = None,
    until: UntilBoundaryOption = None,
    timezone_name: TimezoneOption = None,
    utc: UtcOption = False,
    rich_output: RichOption = False,
    collapse_thinking: CollapseThinkingOption = False,
    price_state: PriceStateOption = "all",
    min_messages: MinMessagesOption = None,
    min_tokens: MinTokensOption = None,
    sort: ReportSortOption = "actual",
    limit: ReportLimitOption = None,
) -> None:
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
    display_filters = _normalize_report_display_filter(
        price_state=price_state,
        min_messages=min_messages,
        min_tokens=min_tokens,
        sort=sort,
        limit=limit,
    )
    conn = _open_toktrail_connection(ctx)
    try:
        report = summarize_usage(
            conn,
            UsageReportFilter(
                tracking_session_id=None,
                harness=harness,
                source_session_id=source_session_id,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                agent=agent,
                since_ms=resolved_range.since_ms,
                until_ms=resolved_range.until_ms,
                split_thinking=not collapse_thinking,
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
        typer.echo(json.dumps(payload, indent=2))
        return

    title = "toktrail usage"
    if resolved_range.period is not None:
        title = f"{title} ({resolved_range.period})"
    typer.echo(title)
    _print_usage_summary(
        report,
        rich_output=rich_output,
        by_model=filtered_by_model,
        unconfigured_models=filtered_unconfigured,
        missing_price_mode=costing_config.missing_price,
    )


def _print_usage_summary(
    report: InternalTrackingSessionReport,
    *,
    rich_output: bool,
    by_model: list[ModelSummaryRow] | None = None,
    unconfigured_models: list[UnconfiguredModelRow] | None = None,
    missing_price_mode: str = "warn",
) -> None:
    typer.echo("")
    typer.echo("Totals")
    totals = report.totals
    typer.echo(f"  input:       {_format_int(totals.tokens.input)}")
    typer.echo(f"  output:      {_format_int(totals.tokens.output)}")
    typer.echo(f"  reasoning:   {_format_int(totals.tokens.reasoning)}")
    typer.echo(f"  cache read:  {_format_int(totals.tokens.cache_read)}")
    typer.echo(f"  cache write: {_format_int(totals.tokens.cache_write)}")
    typer.echo(f"  total:       {_format_int(totals.tokens.total)}")

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
    typer.echo("By harness")
    by_harness = report.by_harness
    if by_harness:
        for harness_row in by_harness:
            typer.echo(
                f"  {harness_row.harness:<12}"
                f"{_format_int(harness_row.total_tokens):>12} tokens   "
                f"actual {_format_cost(harness_row.actual_cost_usd)}   "
                f"virtual {_format_cost(harness_row.virtual_cost_usd)}   "
                f"savings {_format_cost(harness_row.savings_usd)}"
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
    typer.echo("By agent")
    by_agent = report.by_agent
    if by_agent:
        for agent_row in by_agent:
            typer.echo(
                f"  {agent_row.agent:<12}"
                f"{_format_int(agent_row.total_tokens):>12} tokens   "
                f"actual {_format_cost(agent_row.actual_cost_usd)}   "
                f"virtual {_format_cost(agent_row.virtual_cost_usd)}   "
                f"savings {_format_cost(agent_row.savings_usd)}"
            )
    else:
        typer.echo("  (none)")


@config_app.command("path")
def config_path(ctx: typer.Context) -> None:
    typer.echo(_resolve_config_path(ctx))


@config_app.command("init")
def config_init(
    ctx: typer.Context,
    template: Annotated[str, typer.Option("--template")] = DEFAULT_TEMPLATE_NAME,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    path = _resolve_config_path(ctx)
    if path.exists() and not force:
        _exit_with_error(f"Toktrail config already exists: {path}")
    try:
        content = render_config_template(template)
    except ValueError as exc:
        _exit_with_error(str(exc))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    typer.echo(f"Initialized toktrail config: {path}")


@config_app.command("validate")
def config_validate(ctx: typer.Context) -> None:
    loaded = _load_resolved_costing_config_or_exit(ctx)
    summary = summarize_costing_config(loaded.config)
    typer.echo(f"Config valid: {loaded.path}")
    typer.echo(f"  actual rules:   {summary.actual_rule_count}")
    typer.echo(f"  actual prices:  {summary.actual_price_count}")
    typer.echo(f"  virtual prices: {summary.virtual_price_count}")
    warnings = [
        price
        for price in (*loaded.config.actual_prices, *loaded.config.virtual_prices)
        if price.cached_input_usd_per_1m is not None
        and price.cached_input_usd_per_1m > price.input_usd_per_1m
    ]
    for price in warnings:
        typer.echo(
            "  warning: cached_input exceeds input for "
            f"{price.provider}/{price.model}"
        )


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    loaded = _load_resolved_costing_config_or_exit(ctx)
    summary = summarize_costing_config(loaded.config)
    typer.echo(f"path:            {loaded.path}")
    typer.echo(f"exists:          {'yes' if loaded.exists else 'no'}")
    typer.echo(f"config_version:  {summary.config_version}")
    typer.echo(f"default actual:  {summary.default_actual_mode}")
    typer.echo(f"default virtual: {summary.default_virtual_mode}")
    typer.echo(f"missing price:   {summary.missing_price}")
    typer.echo(f"price profile:   {summary.price_profile or '(none)'}")
    typer.echo(f"actual rules:    {summary.actual_rule_count}")
    typer.echo(f"actual prices:   {summary.actual_price_count}")
    typer.echo(f"virtual prices:  {summary.virtual_price_count}")
    typer.echo("Run `toktrail config prices` to inspect configured price rows.")


@config_app.command("prices")
def config_prices(
    ctx: typer.Context,
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
) -> None:
    loaded = _load_resolved_costing_config_or_exit(ctx)
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
    rows = _filter_price_rows(_price_rows(loaded.config, filters.table), filters)
    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return
    _print_price_table(rows, aliases=aliases, rich_output=False)


@sessions_app.callback(invoke_without_command=True)
def sessions(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
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
            f"started={format_epoch_ms_compact(session.started_at_ms)}\t"
            f"ended={format_epoch_ms_compact(session.ended_at_ms)}"
        )


@import_app.callback(invoke_without_command=True)
def import_usage_command(
    ctx: typer.Context,
    harnesses: HarnessesOption = None,
    source_path: SourcePathOption = None,
    session_id: SessionOption = None,
    no_session: Annotated[bool, typer.Option("--no-session")] = False,
    raw: RawOption = None,
    json_output: JsonOption = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if session_id is not None and no_session:
        _exit_with_error("Use either --session or --no-session, not both.")
    try:
        results = import_configured_usage_api(
            _resolve_state_db(ctx),
            harnesses=harnesses,
            source_path=source_path,
            session_id=session_id,
            use_active_session=not no_session,
            include_raw_json=raw,
            config_path=_resolve_config_path(ctx),
        )
    except (OSError, ValueError, ToktrailError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps([result.as_dict() for result in results], indent=2))
        return
    _print_configured_import_results(results)


@import_app.command("opencode")
def import_opencode(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    opencode_db: OpenCodeDbOption = None,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    result = _run_harness_import(
        ctx,
        harness_name="opencode",
        source_path=opencode_db,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        since_start=since_start,
        no_raw=no_raw,
    )
    _print_import_result(result)


@import_app.command("copilot")
def import_copilot(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    copilot_path: CopilotPathOption = None,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    result = _run_harness_import(
        ctx,
        harness_name="copilot",
        source_path=copilot_path,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
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
    result = _run_harness_import(
        ctx,
        harness_name="pi",
        source_path=pi_path,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        since_start=since_start,
        no_raw=no_raw,
    )
    _print_import_result(result)


@import_app.command("codex")
def import_codex(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    codex_path: CodexPathOption = None,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    result = _run_harness_import(
        ctx,
        harness_name="codex",
        source_path=codex_path,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        since_start=since_start,
        no_raw=no_raw,
    )
    _print_import_result(result)


@import_app.command("goose")
def import_goose(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    goose_path: GoosePathOption = None,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    result = _run_harness_import(
        ctx,
        harness_name="goose",
        source_path=goose_path,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
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
    _watch_harness(
        ctx,
        harness_name="opencode",
        source_path=opencode_db,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        interval=interval,
        since_start=since_start,
        no_raw=no_raw,
    )


@watch_app.command("copilot")
def watch_copilot(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    copilot_path: CopilotPathOption = None,
    interval: IntervalOption = 2.0,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    _watch_harness(
        ctx,
        harness_name="copilot",
        source_path=copilot_path,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        interval=interval,
        since_start=since_start,
        no_raw=no_raw,
    )


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
    _watch_harness(
        ctx,
        harness_name="pi",
        source_path=pi_path,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        interval=interval,
        since_start=since_start,
        no_raw=no_raw,
    )


@watch_app.command("codex")
def watch_codex(
    ctx: typer.Context,
    session_id: SessionOption = None,
    source_session_id: SourceSessionOption = None,
    codex_path: CodexPathOption = None,
    interval: IntervalOption = 2.0,
    since_start: SinceStartOption = False,
    no_raw: NoRawOption = False,
) -> None:
    _watch_harness(
        ctx,
        harness_name="codex",
        source_path=codex_path,
        tracking_session_id=session_id,
        source_session_id=source_session_id,
        interval=interval,
        since_start=since_start,
        no_raw=no_raw,
    )


@sessions_app.command("opencode")
def sessions_opencode(
    ctx: typer.Context,
    source_session_id: SourceSessionArgument = None,
    opencode_db: OpenCodeDbOption = None,
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
        "opencode",
        source_path=opencode_db,
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


@sessions_app.command("pi")
def sessions_pi(
    ctx: typer.Context,
    source_session_id: SourceSessionArgument = None,
    pi_path: PiPathOption = None,
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
        "pi",
        source_path=pi_path,
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


@sessions_app.command("codex")
def sessions_codex(
    ctx: typer.Context,
    source_session_id: SourceSessionArgument = None,
    codex_path: CodexPathOption = None,
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
        "codex",
        source_path=codex_path,
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


@sessions_app.command("goose")
def sessions_goose(
    ctx: typer.Context,
    source_session_id: SourceSessionArgument = None,
    goose_path: GoosePathOption = None,
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
        "goose",
        source_path=goose_path,
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


@sessions_app.command("copilot")
def sessions_copilot(
    ctx: typer.Context,
    source_session_id: SourceSessionArgument = None,
    copilot_path: CopilotPathOption = None,
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
        "copilot",
        source_path=copilot_path,
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


@copilot_app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def copilot_run(
    ctx: typer.Context,
    session_id: SessionOption = None,
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
            tracking_session_id=session_id,
            source_session_id=None,
            since_start=False,
            no_raw=no_raw,
        )
        _print_import_result(result)

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
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


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
    no_raw: bool,
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
            _exit_with_error("No active tracking session found.")

        tracking_session = get_tracking_session(conn, selected_session_id)
        if tracking_session is None:
            _exit_with_error(f"Tracking session not found: {selected_session_id}")

        scan = harness.scan(
            resolved_source,
            source_session_id=source_session_id,
            include_raw_json=not no_raw,
        )
        since_ms = tracking_session.started_at_ms if since_start else None
        filtered_events = [
            event
            for event in scan.events
            if since_ms is None or event.created_ms >= since_ms
        ]
        insert_result = insert_usage_events(conn, selected_session_id, filtered_events)
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
        tracking_session_id=selected_session_id,
        rows_seen=scan.rows_seen,
        rows_imported=insert_result.rows_inserted,
        rows_skipped=rows_skipped,
    )


def _watch_harness(
    ctx: typer.Context,
    *,
    harness_name: str,
    source_path: Path | None,
    tracking_session_id: int | None,
    source_session_id: str | None,
    interval: float,
    since_start: bool,
    no_raw: bool,
) -> None:
    harness = get_harness(harness_name)
    total_seen = 0
    total_imported = 0
    total_skipped = 0
    try:
        while True:
            result = _run_harness_import(
                ctx,
                harness_name=harness_name,
                source_path=source_path,
                tracking_session_id=tracking_session_id,
                source_session_id=source_session_id,
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
        typer.echo(f"Stopped watching {harness.display_name}.")
        typer.echo(f"  rows seen:     {total_seen}")
        typer.echo(f"  rows imported: {total_imported}")
        typer.echo(f"  rows skipped:  {total_skipped}")


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
    _print_table(rows, selected_columns, headers, rich_output=rich_output)


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
    by_agent = summarize_events_by_agent(events, costing_config=costing_config)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "harness": harness_display_name.lower(),
                    "source_session_id": summary.source_session_id,
                    "source_paths": list(summary.source_paths),
                    "first_created_ms": summary.first_created_ms,
                    "last_created_ms": summary.last_created_ms,
                    "assistant_message_count": summary.assistant_message_count,
                    "totals": totals.as_dict(),
                    "by_model": [row.as_dict() for row in by_model],
                    "by_agent": [row.as_dict() for row in by_agent],
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
    typer.echo(f"  input:       {_format_int(totals.tokens.input)}")
    typer.echo(f"  output:      {_format_int(totals.tokens.output)}")
    typer.echo(f"  reasoning:   {_format_int(totals.tokens.reasoning)}")
    typer.echo(f"  cache read:  {_format_int(totals.tokens.cache_read)}")
    typer.echo(f"  cache write: {_format_int(totals.tokens.cache_write)}")
    typer.echo(f"  total:       {_format_int(totals.tokens.total)}")
    typer.echo("")
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
    if by_agent:
        typer.echo("")
        typer.echo("By agent")
        for row in by_agent:
            typer.echo(
                f"  {row.agent:<12}"
                f"{_format_int(row.total_tokens):>12} tokens   "
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


def _render_table(
    rows: list[dict[str, str]],
    columns: list[str],
    headers: dict[str, str],
) -> str:
    widths = {
        column: max(
            len(headers[column]),
            *(len(row.get(column, "")) for row in rows),
        )
        for column in columns
    }
    lines = ["  ".join(headers[column].ljust(widths[column]) for column in columns)]
    for row in rows:
        lines.append(
            "  ".join(row.get(column, "").ljust(widths[column]) for column in columns)
        )
    return "\n".join(lines)


def _print_table(
    rows: list[dict[str, str]],
    columns: list[str],
    headers: dict[str, str],
    *,
    rich_output: bool,
) -> None:
    if not rich_output:
        typer.echo(_render_table(rows, columns, headers))
        return

    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        _exit_with_error("Rich output requires installing toktrail[rich].")

    table = Table(show_header=True, header_style="bold")
    for column in columns:
        table.add_column(headers[column])
    for row in rows:
        table.add_row(*(row.get(column, "") for column in columns))
    Console().print(table)


def _print_model_table(
    rows: list[ModelSummaryRow],
    *,
    rich_output: bool,
) -> None:
    headers = {
        "provider_model": "provider/model",
        "thinking": "thinking",
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
    }
    include_thinking = any(row.thinking_level is not None for row in rows)
    payload_rows = [
        {
            "provider_model": f"{row.provider_id}/{row.model_id}",
            "thinking": row.thinking_level or "-",
            "msgs": _format_int(row.message_count),
            "input": _format_int(row.tokens.input),
            "output": _format_int(row.tokens.output),
            "reasoning": _format_int(row.tokens.reasoning),
            "cache_r": _format_int(row.tokens.cache_read),
            "cache_w": _format_int(row.tokens.cache_write),
            "total": _format_int(row.total_tokens),
            "actual": _format_cost(row.actual_cost_usd),
            "virtual": _format_cost(row.virtual_cost_usd),
            "savings": _format_cost(row.savings_usd),
        }
        for row in rows
    ]
    columns = ["provider_model"]
    if include_thinking:
        columns.append("thinking")
    columns.extend(
        [
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
    )
    _print_table(
        payload_rows,
        columns,
        headers,
        rich_output=rich_output,
    )


def _print_unconfigured_model_table(
    rows: list[UnconfiguredModelRow],
    *,
    rich_output: bool,
) -> None:
    headers = {
        "required": "required",
        "provider_model": "provider/model",
        "harness": "harness",
        "thinking": "thinking",
        "msgs": "msgs",
        "input": "input",
        "output": "output",
        "reasoning": "reasoning",
        "cache_r": "cache_r",
        "cache_w": "cache_w",
        "total": "total",
    }
    include_thinking = any(row.thinking_level is not None for row in rows)
    payload_rows = [
        {
            "required": "+".join(row.required),
            "provider_model": f"{row.provider_id}/{row.model_id}",
            "harness": row.harness,
            "thinking": row.thinking_level or "-",
            "msgs": _format_int(row.message_count),
            "input": _format_int(row.tokens.input),
            "output": _format_int(row.tokens.output),
            "reasoning": _format_int(row.tokens.reasoning),
            "cache_r": _format_int(row.tokens.cache_read),
            "cache_w": _format_int(row.tokens.cache_write),
            "total": _format_int(row.total_tokens),
        }
        for row in rows
    ]
    columns = ["required", "provider_model", "harness"]
    if include_thinking:
        columns.append("thinking")
    columns.extend(
        [
            "msgs",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "total",
        ]
    )
    _print_table(payload_rows, columns, headers, rich_output=rich_output)


def _normalize_report_display_filter(
    *,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
    sort: str,
    limit: int | None,
) -> ReportDisplayFilter:
    normalized_price_state = price_state.strip().lower()
    if normalized_price_state not in _VALID_REPORT_PRICE_STATES:
        _exit_with_error("Unsupported --price-state. Use all, priced, or unpriced.")
    normalized_sort = sort.strip().lower()
    if normalized_sort not in _VALID_REPORT_SORTS:
        _exit_with_error(
            "Unsupported --sort. Use actual, virtual, savings, tokens, messages, "
            "provider, model, or unpriced."
        )
    if min_messages is not None and min_messages < 0:
        _exit_with_error("--min-messages must be non-negative.")
    if min_tokens is not None and min_tokens < 0:
        _exit_with_error("--min-tokens must be non-negative.")
    if limit is not None and limit < 0:
        _exit_with_error("--limit must be non-negative.")
    return ReportDisplayFilter(
        price_state=normalized_price_state,
        min_messages=min_messages,
        min_tokens=min_tokens,
        sort=normalized_sort,
        limit=limit,
    )


def _normalize_price_display_filter(
    *,
    table: str,
    provider: str | None,
    model: str | None,
    query: str | None,
    category: str | None,
    release_status: str | None,
    sort: str,
    limit: int | None,
) -> PriceDisplayFilter:
    normalized_table = table.strip().lower()
    if normalized_table not in _VALID_PRICE_TABLES:
        _exit_with_error("Unsupported --table. Use virtual, actual, or all.")
    normalized_sort = sort.strip().lower()
    if normalized_sort not in _VALID_PRICE_SORTS:
        _exit_with_error(
            "Unsupported --sort. Use provider, model, input, cached, cache-write, "
            "output, reasoning, category, or release."
        )
    if limit is not None and limit < 0:
        _exit_with_error("--limit must be non-negative.")
    return PriceDisplayFilter(
        table=normalized_table,
        provider=provider,
        model=model,
        query=query,
        category=category,
        release_status=release_status,
        sort=normalized_sort,
        limit=limit,
    )


def _filter_model_rows(
    rows: list[ModelSummaryRow],
    *,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
    sort: str,
    limit: int | None,
) -> list[ModelSummaryRow]:
    filtered = [
        row
        for row in rows
        if _model_row_matches_price_state(row, price_state)
        and (min_messages is None or row.message_count >= min_messages)
        and (min_tokens is None or row.total_tokens >= min_tokens)
    ]
    sorted_rows = _sort_model_rows(filtered, sort=sort)
    if limit is not None:
        return sorted_rows[:limit]
    return sorted_rows


def _model_row_matches_price_state(row: ModelSummaryRow, price_state: str) -> bool:
    if price_state == "all":
        return True
    if price_state == "priced":
        return row.unpriced_count == 0
    return row.unpriced_count > 0


def _sort_model_rows(
    rows: list[ModelSummaryRow],
    *,
    sort: str,
) -> list[ModelSummaryRow]:
    if sort == "actual":
        return sorted(
            rows,
            key=lambda row: (
                row.actual_cost_usd,
                row.total_tokens,
                row.message_count,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "virtual":
        return sorted(
            rows,
            key=lambda row: (
                row.virtual_cost_usd,
                row.total_tokens,
                row.message_count,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "savings":
        return sorted(
            rows,
            key=lambda row: (
                row.savings_usd,
                row.total_tokens,
                row.message_count,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "tokens":
        return sorted(
            rows,
            key=lambda row: (
                row.total_tokens,
                row.message_count,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "messages":
        return sorted(
            rows,
            key=lambda row: (
                row.message_count,
                row.total_tokens,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "provider":
        return sorted(
            rows,
            key=lambda row: (
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
                -row.total_tokens,
                -row.message_count,
            ),
        )
    if sort == "model":
        return sorted(
            rows,
            key=lambda row: (
                row.model_id,
                row.provider_id,
                row.thinking_level or "",
                -row.total_tokens,
                -row.message_count,
            ),
        )
    return sorted(
        rows,
        key=lambda row: (
            row.unpriced_count == 0,
            -row.total_tokens,
            -row.message_count,
            row.provider_id,
            row.model_id,
            row.thinking_level or "",
        ),
    )


def _filter_unconfigured_models(
    rows: list[UnconfiguredModelRow],
    *,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
) -> list[UnconfiguredModelRow]:
    if price_state == "priced":
        return []
    return [
        row
        for row in rows
        if (min_messages is None or row.message_count >= min_messages)
        and (min_tokens is None or row.total_tokens >= min_tokens)
    ]


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
                    "output_usd_per_1m": price.output_usd_per_1m,
                    "reasoning_usd_per_1m": price.reasoning_usd_per_1m,
                    "effective_reasoning_usd_per_1m": (
                        price.reasoning_usd_per_1m
                        if price.reasoning_usd_per_1m is not None
                        else price.output_usd_per_1m
                    ),
                    "category": price.category,
                    "release_status": price.release_status,
                }
            )
    return rows


def _filter_price_rows(
    rows: list[dict[str, object]],
    filters: PriceDisplayFilter,
) -> list[dict[str, object]]:
    filtered = rows
    if filters.provider is not None:
        normalized_provider = normalize_identity(filters.provider)
        filtered = [
            row
            for row in filtered
            if normalize_identity(str(row["provider"])) == normalized_provider
        ]
    if filters.model is not None:
        normalized_model = normalize_identity(filters.model)
        filtered = [
            row
            for row in filtered
            if normalize_identity(str(row["model"])) == normalized_model
            or normalized_model
            in {normalize_identity(alias) for alias in _aliases_from_row(row)}
        ]
    if filters.category is not None:
        normalized_category = normalize_identity(filters.category)
        filtered = [
            row
            for row in filtered
            if normalize_identity(str(row.get("category") or "")) == normalized_category
        ]
    if filters.release_status is not None:
        normalized_release = filters.release_status.strip().lower()
        filtered = [
            row
            for row in filtered
            if str(row.get("release_status") or "").strip().lower()
            == normalized_release
        ]
    if filters.query is not None:
        needle = filters.query.strip().lower()
        filtered = [
            row
            for row in filtered
            if needle
            in " ".join(
                [
                    str(row["provider"]),
                    str(row["model"]),
                    *_aliases_from_row(row),
                    str(row.get("category") or ""),
                    str(row.get("release_status") or ""),
                ]
            ).lower()
        ]

    sorted_rows = _sort_price_rows(filtered, sort=filters.sort)
    if filters.limit is not None:
        return sorted_rows[: filters.limit]
    return sorted_rows


def _sort_price_rows(
    rows: list[dict[str, object]],
    *,
    sort: str,
) -> list[dict[str, object]]:
    if sort == "provider":
        return sorted(
            rows,
            key=lambda row: (
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
        )
    if sort == "model":
        return sorted(
            rows,
            key=lambda row: (
                str(row["model"]),
                str(row["provider"]),
                str(row["table"]),
            ),
        )
    if sort == "input":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "input_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "cached":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "effective_cached_input_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "cache-write":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "effective_cache_write_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "output":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "output_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "reasoning":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "effective_reasoning_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "category":
        return sorted(
            rows,
            key=lambda row: (
                str(row.get("category") or ""),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("release_status") or ""),
            str(row["provider"]),
            str(row["model"]),
            str(row["table"]),
        ),
    )


def _aliases_from_row(row: dict[str, object]) -> list[str]:
    aliases = row.get("aliases")
    if not isinstance(aliases, list):
        return []
    return [str(alias) for alias in aliases]


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
    columns = ["table", "provider", "model"]
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
    _print_table(payload_rows, columns, headers, rich_output=rich_output)


def _as_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"Expected float-compatible value, got {value!r}"
        raise TypeError(msg)
    return float(value)


def _required_row_float(row: dict[str, object], key: str) -> float:
    value = _as_float_or_none(row.get(key))
    if value is None:
        msg = f"Missing numeric price field: {key}"
        raise TypeError(msg)
    return value


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


def _print_import_result(
    result: ImportExecutionResult,
) -> None:
    typer.echo(f"Imported {result.harness} usage:")
    typer.echo(f"  source path: {result.source_path}")
    typer.echo(f"  tracking session: {result.tracking_session_id}")
    typer.echo(f"  rows seen: {result.rows_seen}")
    typer.echo(f"  rows imported: {result.rows_imported}")
    typer.echo(f"  rows skipped: {result.rows_skipped}")


def _print_configured_import_results(results: tuple[ImportUsageResult, ...]) -> None:
    typer.echo("Imported usage")
    rows = []
    for result in results:
        rows.append(
            {
                "harness": result.harness,
                "source": (
                    str(result.source_path)
                    if result.source_path is not None
                    else "(none)"
                ),
                "inserted": _format_int(result.rows_imported),
                "linked": _format_int(result.rows_linked),
                "skipped": _format_int(result.rows_skipped),
                "status": result.status,
            }
        )
    _print_table(
        rows,
        ["harness", "source", "inserted", "linked", "skipped", "status"],
        {
            "harness": "harness",
            "source": "source",
            "inserted": "inserted",
            "linked": "linked",
            "skipped": "skipped",
            "status": "status",
        },
        rich_output=False,
    )


def _resolve_config_path(ctx: typer.Context) -> Path:
    root_obj = ctx.find_root().obj or {}
    config_path = root_obj.get("config_path")
    if config_path is not None and not isinstance(config_path, Path):
        msg = "Unexpected CLI state for --config."
        raise TypeError(msg)
    return resolve_toktrail_config_path(config_path)


def _load_resolved_costing_config_or_exit(ctx: typer.Context) -> LoadedCostingConfig:
    try:
        return load_resolved_costing_config(_resolve_config_path(ctx))
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
    return conn


def _exit_with_error(message: str) -> NoReturn:
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_cost(value: float) -> str:
    return f"${value:.2f}"


def _format_price(value: float | None, *, fallback: str | None = None) -> str:
    if value is None:
        return f"={fallback}" if fallback is not None else "-"
    return f"${value:.2f}"
