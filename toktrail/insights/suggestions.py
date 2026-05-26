"""Deterministic suggestion rules for insights.

Each rule tests a signal and emits a DeterministicSuggestion with
kind, severity, title, detail, and optional command.  No LLM calls.
"""

from __future__ import annotations

from toktrail.insights.models import (
    InsightAggregate,
    InsightSessionMeta,
)


def generate_suggestions(
    current: InsightAggregate,
    previous: InsightAggregate | None = None,
    sessions: tuple[InsightSessionMeta, ...] = (),
) -> tuple[dict[str, object], ...]:
    """Generate deterministic suggestions based on current state.

    Returns a tuple of DeterministicSuggestion-like dicts so the
    caller can construct DeterministicSuggestion objects directly.
    """
    suggestions: list[dict[str, object]] = []

    # Rule 1: unpriced models
    if current.unpriced_count > 0:
        unpriced_models: set[str] = set()
        for s in sessions:
            if s.unpriced_count > 0:
                for m in s.models:
                    unpriced_models.add(m)
        models_str = (
            f" Models: {', '.join(sorted(unpriced_models))}." if unpriced_models else ""
        )
        suggestions.append(
            {
                "kind": "unpriced_models",
                "severity": "warning",
                "title": "Add missing price configuration",
                "detail": (
                    f"{current.unpriced_count} call(s) had usage but no "
                    f"actual price resolution.{models_str}"
                ),
                "command": "toktrail config prices",
            }
        )

    # Rule 2: high tool failures -> inspect session
    failure_sessions = sorted(
        [s for s in sessions if s.tool_failure_count >= 3],
        key=lambda s: s.tool_failure_count,
        reverse=True,
    )
    if failure_sessions:
        s = failure_sessions[0]
        cmd = f"toktrail analyze session {s.harness} {s.source_session_id}"
        suggestions.append(
            {
                "kind": "tool_failures",
                "severity": "warning",
                "title": "Inspect failed tool calls",
                "detail": (
                    f"Session {s.harness}/{s.source_session_id} had "
                    f"{s.tool_failure_count} failed tool calls."
                ),
                "command": cmd,
            }
        )

    # Rule 3: high cache miss ratio
    prompt_total = (
        current.input_tokens + current.cache_read_tokens + current.cache_write_tokens
    )
    if prompt_total > 50_000 and current.cache_read_ratio < 0.10:
        suggestions.append(
            {
                "kind": "cache_inefficiency",
                "severity": "info",
                "title": "Check context/cache reuse",
                "detail": (
                    f"Cache hit ratio is {current.cache_read_ratio:.0%} "
                    f"with {current.input_tokens:,} input tokens. "
                    "Consider reusing prompts or enabling caching."
                ),
                "command": "toktrail usage sessions --sort cost",
            }
        )

    # Rule 4: no area on high-cost sessions
    no_area_sessions = [
        s for s in sessions if s.area_path is None and s.virtual_cost > 0
    ]
    if no_area_sessions and current.virtual_cost > 0:
        top_no_area = sorted(
            no_area_sessions, key=lambda s: s.virtual_cost, reverse=True
        )
        s = top_no_area[0]
        suggestions.append(
            {
                "kind": "no_area",
                "severity": "info",
                "title": "Assign an area to untracked sessions",
                "detail": (
                    f"{len(no_area_sessions)} session(s) have no area "
                    f"assigned. Top: {s.harness}/{s.source_session_id} "
                    f"(${s.virtual_cost:.2f} virtual cost)."
                ),
                "command": "toktrail area assign --last <area>",
            }
        )

    # Rule 5: machine name missing
    unknown_machine = [
        s
        for s in sessions
        if s.origin_machine_id is None or s.origin_machine_id in ("", "(unknown)")
    ]
    if unknown_machine:
        suggestions.append(
            {
                "kind": "machine_name_missing",
                "severity": "info",
                "title": "Name this machine",
                "detail": (f"{len(unknown_machine)} session(s) have no machine name."),
                "command": "toktrail machine set-name <name>",
            }
        )

    # Rule 6: area dominates cost
    if current.by_area and current.virtual_cost > 0:
        for area_row in current.by_area:
            area_share = (
                float(area_row.virtual_cost) / float(current.virtual_cost)
                if current.virtual_cost > 0
                else 0
            )
            if area_share > 0.70:
                suggestions.append(
                    {
                        "kind": "area_dominance",
                        "severity": "info",
                        "title": "Drill down by area",
                        "detail": (
                            f"Area '{area_row.label}' accounts for "
                            f"{area_share:.0%} of total virtual cost."
                        ),
                        "command": (f"toktrail usage daily --area {area_row.key}"),
                    }
                )
                break

    # Rule 7: model repeatedly unpriced
    unpriced_model_counts: dict[str, int] = {}
    for s in sessions:
        if s.unpriced_count > 0:
            for m in s.models:
                unpriced_model_counts[m] = unpriced_model_counts.get(m, 0) + 1
    repeatedly_unpriced = {m: c for m, c in unpriced_model_counts.items() if c >= 2}
    if repeatedly_unpriced:
        models_str = ", ".join(sorted(repeatedly_unpriced.keys()))
        suggestions.append(
            {
                "kind": "model_repeatedly_unpriced",
                "severity": "warning",
                "title": "Add provider/model price",
                "detail": (
                    f"Models appearing in multiple unpriced sessions: {models_str}."
                ),
                "command": "toktrail config prices add ...",
            }
        )

    # Rule 8: cost spike compared to previous period
    if previous is not None and previous.virtual_cost > 0:
        cost_ratio = float(current.virtual_cost / previous.virtual_cost)
        if cost_ratio >= 2.0:
            suggestions.append(
                {
                    "kind": "cost_spike",
                    "severity": "warning",
                    "title": "Cost appears significantly higher",
                    "detail": (
                        f"Virtual cost is {cost_ratio:.1f}x the previous "
                        f"period (${current.virtual_cost:.2f} vs "
                        f"${previous.virtual_cost:.2f})."
                    ),
                    "command": None,
                }
            )

    # Rule 9: many failed edits
    edit_failures = [
        s
        for s in sessions
        if any(
            cat[0] == "edit_failed" and cat[1] >= 2 for cat in s.tool_failure_categories
        )
    ]
    if edit_failures:
        s = max(edit_failures, key=lambda s: s.tool_failure_count)
        cmd = f"toktrail analyze session {s.harness} {s.source_session_id}"
        suggestions.append(
            {
                "kind": "edit_failures",
                "severity": "info",
                "title": "Inspect session before continuing",
                "detail": (
                    f"Session {s.harness}/{s.source_session_id} has "
                    f"repeated edit failures."
                ),
                "command": cmd,
            }
        )

    return tuple(suggestions)
