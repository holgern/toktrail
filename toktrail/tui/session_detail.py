from __future__ import annotations

from toktrail.api.models import SessionDigest
from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.formatting import format_epoch_ms_compact


def _health_summary(digest: SessionDigest) -> str:
    if digest.health is None:
        return "- unknown (low)"
    score = "-" if digest.health.score is None else _format_int(digest.health.score)
    grade = digest.health.grade or "-"
    return (
        f"{grade} {score} {digest.health.outcome} "
        f"({digest.health.outcome_confidence})"
    )


def render_session_health_text(
    session_key: str,
    digest: SessionDigest,
    *,
    utc: bool = False,
    rich_output: bool = True,
) -> str:
    lines: list[str] = []
    lines.append(f"Key: {session_key}")
    lines.append(f"{digest.harness} source session {digest.source_session_id}")
    if digest.area_path:
        lines.append(f"Area:       {digest.area_path}")
    if digest.machine_label:
        lines.append(f"Machine:    {digest.machine_label}")
    if digest.cwd or digest.source_dir:
        lines.append(f"Where:      {digest.cwd or digest.source_dir}")
    if digest.git_remote:
        lines.append(f"Git:        {digest.git_remote}")
    if digest.models:
        lines.append(f"Models:     {', '.join(digest.models)}")
    if digest.providers:
        lines.append(f"Providers:  {', '.join(digest.providers)}")
    if digest.started_ms is not None and digest.last_seen_ms is not None:
        lines.append(
            "When:       "
            f"{format_epoch_ms_compact(digest.started_ms, utc=utc)}.."
            f"{format_epoch_ms_compact(digest.last_seen_ms, utc=utc)}"
        )
    lines.append(
        "Usage:      "
        f"messages={_format_int(digest.message_count)} "
        f"tokens={_format_int(digest.usage.tokens.total)} "
        f"actual={_format_cost(digest.usage.costs.actual_cost_usd)} "
        f"virtual={_format_cost(digest.usage.costs.virtual_cost_usd)}"
    )

    lines.extend(["", "Summary"])
    lines.append(f"  {digest.summary.one_line or 'No summary available.'}")
    for bullet in digest.summary.bullets:
        lines.append(f"  - {bullet}")

    lines.extend(["", "Tool health"])
    lines.append(f"  Tool calls:   {_format_int(digest.tool_health.tool_call_count)}")
    lines.append(
        f"  Failures:     {_format_int(digest.tool_health.tool_failure_count)}"
    )
    if digest.tool_health.failed_tools:
        failed = ", ".join(
            f"{name}:{count}"
            for name, count in sorted(digest.tool_health.failed_tools.items())
        )
        lines.append(f"  Failed tools: {failed}")
    if digest.tool_health.warnings:
        lines.append(f"  Warnings:     {', '.join(digest.tool_health.warnings)}")

    lines.extend(["", "Health"])
    lines.append(f"  Score:        {_health_summary(digest)}")
    if digest.health is not None:
        lines.append(
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
            lines.append(f"  Penalties:    {penalty_text}")
        if digest.health.basis:
            lines.append("  Basis:")
            for item in digest.health.basis[:8]:
                lines.append(f"    - {item}")

    if digest.files_mentioned:
        lines.extend(["", "Files/paths mentioned"])
        for value in digest.files_mentioned[:12]:
            lines.append(f"  - {value}")

    if rich_output and digest.commands_mentioned:
        lines.extend(["", "Commands mentioned"])
        for value in digest.commands_mentioned[:12]:
            lines.append(f"  - {value}")

    lines.extend(
        [
            "",
            "Privacy",
            "  Redacted: true",
            f"  Raw transcript included: {str(digest.contains_raw_transcript).lower()}",
            f"  Snippets included: {str(digest.contains_snippets).lower()}",
        ]
    )

    return "\n".join(lines)
