"""Extract per-session insights metadata from existing toktrail state.

Reads from:
  - usage_sessions report (token/cost per session)
  - source_session_metadata (cwd, git_root, session_title, timestamps)
  - session digests (tool health, summary)
  - source session metadata (area assignments)

Does NOT re-parse source files.  Derives everything from normalized
usage events and stored metadata.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from toktrail._db._impl_db import (
    SourceSessionMetadata,
    list_source_session_digests,
    list_source_session_metadata,
)
from toktrail.api._common import _open_state_db
from toktrail.api.models import UsageSessionRow, UsageSessionsReport
from toktrail.api.reports import usage_sessions_report
from toktrail.config import CostingConfig
from toktrail.insights.models import (
    InsightAggregate,
    InsightGroupRow,
    InsightSessionMeta,
)


def _session_meta_from_row(
    row: UsageSessionRow,
    metadata_lookup: dict[tuple[str, str, str], SourceSessionMetadata],
    tool_call_counts: dict[tuple[str, str, str], int] | None = None,
    tool_failure_counts: dict[tuple[str, str, str], int] | None = None,
    tool_failure_categories: dict[tuple[str, str, str], tuple[tuple[str, int], ...]]
    | None = None,
    first_prompts: dict[tuple[str, str, str], str] | None = None,
    user_message_counts: dict[tuple[str, str, str], int] | None = None,
    assistant_message_counts: dict[tuple[str, str, str], int] | None = None,
) -> InsightSessionMeta:
    """Convert a UsageSessionRow to InsightSessionMeta."""
    key = (row.origin_machine_id or "", row.harness, row.source_session_id)
    # Use origin_machine_id from the row
    machine_id = row.origin_machine_id
    cwd = row.cwd
    start_ms = row.first_ms
    end_ms = row.last_ms
    duration_ms = (
        (end_ms - start_ms) if (start_ms is not None and end_ms is not None) else None
    )

    tc = (tool_call_counts or {}).get(key, 0)
    tf = (tool_failure_counts or {}).get(key, 0)
    tfc = (tool_failure_categories or {}).get(key, ())
    fp = (first_prompts or {}).get(key)
    um = (user_message_counts or {}).get(key, 0)
    am = (assistant_message_counts or {}).get(key, 0)

    return InsightSessionMeta(
        harness=row.harness,
        source_session_id=row.source_session_id,
        origin_machine_id=machine_id,
        source_path=row.source_paths[0] if row.source_paths else None,
        cwd=cwd,
        area_path=row.area_path,
        area_name=row.area_name,
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=duration_ms,
        user_messages=um,
        assistant_messages=am,
        total_tokens=row.tokens.total,
        input_tokens=row.tokens.input,
        output_tokens=row.tokens.output,
        reasoning_tokens=row.tokens.reasoning,
        cache_read_tokens=row.tokens.cache_read,
        cache_write_tokens=row.tokens.cache_write,
        cache_output_tokens=row.tokens.cache_output,
        actual_cost=row.costs.actual_cost_usd,
        virtual_cost=row.costs.virtual_cost_usd,
        source_cost=row.costs.source_cost_usd,
        unpriced_count=row.costs.unpriced_count,
        tool_call_count=tc,
        tool_failure_count=tf,
        tool_failure_categories=tfc,
        models=row.models,
        providers=row.providers,
        first_prompt_preview=fp,
    )


def extract_session_metas(
    *,
    db_path: Path | None = None,
    config_path: Path | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
    period: str | None = None,
    timezone_name: str | None = None,
    utc: bool = False,
    machine_id: str | None = None,
    harness: str | None = None,
    source_session_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    agent: str | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    split_thinking: bool = False,
    session_id: int | None = None,
    costing_config: CostingConfig | None = None,
) -> tuple[InsightSessionMeta, ...]:
    """Extract per-session metadata for insights from existing state.

    Uses usage_sessions_report for token/cost data, then enriches
    with source session metadata for timestamps and tool health.
    """
    # costing_config is not used directly here;
    # it flows through the underlying usage_sessions_report via config_path

    sessions_report: UsageSessionsReport = usage_sessions_report(
        db_path=db_path,
        session_id=session_id,
        period=period,
        timezone=timezone_name,
        utc=utc,
        since_ms=since_ms,
        until_ms=until_ms,
        machine_id=machine_id,
        harness=harness,
        source_session_id=source_session_id,
        provider_id=provider_id,
        model_id=model_id,
        agent=agent,
        area=area,
        area_leaf=area_leaf,
        area_exact=area_exact,
        unassigned_area=unassigned_area,
        split_thinking=split_thinking,
        config_path=config_path,
        limit=None,
        order="desc",
        breakdown=False,
    )

    conn, resolved_path = _open_state_db(db_path)
    try:
        from toktrail._db._impl_db import migrate

        migrate(conn)
        raw_meta = list_source_session_metadata(conn)
        raw_digests = list_source_session_digests(conn)
        meta_lookup: dict[tuple[str, str, str], SourceSessionMetadata] = {}
        tool_call_counts: dict[tuple[str, str, str], int] = {}
        tool_failure_counts: dict[tuple[str, str, str], int] = {}
        tool_failure_categories: dict[tuple[str, str, str], tuple[tuple[str, int], ...]] = {}
        for m in raw_meta:
            key = (m.origin_machine_id, m.harness, m.source_session_id)
            meta_lookup[key] = m
        for digest in raw_digests:
            key = (
                digest.origin_machine_id or "",
                digest.harness,
                digest.source_session_id,
            )
            tool_call_counts[key] = digest.tool_health.tool_call_count
            tool_failure_counts[key] = digest.tool_health.tool_failure_count
            tool_failure_categories[key] = tuple(
                sorted(
                    digest.tool_health.failed_tools.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            )
    finally:
        conn.close()

    metas: list[InsightSessionMeta] = []
    for row in sessions_report.sessions:
        meta = _session_meta_from_row(
            row,
            meta_lookup,
            tool_call_counts=tool_call_counts,
            tool_failure_counts=tool_failure_counts,
            tool_failure_categories=tool_failure_categories,
        )
        metas.append(meta)

    return tuple(sorted(metas, key=lambda m: m.start_ms or 0, reverse=True))


def extract_session_metas_from_report(
    sessions_report: UsageSessionsReport,
    metadata_lookup: dict[tuple[str, str, str], SourceSessionMetadata],
    *,
    tool_call_counts: dict[tuple[str, str, str], int] | None = None,
    tool_failure_counts: dict[tuple[str, str, str], int] | None = None,
    tool_failure_categories: dict[tuple[str, str, str], tuple[tuple[str, int], ...]]
    | None = None,
    first_prompts: dict[tuple[str, str, str], str] | None = None,
    user_message_counts: dict[tuple[str, str, str], int] | None = None,
    assistant_message_counts: dict[tuple[str, str, str], int] | None = None,
) -> tuple[InsightSessionMeta, ...]:
    """Build session metas from a pre-fetched report and metadata lookup.

    This variant allows callers to supply their own data sources,
    which is useful for testing and for service.py orchestration.
    """
    metas: list[InsightSessionMeta] = []
    for row in sessions_report.sessions:
        meta = _session_meta_from_row(
            row,
            metadata_lookup,
            tool_call_counts=tool_call_counts,
            tool_failure_counts=tool_failure_counts,
            tool_failure_categories=tool_failure_categories,
            first_prompts=first_prompts,
            user_message_counts=user_message_counts,
            assistant_message_counts=assistant_message_counts,
        )
        metas.append(meta)

    return tuple(sorted(metas, key=lambda m: m.start_ms or 0, reverse=True))


def aggregate_sessions(
    sessions: tuple[InsightSessionMeta, ...],
) -> InsightAggregate:
    """Aggregate per-session metas into group totals and per-dimension rows."""
    if not sessions:
        return InsightAggregate()

    total_tokens = sum(s.total_tokens for s in sessions)
    total_input = sum(s.input_tokens for s in sessions)
    total_output = sum(s.output_tokens for s in sessions)
    total_reasoning = sum(s.reasoning_tokens for s in sessions)
    total_cache_read = sum(s.cache_read_tokens for s in sessions)
    total_cache_write = sum(s.cache_write_tokens for s in sessions)
    total_cache_output = sum(s.cache_output_tokens for s in sessions)
    total_actual = sum((s.actual_cost for s in sessions), Decimal(0))
    total_virtual = sum((s.virtual_cost for s in sessions), Decimal(0))
    total_source = sum((s.source_cost for s in sessions), Decimal(0))
    total_unpriced = sum(s.unpriced_count for s in sessions)
    total_tool_calls = sum(s.tool_call_count for s in sessions)
    total_tool_failures = sum(s.tool_failure_count for s in sessions)
    total_messages = sum(s.user_messages + s.assistant_messages for s in sessions)

    by_area = _group_by(sessions, key_fn=lambda s: s.area_path or "(unassigned)")
    by_machine = _group_by(
        sessions, key_fn=lambda s: s.origin_machine_id or "(unknown)"
    )
    by_harness = _group_by(sessions, key_fn=lambda s: s.harness)
    by_model = _group_sessions_by_model(sessions)
    by_provider = _group_by(
        sessions,
        key_fn=lambda s: s.providers[0] if s.providers else "(unknown)",
    )

    # Top sessions by virtual cost
    top_sessions = tuple(
        sorted(sessions, key=lambda s: s.virtual_cost, reverse=True)[:20]
    )

    return InsightAggregate(
        session_count=len(sessions),
        message_count=total_messages,
        total_tokens=total_tokens,
        input_tokens=total_input,
        output_tokens=total_output,
        reasoning_tokens=total_reasoning,
        cache_read_tokens=total_cache_read,
        cache_write_tokens=total_cache_write,
        cache_output_tokens=total_cache_output,
        actual_cost=total_actual,
        virtual_cost=total_virtual,
        source_cost=total_source,
        unpriced_count=total_unpriced,
        tool_call_count=total_tool_calls,
        tool_failure_count=total_tool_failures,
        by_area=tuple(by_area),
        by_machine=tuple(by_machine),
        by_harness=tuple(by_harness),
        by_model=tuple(by_model),
        by_provider=tuple(by_provider),
        top_sessions=top_sessions,
    )


def _group_by(
    sessions: tuple[InsightSessionMeta, ...],
    key_fn: Callable[[InsightSessionMeta], str],
) -> list[InsightGroupRow]:
    """Group sessions by an arbitrary key function."""
    groups: dict[str, list[InsightSessionMeta]] = {}
    for s in sessions:
        k = key_fn(s)
        groups.setdefault(k, []).append(s)

    rows: list[InsightGroupRow] = []
    for key, group in sorted(groups.items()):
        rows.append(_aggregate_group(key, key, group))
    return rows


def _group_sessions_by_model(
    sessions: tuple[InsightSessionMeta, ...],
) -> list[InsightGroupRow]:
    """Group sessions by model, counting each session once per model."""
    from collections import defaultdict

    model_groups: dict[str, list[InsightSessionMeta]] = defaultdict(list)
    for s in sessions:
        models = s.models if s.models else ("(unknown)",)
        for m in models:
            model_groups[m].append(s)

    rows: list[InsightGroupRow] = []
    for model_id, group in sorted(model_groups.items()):
        rows.append(_aggregate_group(model_id, model_id, group))
    return rows


def _aggregate_group(
    key: str,
    label: str,
    sessions: list[InsightSessionMeta],
) -> InsightGroupRow:
    """Aggregate a list of sessions into a group row."""
    return InsightGroupRow(
        key=key,
        label=label,
        session_count=len(sessions),
        message_count=sum(s.user_messages + s.assistant_messages for s in sessions),
        total_tokens=sum(s.total_tokens for s in sessions),
        input_tokens=sum(s.input_tokens for s in sessions),
        output_tokens=sum(s.output_tokens for s in sessions),
        reasoning_tokens=sum(s.reasoning_tokens for s in sessions),
        cache_read_tokens=sum(s.cache_read_tokens for s in sessions),
        cache_write_tokens=sum(s.cache_write_tokens for s in sessions),
        cache_output_tokens=sum(s.cache_output_tokens for s in sessions),
        actual_cost=sum((s.actual_cost for s in sessions), Decimal(0)),
        virtual_cost=sum((s.virtual_cost for s in sessions), Decimal(0)),
        source_cost=sum((s.source_cost for s in sessions), Decimal(0)),
        unpriced_count=sum(s.unpriced_count for s in sessions),
        tool_call_count=sum(s.tool_call_count for s in sessions),
        tool_failure_count=sum(s.tool_failure_count for s in sessions),
    )
