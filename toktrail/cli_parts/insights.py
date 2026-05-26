"""CLI commands for toktrail insights.

Provides the ``toktrail insights report`` command with period
filters, --json output, --format md, and --out file support.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from toktrail.insights.models import InsightsReport

insights_app = typer.Typer(
    name="insights",
    help="Usage insights: anomalies, deltas, and suggestions.",
    no_args_is_help=True,
)


@dataclass(frozen=True)
class InsightsRuntime:
    resolve_state_db: Callable[[typer.Context], Path]
    resolve_config_path: Callable[[typer.Context], Path]
    exit_with_error: Callable[[str], None]


_RUNTIME: InsightsRuntime | None = None


def configure_insights_runtime(runtime: InsightsRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def _resolve_state_db(ctx: typer.Context) -> Path:
    if _RUNTIME is None:
        msg = "insights runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME.resolve_state_db(ctx)


def _resolve_config_path(ctx: typer.Context) -> Path:
    if _RUNTIME is None:
        msg = "insights runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME.resolve_config_path(ctx)


def _exit_with_error(message: str) -> None:
    if _RUNTIME is None:
        msg = "insights runtime is not configured"
        raise RuntimeError(msg)
    _RUNTIME.exit_with_error(message)


@insights_app.command("report")
def insights_report(
    ctx: typer.Context,
    period: Annotated[
        str | None,
        typer.Option(
            "--period",
            help=(
                "Named period: today, yesterday, "
                "this-week, last-week, this-month, last-month."
            ),
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Start of period (YYYYMMDD or ISO date).",
        ),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option(
            "--until",
            help="End of period (YYYYMMDD or ISO date).",
        ),
    ] = None,
    area: Annotated[
        str | None,
        typer.Option("--area", help="Filter by area path."),
    ] = None,
    area_leaf: Annotated[
        str | None,
        typer.Option("--area-leaf", help="Filter by area leaf name."),
    ] = None,
    machine: Annotated[
        str | None,
        typer.Option("--machine", help="Filter by machine ID or name."),
    ] = None,
    harness: Annotated[
        list[str] | None,
        typer.Option("--harness", help="Filter by harness."),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Filter by provider."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Filter by model."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON."),
    ] = False,
    format_md: Annotated[
        str | None,
        typer.Option(
            "--format",
            help="Output format: terminal (default), md (Markdown).",
        ),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write output to file."),
    ] = None,
    timezone: Annotated[
        str | None,
        typer.Option("--timezone", help="Timezone for period boundaries."),
    ] = None,
    utc: Annotated[
        bool,
        typer.Option("--utc", help="Use UTC timezone."),
    ] = False,
    no_refresh: Annotated[
        bool,
        typer.Option(
            "--no-refresh",
            help="Use existing state only, do not re-import.",
        ),
    ] = False,
) -> None:
    """Generate a usage insights report."""
    from toktrail.api.insights import insights_report as insights_api
    from toktrail.insights.render_markdown import render_insights_markdown

    db_path = _resolve_state_db(ctx)
    config_path = _resolve_config_path(ctx)

    harnesses = tuple(harness) if harness else ()
    refresh = not no_refresh

    # Resolve since/until from text if provided
    since_ms: int | None = None
    until_ms: int | None = None
    if since or until:
        from toktrail.periods import resolve_time_range

        resolved = resolve_time_range(
            timezone_name=timezone,
            utc=utc,
            since_text=since,
            until_text=until,
        )
        since_ms = resolved.since_ms
        until_ms = resolved.until_ms

    try:
        report = insights_api(
            db_path=db_path,
            config_path=config_path,
            period=period,
            timezone_name=timezone,
            utc=utc,
            since_ms=since_ms,
            until_ms=until_ms,
            area=area,
            area_leaf=area_leaf,
            machine_id=machine,
            harnesses=harnesses,
            provider_id=provider,
            model_id=model,
            refresh=refresh,
        )
    except Exception as exc:
        _exit_with_error(str(exc))
        return

    if json_output:
        payload = report.as_dict()
        output = json.dumps(payload, indent=2, default=str)
        if out:
            out.write_text(output)
            typer.echo(f"Wrote JSON report to {out}")
        else:
            typer.echo(output)
        return

    if format_md == "md":
        markdown = render_insights_markdown(report)
        if out:
            out.write_text(markdown)
            typer.echo(f"Wrote Markdown report to {out}")
        else:
            typer.echo(markdown)
        return

    # Default: terminal summary
    _print_terminal_summary(report)


def _print_terminal_summary(report: InsightsReport) -> None:
    """Print a human-readable terminal summary."""
    cur = report.current
    typer.echo(f"toktrail insights — {report.period_label}")
    typer.echo("")

    typer.echo("At a glance:")
    typer.echo(f"  Sessions:    {cur.session_count}")
    typer.echo(f"  Messages:    {cur.message_count}")
    typer.echo(f"  Total tokens: {cur.total_tokens:,}")
    typer.echo(f"  Input:       {cur.input_tokens:,}")
    typer.echo(f"  Output:      {cur.output_tokens:,}")
    typer.echo(f"  Actual cost: ${cur.actual_cost:.2f}")
    typer.echo(f"  Virtual cost: ${cur.virtual_cost:.2f}")
    if cur.unpriced_count > 0:
        typer.echo(f"  Unpriced:    {cur.unpriced_count}")
    if cur.tool_failure_count > 0:
        typer.echo(f"  Failures:    {cur.tool_failure_count}")
    typer.echo("")

    if report.deltas:
        typer.echo("What changed:")
        for delta in report.deltas:
            direction_icon = {
                "up": "↑",
                "down": "↓",
                "flat": "→",
                "new": "+",
                "removed": "✕",
            }.get(delta.direction, "·")
            delta_val = (
                f"${float(delta.current):.2f}"
                if "cost" in delta.metric
                else str(delta.current)
            )
            typer.echo(
                f"  {direction_icon} {delta.metric}: {delta_val} ({delta.change})"
            )
        typer.echo("")

    if report.anomalies:
        typer.echo("Anomalies:")
        for anomaly in report.anomalies:
            icon = {"low": "ℹ", "medium": "⚠", "high": "✖"}.get(anomaly.severity, "•")
            typer.echo(f"  {icon} [{anomaly.kind}] {anomaly.message}")
        typer.echo("")

    if report.suggestions:
        typer.echo("Suggestions:")
        for suggestion in report.suggestions:
            icon = {"info": "ℹ", "warning": "⚠", "critical": "✖"}.get(
                suggestion.severity, "•"
            )
            cmd_str = f"  Command: {suggestion.command}" if suggestion.command else ""
            typer.echo(f"  {icon} {suggestion.title}: {suggestion.detail}{cmd_str}")
        typer.echo("")

    if report.sessions_to_inspect:
        typer.echo("Sessions to inspect:")
        for s in report.sessions_to_inspect[:10]:
            area = s.area_path or "—"
            typer.echo(
                f"  {s.harness}/{s.source_session_id[:24]} "
                f"area={area} "
                f"tokens={s.total_tokens:,} "
                f"cost=${s.virtual_cost:.2f} "
                f"failures={s.tool_failure_count}"
            )
