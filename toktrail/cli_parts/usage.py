from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, NoReturn

import typer

if TYPE_CHECKING:
    from toktrail.config import CostingConfig, LoadedToktrailConfig
    from toktrail.reporting import UsageSeriesBucket

from toktrail.cli_parts.filters import (
    _filter_model_rows,
    _filter_series_buckets,
    _filter_unconfigured_models,
    _normalize_report_display_filter,
    _sort_series_buckets,
)
from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.cli_parts.table import (
    _print_model_table,
    _print_table,
    _print_unconfigured_model_table,
)
from toktrail.cli_parts.types import ImportExecutionResult
from toktrail.db import (
    summarize_usage,
    summarize_usage_areas,
    summarize_usage_runs,
    summarize_usage_sessions,
)
from toktrail.models import TokenBreakdown
from toktrail.periods import resolve_time_range
from toktrail.reporting import (
    CostTotals,
    ModelSummaryRow,
    ProviderSummaryRow,
    UnconfiguredModelRow,
    UsageReportFilter,
)
from toktrail.reporting import RunReport as InternalRunReport

JsonOption = bool
HarnessOption = str | None
SourceSessionOption = str | None
MachineOption = str | None
ProviderOption = str | None
ModelOption = str | None
ThinkingOption = str | None
AgentOption = str | None
AreaOption = str | None
AreaLeafOption = str | None
AreaExactOption = bool
UnassignedAreaOption = bool
TimeBoundaryOption = str | None
UntilBoundaryOption = str | None
UsagePeriodOption = str | None
SessionTodayOption = bool
SessionYesterdayOption = bool
SessionThisWeekOption = bool
SessionLastWeekOption = bool
SessionThisMonthOption = bool
SessionLastMonthOption = bool
TimezoneOption = str | None
UtcOption = bool
RichOption = bool
SplitThinkingOption = bool
PriceStateOption = str
MinMessagesOption = int | None
MinTokensOption = int | None
ReportSortOption = str
ReportLimitOption = int | None
BreakdownOption = bool
SessionTableOption = bool
RefreshOption = bool
RefreshDetailsOption = bool
RawModeOption = bool | None


@dataclass(frozen=True)
class UsageRuntime:
    refresh_before_report: Callable[..., list[ImportExecutionResult]]
    wrap_refresh_json_payload: Callable[..., dict[str, object]]
    load_costing_config_or_exit: Callable[[typer.Context], CostingConfig]
    open_toktrail_connection: Callable[[typer.Context], sqlite3.Connection]
    resolve_machine_id_or_exit: Callable[[sqlite3.Connection, str | None], str | None]
    load_resolved_toktrail_config_or_exit: Callable[
        [typer.Context], LoadedToktrailConfig
    ]
    exit_with_error: Callable[[str], NoReturn]


_RUNTIME: UsageRuntime | None = None


def configure_usage_runtime(runtime: UsageRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def _runtime() -> UsageRuntime:
    if _RUNTIME is None:
        msg = "usage runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME


def _refresh_before_report(
    *args: object, **kwargs: object
) -> list[ImportExecutionResult]:
    return _runtime().refresh_before_report(*args, **kwargs)


def _wrap_refresh_json_payload(
    payload: dict[str, object],
    *,
    refresh_results: list[ImportExecutionResult],
    include_refresh: bool,
) -> dict[str, object]:
    return _runtime().wrap_refresh_json_payload(
        payload,
        refresh_results=refresh_results,
        include_refresh=include_refresh,
    )


def _load_costing_config_or_exit(ctx: typer.Context) -> CostingConfig:
    return _runtime().load_costing_config_or_exit(ctx)


def _open_toktrail_connection(ctx: typer.Context) -> sqlite3.Connection:
    return _runtime().open_toktrail_connection(ctx)


def _resolve_machine_id_or_exit(
    conn: sqlite3.Connection,
    machine_selector: str | None,
) -> str | None:
    return _runtime().resolve_machine_id_or_exit(conn, machine_selector)


def _load_resolved_toktrail_config_or_exit(ctx: typer.Context) -> LoadedToktrailConfig:
    return _runtime().load_resolved_toktrail_config_or_exit(ctx)


def _exit_with_error(message: str) -> NoReturn:
    _runtime().exit_with_error(message)


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


def _resolve_area_filter_inputs_or_exit(
    *,
    area: str | None,
    area_leaf: str | None,
    unassigned_area: bool,
) -> tuple[str | None, str]:
    if (
        sum(
            bool(value)
            for value in (area is not None, area_leaf is not None, unassigned_area)
        )
        > 1
    ):
        _exit_with_error("Use only one of --area, --area-leaf, or --unassigned-area.")
    if area_leaf is not None:
        return area_leaf, "leaf"
    return area, "auto"


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
    area_match: str,
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
                area_match=area_match,
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
    except ValueError as exc:
        _exit_with_error(str(exc))
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
    area_filter_summary = _format_area_filter_summary(report.filters)
    if area_filter_summary is not None:
        typer.echo(area_filter_summary)
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
    area_match: str,
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
                area_match=area_match,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=resolved_range.since_ms,
                until_ms=resolved_range.until_ms,
                split_thinking=split_thinking,
            ),
            costing_config=costing_config,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
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
    area_match: str,
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
    with_summary: bool,
) -> dict[str, object] | None:
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
                area_match=area_match,
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
        digest_lookup = (
            _session_digest_lookup(conn, report.sessions) if with_summary else {}
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
    finally:
        conn.close()

    if json_output:
        payload = report.as_dict()
        if with_summary:
            _add_digest_summaries_to_payload(payload, digest_lookup)
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
        digest_lookup=digest_lookup,
    )
    return None


def _session_digest_lookup(conn, sessions) -> dict[tuple[str, str, str], object]:
    from toktrail.db import list_source_session_digests

    keys = {
        (session.origin_machine_id, session.harness, session.source_session_id)
        for session in sessions
        if session.origin_machine_id is not None
    }
    if not keys:
        return {}
    rows = list_source_session_digests(conn)
    return {
        (row.origin_machine_id, row.harness, row.source_session_id): row
        for row in rows
        if row.origin_machine_id is not None
        and (row.origin_machine_id, row.harness, row.source_session_id) in keys
    }


def _lookup_session_digest(digest_lookup, session):
    if digest_lookup is None or session.origin_machine_id is None:
        return None
    return digest_lookup.get(
        (session.origin_machine_id, session.harness, session.source_session_id)
    )


def _digest_one_line(digest_lookup, session) -> str:
    digest = _lookup_session_digest(digest_lookup, session)
    if digest is None:
        return "-"
    return digest.summary.one_line or "-"


def _digest_tool_failures(digest_lookup, session) -> str:
    digest = _lookup_session_digest(digest_lookup, session)
    if digest is None:
        return "-"
    return _format_int(digest.tool_health.tool_failure_count)


def _add_digest_summaries_to_payload(
    payload: dict[str, object],
    digest_lookup,
) -> None:
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        return
    for row in sessions:
        if not isinstance(row, dict):
            continue
        origin = row.get("origin_machine_id")
        harness = row.get("harness")
        source_session_id = row.get("source_session_id")
        if not all(
            isinstance(value, str) for value in (origin, harness, source_session_id)
        ):
            continue
        digest = digest_lookup.get((origin, harness, source_session_id))
        if digest is None:
            continue
        row["summary"] = {
            "one_line": digest.summary.one_line,
            "confidence": digest.summary.confidence,
            "generator": digest.summary.generator,
            "tool_failure_count": digest.tool_health.tool_failure_count,
        }


def _print_usage_sessions(
    report: object,
    *,
    compact: bool,
    breakdown: bool,
    utc: bool,
    rich_output: bool,
    table: bool,
    period: str | None,
    digest_lookup: dict[tuple[str, str, str], object] | None = None,
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
            digest = _lookup_session_digest(digest_lookup, session)
            if digest is not None:
                typer.echo(
                    "   Summary: "
                    f"{digest.summary.one_line or '-'} "
                    f"(tool_failures={_format_int(digest.tool_health.tool_failure_count)})"
                )
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
        columns = [
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
        ]
        labels = {
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
        }
        numeric_columns = {"msgs", "total", "actual", "virtual", "savings"}
        wrap_columns = {"area", "models"}
        max_widths = {"area": 36, "models": 48, "session": 48}
        if digest_lookup is not None:
            columns.extend(["summary", "tool_failures"])
            labels.update({"summary": "summary", "tool_failures": "tool_failures"})
            numeric_columns.add("tool_failures")
            wrap_columns.add("summary")
            max_widths["summary"] = 48
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
                "summary": _digest_one_line(digest_lookup, session),
                "tool_failures": _digest_tool_failures(digest_lookup, session),
            }
            for session in report.sessions
        ]
        _print_table(
            rows,
            columns,
            labels,
            rich_output=rich_output,
            numeric_columns=numeric_columns,
            wrap_columns=wrap_columns,
            max_widths=max_widths,
        )
    else:
        columns = [
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
        ]
        labels = {
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
        }
        numeric_columns = {
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
        }
        wrap_columns = {"session", "area", "models"}
        max_widths = {"session": 48, "area": 36, "models": 48}
        if digest_lookup is not None:
            columns.extend(["summary", "tool_failures"])
            labels.update({"summary": "summary", "tool_failures": "tool_failures"})
            numeric_columns.add("tool_failures")
            wrap_columns.add("summary")
            max_widths["summary"] = 48
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
                "summary": _digest_one_line(digest_lookup, session),
                "tool_failures": _digest_tool_failures(digest_lookup, session),
            }
            for session in report.sessions
        ]
        _print_table(
            rows,
            columns,
            labels,
            rich_output=rich_output,
            numeric_columns=numeric_columns,
            wrap_columns=wrap_columns,
            max_widths=max_widths,
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
    area_match: str,
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
                area_match=area_match,
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
    except ValueError as exc:
        _exit_with_error(str(exc))
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
    area_match: str,
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
                area_match=area_match,
                area_exact=area_exact,
                unassigned_area=unassigned_area,
                since_ms=resolved_range.since_ms,
                until_ms=resolved_range.until_ms,
                split_thinking=split_thinking,
            ),
            costing_config=costing_config,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
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

    def _matches() -> tuple[str, ...]:
        raw_matches = _value("area_matches")
        if isinstance(raw_matches, (list, tuple)):
            return tuple(str(item) for item in raw_matches)
        return ()

    def _preview(matches: tuple[str, ...]) -> str:
        shown = ", ".join(matches[:3])
        if len(matches) > 3:
            shown = f"{shown}, ..."
        return shown

    if bool(_value("unassigned_area")):
        return "Area filter: unassigned only"
    area_value = _value("area")
    if not isinstance(area_value, str) or not area_value:
        return None
    area_match = _value("area_match")
    matches = _matches()
    if area_match == "leaf":
        if matches:
            return (
                f"Area filter: leaf {area_value} "
                f"({len(matches)} matches: {_preview(matches)})"
            )
        return f"Area filter: leaf {area_value}"
    display_area = matches[0] if len(matches) == 1 else area_value
    detail: str | None = None
    if area_match == "unique_suffix" and display_area != area_value:
        detail = f"resolved from selector {area_value}"
    elif area_match == "sync_id" and display_area != area_value:
        detail = f"resolved from sync id {area_value}"
    if bool(_value("area_exact")):
        if detail is not None:
            return f"Area filter: {display_area} ({detail}; exact only)"
        return f"Area filter: {display_area} (exact only)"
    if detail is not None:
        return f"Area filter: {display_area} ({detail}; including descendants)"
    return f"Area filter: {display_area} (including descendants)"


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
    area_match: str,
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
            area_match=area_match,
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
    except ValueError as exc:
        _exit_with_error(str(exc))
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
