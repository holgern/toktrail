from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import typer

from toktrail.api.imports import import_configured_usage as import_configured_usage_api
from toktrail.cli_parts.types import WatchDelta, WatchTotals
from toktrail.config import CostingConfig
from toktrail.db import get_active_tracking_session, get_tracking_session, summarize_usage
from toktrail.models import TokenBreakdown
from toktrail.reporting import CostTotals, RunReport
from toktrail.reporting import UsageReportFilter


@dataclass(frozen=True)
class WatchRuntime:
    resolve_state_db: Callable[[typer.Context], Path]
    resolve_config_path: Callable[[typer.Context], Path]
    open_toktrail_connection: Callable[[typer.Context], object]
    load_costing_config_or_exit: Callable[[typer.Context], CostingConfig]
    load_resolved_toktrail_config_or_exit: Callable[[typer.Context], object]
    exit_with_error: Callable[[str], None]
    format_int: Callable[[int], str]
    format_signed_int: Callable[[int], str]
    format_token_delta: Callable[[TokenBreakdown], str]
    format_cost: Callable[[object], str]


_RUNTIME: WatchRuntime | None = None


def configure_watch_runtime(runtime: WatchRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def _runtime() -> WatchRuntime:
    if _RUNTIME is None:
        msg = "watch runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME


def _resolve_state_db(ctx: typer.Context) -> Path:
    return _runtime().resolve_state_db(ctx)


def _resolve_config_path(ctx: typer.Context) -> Path:
    return _runtime().resolve_config_path(ctx)


def _open_toktrail_connection(ctx: typer.Context):
    return _runtime().open_toktrail_connection(ctx)


def _load_costing_config_or_exit(ctx: typer.Context) -> CostingConfig:
    return _runtime().load_costing_config_or_exit(ctx)


def _load_resolved_toktrail_config_or_exit(ctx: typer.Context) -> object:
    return _runtime().load_resolved_toktrail_config_or_exit(ctx)


def _exit_with_error(message: str) -> None:
    _runtime().exit_with_error(message)


def _format_int(value: int) -> str:
    return _runtime().format_int(value)


def _format_signed_int(value: int) -> str:
    return _runtime().format_signed_int(value)


def _format_token_delta(tokens: TokenBreakdown) -> str:
    return _runtime().format_token_delta(tokens)


def _format_cost(value: object) -> str:
    return _runtime().format_cost(value)


def watch_impl(
    ctx: typer.Context,
    *,
    run_id: int | None,
    harnesses: list[str] | None,
    interval: float,
    raw: bool | None,
    json_output: bool,
) -> None:
    harness_list: list[str] | None = harnesses
    watch_configured(
        ctx,
        tracking_session_id=run_id,
        harnesses=harness_list,
        interval=interval,
        include_raw_json=raw,
        json_output=json_output,
    )


def resolve_watch_session_id(
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


def watch_report(
    ctx: typer.Context,
    *,
    session_id: int,
    costing_config: CostingConfig,
) -> RunReport:
    conn = _open_toktrail_connection(ctx)
    try:
        return summarize_usage(
            conn,
            UsageReportFilter(tracking_session_id=session_id),
            costing_config=costing_config,
        )
    finally:
        conn.close()


def message_count(report: RunReport) -> int:
    return sum(row.message_count for row in report.by_harness)


def watch_totals_from_report(report: RunReport) -> WatchTotals:
    return WatchTotals(
        message_count=message_count(report),
        tokens=report.totals.tokens,
        costs=report.totals.costs,
    )


def subtract_tokens(after: TokenBreakdown, before: TokenBreakdown) -> TokenBreakdown:
    return TokenBreakdown(
        input=after.input - before.input,
        output=after.output - before.output,
        reasoning=after.reasoning - before.reasoning,
        cache_read=after.cache_read - before.cache_read,
        cache_write=after.cache_write - before.cache_write,
        cache_output=after.cache_output - before.cache_output,
    )


def subtract_costs(after: CostTotals, before: CostTotals) -> CostTotals:
    return CostTotals(
        source_cost_usd=after.source_cost_usd - before.source_cost_usd,
        actual_cost_usd=after.actual_cost_usd - before.actual_cost_usd,
        virtual_cost_usd=after.virtual_cost_usd - before.virtual_cost_usd,
        unpriced_count=after.unpriced_count - before.unpriced_count,
    )


def subtract_totals(after: WatchTotals, before: WatchTotals) -> WatchTotals:
    return WatchTotals(
        message_count=after.message_count - before.message_count,
        tokens=subtract_tokens(after.tokens, before.tokens),
        costs=subtract_costs(after.costs, before.costs),
    )


def watch_delta_has_activity(delta: WatchDelta) -> bool:
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


def by_harness_totals(report: RunReport) -> dict[str, WatchTotals]:
    return {
        row.harness: WatchTotals(
            message_count=row.message_count,
            tokens=row.tokens,
            costs=row.costs,
        )
        for row in report.by_harness
    }


def watch_delta(previous: RunReport, current: RunReport) -> WatchDelta:
    before_totals = watch_totals_from_report(previous)
    after_totals = watch_totals_from_report(current)
    before_by_harness = by_harness_totals(previous)
    after_by_harness = by_harness_totals(current)

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
        delta = subtract_totals(after_h, before_h)
        if watch_delta_has_activity(WatchDelta(totals=delta, by_harness={})):
            by_harness_delta[harness_name] = delta

    return WatchDelta(
        totals=subtract_totals(after_totals, before_totals),
        by_harness=by_harness_delta,
    )


def print_watch_start(
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


def print_watch_delta(
    delta: WatchDelta,
    current_report: RunReport,
) -> None:
    _ = current_report
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


def print_watch_delta_json(
    session_id: int,
    delta: WatchDelta,
    current_report: RunReport,
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
            "message_count": message_count(current_report),
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


def print_watch_stop(observed: WatchDelta) -> None:
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


def watch_configured(
    ctx: typer.Context,
    *,
    tracking_session_id: int | None,
    harnesses: list[str] | None,
    interval: float,
    include_raw_json: bool | None,
    json_output: bool,
) -> None:
    selected_session_id = resolve_watch_session_id(ctx, tracking_session_id)
    costing_config = _load_costing_config_or_exit(ctx)

    previous_report = watch_report(
        ctx,
        session_id=selected_session_id,
        costing_config=costing_config,
    )
    baseline_report = previous_report

    if not json_output:
        print_watch_start(ctx, selected_session_id, harnesses)

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
            current_report = watch_report(
                ctx,
                session_id=selected_session_id,
                costing_config=costing_config,
            )
            delta = watch_delta(previous_report, current_report)
            if watch_delta_has_activity(delta):
                if json_output:
                    print_watch_delta_json(selected_session_id, delta, current_report)
                else:
                    print_watch_delta(delta, current_report)
            previous_report = current_report
            time.sleep(interval)
    except KeyboardInterrupt:
        final_report = watch_report(
            ctx,
            session_id=selected_session_id,
            costing_config=costing_config,
        )
        observed = watch_delta(baseline_report, final_report)
        if not json_output:
            typer.echo("")
            print_watch_stop(observed)
