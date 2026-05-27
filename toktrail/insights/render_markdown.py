"""Markdown renderer for insights reports.

Produces deterministic, stable Markdown output with no raw JSON,
no HTML, and no transcript content.  Designed for snapshot testing.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from toktrail.insights.models import (
    DeterministicSuggestion,
    InsightAggregate,
    InsightAnomaly,
    InsightDelta,
    InsightGroupRow,
    InsightSessionMeta,
    InsightsReport,
)


def _format_token_count(value: int) -> str:
    """Format large token counts with K/M suffixes."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _format_cost(value: Decimal | int | float) -> str:
    """Format cost as USD string."""
    if isinstance(value, Decimal):
        return f"${value:.2f}"
    return f"${float(value):.2f}"


def _format_percent(value: float | None) -> str:
    """Format a ratio as a percentage string."""
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


def _format_ms_as_date(ms: int | None) -> str:
    """Format epoch milliseconds as an ISO date string."""
    if ms is None:
        return "—"
    dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _render_at_a_glance(agg: InsightAggregate) -> str:
    """Render the at-a-glance section."""
    lines = [
        "## At a glance",
        "",
        f"- Sessions: {agg.session_count}",
        f"- Messages: {agg.message_count}",
        f"- Total tokens: {_format_token_count(agg.total_tokens)}",
        f"- Input tokens: {_format_token_count(agg.input_tokens)}",
        f"- Output tokens: {_format_token_count(agg.output_tokens)}",
        f"- Actual cost: {_format_cost(agg.actual_cost)}",
        f"- Virtual cost: {_format_cost(agg.virtual_cost)}",
    ]
    if agg.unpriced_count > 0:
        lines.append(f"- Unpriced models: {agg.unpriced_count}")
    if agg.tool_failure_count > 0:
        lines.append(f"- Tool failures: {agg.tool_failure_count}")
    if agg.input_tokens > 0:
        lines.append(f"- Cache read ratio: {_format_percent(agg.cache_read_ratio)}")
    lines.append("")
    return "\n".join(lines)


def _render_deltas(deltas: tuple[InsightDelta, ...]) -> str:
    """Render the what-changed section."""
    if not deltas:
        return ""

    lines = [
        "## What changed",
        "",
        "| Metric | Current | Previous | Change |",
        "|---|---:|---:|---:|",
    ]
    for delta in deltas:
        current_str = (
            _format_cost(delta.current)
            if "cost" in delta.metric
            else str(delta.current)
        )
        previous_str = (
            _format_cost(delta.previous)
            if "cost" in delta.metric and delta.previous is not None
            else (str(delta.previous) if delta.previous is not None else "—")
        )
        lines.append(
            f"| {delta.metric} | {current_str} | {previous_str} | {delta.change} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_group_table(
    title: str,
    rows: tuple[InsightGroupRow, ...],
    show_tokens: bool = True,
    show_cost: bool = True,
) -> str:
    """Render a table of grouped metrics."""
    if not rows:
        return ""

    lines = [f"## {title}", ""]
    if show_tokens and show_cost:
        lines.extend(
            [
                "| Name | Sessions | Tokens | Actual cost | Virtual cost | Failures |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                f"| {row.label} | {row.session_count} "
                f"| {_format_token_count(row.total_tokens)} "
                f"| {_format_cost(row.actual_cost)} "
                f"| {_format_cost(row.virtual_cost)} "
                f"| {row.tool_failure_count} |"
            )
    elif show_cost:
        lines.extend(
            [
                "| Name | Sessions | Actual cost | Virtual cost |",
                "|---|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                f"| {row.label} | {row.session_count} "
                f"| {_format_cost(row.actual_cost)} "
                f"| {_format_cost(row.virtual_cost)} |"
            )
    else:
        lines.extend(
            [
                "| Name | Sessions | Tokens | Failures |",
                "|---|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                f"| {row.label} | {row.session_count} "
                f"| {_format_token_count(row.total_tokens)} "
                f"| {row.tool_failure_count} |"
            )
    lines.append("")
    return "\n".join(lines)


def _render_anomalies(anomalies: tuple[InsightAnomaly, ...]) -> str:
    """Render anomalies section."""
    if not anomalies:
        return ""

    lines = ["## Anomalies", ""]
    for a in anomalies:
        severity_icon = {"low": "ℹ️", "medium": "⚠️", "high": "🔴"}.get(a.severity, "•")
        lines.append(f"- {severity_icon} **[{a.kind}]** {a.message}")
    lines.append("")
    return "\n".join(lines)


def _render_suggestions(suggestions: tuple[DeterministicSuggestion, ...]) -> str:
    """Render deterministic suggestions section.

    Accepts raw suggestion dicts (before DeterministicSuggestion
    construction) since service.py may pass either form.
    """
    if not suggestions:
        return ""

    lines = ["## Deterministic suggestions", ""]
    for i, s in enumerate(suggestions, 1):
        severity = s.severity
        icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}.get(severity, "•")
        title = s.title
        detail = s.detail
        command = s.command
        lines.append(f"{i}. {icon} **{title}**")
        lines.append(f"   {detail}")
        if command:
            lines.append(f"   Command: `{command}`")
    lines.append("")
    return "\n".join(lines)


def _render_sessions_to_inspect(
    sessions: tuple[InsightSessionMeta, ...],
) -> str:
    """Render sessions-to-inspect section."""
    if not sessions:
        return ""

    lines = [
        "## Sessions to inspect",
        "",
        "| Harness | Session | Area | Tokens | Virtual cost | Failures |",
        "|---|---|---|---:|---:|---:|",
    ]
    for s in sessions[:20]:
        area = s.area_path or "—"
        lines.append(
            f"| {s.harness} | `{s.source_session_id[:24]}` "
            f"| {area} "
            f"| {_format_token_count(s.total_tokens)} "
            f"| {_format_cost(s.virtual_cost)} "
            f"| {s.tool_failure_count} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_insights_markdown(report: InsightsReport) -> str:
    """Render a full insights report as deterministic Markdown.

    Produces a stable, readable Markdown artifact suitable for
    snapshot testing and terminal viewing.  No HTML, no raw JSON,
    no transcript content.
    """
    sections: list[str] = []

    # Header
    header_lines = [
        "# toktrail insights",
        "",
        (
            "Generated: "
            + datetime.datetime.now(tz=datetime.timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
        ),
        f"Period: {report.period_label}",
    ]
    if report.filters:
        filter_parts = [f"{k}={v}" for k, v in report.filters.items() if v is not None]
        if filter_parts:
            header_lines.append(f"Filters: {', '.join(filter_parts)}")
    header_lines.append("")
    sections.append("\n".join(header_lines))

    # At a glance
    sections.append(_render_at_a_glance(report.current))

    # What changed
    if report.deltas:
        sections.append(_render_deltas(report.deltas))

    # Usage by area
    if report.current.by_area:
        sections.append(_render_group_table("Usage by area", report.current.by_area))

    # Usage by machine
    if report.current.by_machine:
        sections.append(
            _render_group_table("Usage by machine", report.current.by_machine)
        )

    # Usage by harness and model
    if report.current.by_harness:
        sections.append(
            _render_group_table("Usage by harness", report.current.by_harness)
        )
    if report.current.by_model:
        sections.append(_render_group_table("Usage by model", report.current.by_model))

    # Model spend and cache
    if report.current.by_model:
        lines = [
            "## Model spend and cache",
            "",
            "| Model | Input | Output | Cache read | Virtual cost |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in report.current.by_model:
            lines.append(
                f"| {row.label} "
                f"| {_format_token_count(row.input_tokens)} "
                f"| {_format_token_count(row.output_tokens)} "
                f"| {_format_token_count(row.cache_read_tokens)} "
                f"| {_format_cost(row.virtual_cost)} |"
            )
        lines.append("")
        sections.append("\n".join(lines))

    # Tool failures
    if report.current.tool_failure_count > 0:
        lines = [
            "## Tool failures",
            "",
            f"Total tool failures: {report.current.tool_failure_count}",
            "",
        ]
        sections.append("\n".join(lines))

    # Anomalies
    if report.anomalies:
        sections.append(_render_anomalies(report.anomalies))

    # Deterministic suggestions
    if report.suggestions:
        sections.append(_render_suggestions(report.suggestions))

    # Sessions to inspect
    if report.sessions_to_inspect:
        sections.append(_render_sessions_to_inspect(report.sessions_to_inspect))

    # Appendix
    sections.append(
        "## Appendix: assumptions\n"
        "\n"
        "- Generated from local toktrail state.\n"
        "- No raw transcript included.\n"
        "- No LLM or network calls used.\n"
    )

    return "\n".join(sections)
