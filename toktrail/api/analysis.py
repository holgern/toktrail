from __future__ import annotations

from pathlib import Path

from toktrail import db as db_module
from toktrail.analysis import (
    CacheCallAnalysis,
    CacheClusterAnalysis,
    analyze_usage_events,
)
from toktrail.api._common import (
    _get_harness,
    _load_costing_config,
    _open_state_db,
    _validate_source_path,
)
from toktrail.api.imports import import_usage
from toktrail.api.models import (
    CacheCallRow,
    CacheClusterRow,
    CostTotals,
    SessionCacheAnalysisReport,
    SessionTotals,
    TokenBreakdown,
)
from toktrail.config import CostingConfig
from toktrail.errors import (
    AmbiguousSourceSessionError,
    InvalidAPIUsageError,
    SourcePathError,
    StateDatabaseError,
)
from toktrail.models import TokenBreakdown as InternalTokenBreakdown
from toktrail.models import UsageEvent
from toktrail.reporting import UsageReportFilter


def session_cache_analysis(
    *,
    db_path: Path | None = None,
    config_path: Path | None = None,
    harness: str,
    source_session_id: str | None = None,
    last: bool = False,
    source_path: Path | None = None,
    refresh: bool = True,
    use_active_run: bool = True,
    cluster_tolerance: float = 0.05,
    include_calls: bool = True,
) -> SessionCacheAnalysisReport:
    if source_session_id is not None and last:
        msg = "source_session_id and last=True cannot be used together."
        raise InvalidAPIUsageError(msg)

    harness_name = _get_harness(harness).name
    costing_config = _load_costing_config(config_path)

    if source_path is not None and not refresh:
        events = _load_events_from_source(
            harness=harness_name,
            source_path=source_path,
            costing_config=costing_config,
            source_session_id=source_session_id,
            last=last,
        )
    else:
        events = _load_events_from_state(
            db_path=db_path,
            harness=harness_name,
            source_session_id=source_session_id,
            last=last,
            source_path=source_path,
            refresh=refresh,
            use_active_run=use_active_run,
        )

    analysis = analyze_usage_events(
        events,
        costing_config=costing_config,
        cluster_tolerance=cluster_tolerance,
    )
    calls = (
        tuple(_to_public_call(call) for call in analysis.calls) if include_calls else ()
    )
    clusters = tuple(_to_public_cluster(cluster) for cluster in analysis.clusters)
    return SessionCacheAnalysisReport(
        harness=analysis.harness,
        source_session_id=analysis.source_session_id,
        first_created_ms=analysis.first_created_ms,
        last_created_ms=analysis.last_created_ms,
        call_count=analysis.call_count,
        totals=SessionTotals(
            tokens=_to_public_tokens(analysis.tokens),
            costs=CostTotals(
                source_cost_usd=analysis.source_cost_usd,
                actual_cost_usd=analysis.actual_cost_usd,
                virtual_cost_usd=analysis.virtual_cost_usd,
                unpriced_count=0,
            ),
            message_count=analysis.call_count,
        ),
        cache_read_tokens=analysis.cache_read_tokens,
        cache_write_tokens=analysis.cache_write_tokens,
        prompt_like_tokens=analysis.prompt_like_tokens,
        cache_reuse_ratio=analysis.cache_reuse_ratio,
        cache_presence_ratio=analysis.cache_presence_ratio,
        source_cost_usd=analysis.source_cost_usd,
        actual_cost_usd=analysis.actual_cost_usd,
        virtual_cost_usd=analysis.virtual_cost_usd,
        virtual_uncached_cost_usd=analysis.virtual_uncached_cost_usd,
        virtual_cache_savings_usd=analysis.virtual_cache_savings_usd,
        estimated_source_cache_loss_usd=analysis.estimated_source_cache_loss_usd,
        calls=calls,
        clusters=clusters,
        warnings=analysis.warnings,
    )


def _load_events_from_state(
    *,
    db_path: Path | None,
    harness: str,
    source_session_id: str | None,
    last: bool,
    source_path: Path | None,
    refresh: bool,
    use_active_run: bool,
) -> list[UsageEvent]:
    if refresh:
        import_usage(
            db_path,
            harness,
            source_path=source_path,
            source_session_id=source_session_id,
            use_active_session=use_active_run,
            include_raw_json=False,
        )

    conn, _ = _open_state_db(db_path)
    try:
        active_run_id = (
            db_module.get_active_tracking_session(conn) if use_active_run else None
        )
        base_filters = UsageReportFilter(
            tracking_session_id=active_run_id,
            harness=harness,
        )
        all_events = db_module.list_usage_events(conn, base_filters, order="created")
        selected_source_session = _resolve_source_session(
            harness=harness,
            events=all_events,
            source_session_id=source_session_id,
            last=last,
        )
        selected_filters = UsageReportFilter(
            tracking_session_id=active_run_id,
            harness=harness,
            source_session_id=selected_source_session,
        )
        selected_events = db_module.list_usage_events(
            conn,
            selected_filters,
            order="created",
        )
    except ValueError as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()
    return selected_events


def _load_events_from_source(
    *,
    harness: str,
    source_path: Path,
    costing_config: CostingConfig,
    source_session_id: str | None,
    last: bool,
) -> list[UsageEvent]:
    definition = _get_harness(harness)
    resolved_source = _validate_source_path(
        harness,
        definition.resolve_source_path(source_path),
        explicit_source=source_path,
    )
    if resolved_source is None:
        msg = f"No source path available for harness {harness}."
        raise SourcePathError(msg)

    selected_source_session = source_session_id
    if selected_source_session is None:
        sessions = definition.list_sessions(
            resolved_source,
            costing_config=costing_config,
        )
        if not sessions:
            msg = (
                f"No source sessions found for harness {harness} "
                f"at {resolved_source}."
            )
            raise SourcePathError(msg)
        if last:
            selected_source_session = max(
                sessions,
                key=lambda row: (row.last_created_ms, row.source_session_id),
            ).source_session_id
        elif len(sessions) == 1:
            selected_source_session = sessions[0].source_session_id
        else:
            candidate_ids = ", ".join(
                summary.source_session_id for summary in sessions[:10]
            )
            msg = (
                f"Multiple source sessions found for harness {harness}: "
                f"{candidate_ids}. "
                "Provide source_session_id or use last=True."
            )
            raise AmbiguousSourceSessionError(msg)
    result = definition.scan(
        resolved_source,
        source_session_id=selected_source_session,
        include_raw_json=False,
    )
    if not result.events:
        msg = (
            f"No usage events found for harness {harness} source session "
            f"{selected_source_session!r}."
        )
        raise SourcePathError(msg)
    return list(result.events)


def _resolve_source_session(
    *,
    harness: str,
    events: list[UsageEvent],
    source_session_id: str | None,
    last: bool,
) -> str:
    sessions: dict[str, int] = {}
    for event in events:
        sessions[event.source_session_id] = max(
            sessions.get(event.source_session_id, event.created_ms),
            event.created_ms,
        )
    if source_session_id is not None:
        if source_session_id not in sessions:
            msg = f"Source session not found for harness {harness}: {source_session_id}"
            raise SourcePathError(msg)
        return source_session_id
    if not sessions:
        msg = f"No usage events found for harness {harness}."
        raise SourcePathError(msg)
    if last:
        return max(sessions.items(), key=lambda item: (item[1], item[0]))[0]
    if len(sessions) == 1:
        return next(iter(sessions))
    candidates = ", ".join(
        f"{session_id}@{created_ms}"
        for session_id, created_ms in sorted(
            sessions.items(),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )[:10]
    )
    msg = (
        f"Multiple source sessions found for harness {harness}: {candidates}. "
        "Provide source_session_id or use last=True."
    )
    raise AmbiguousSourceSessionError(msg)


def _to_public_tokens(value: InternalTokenBreakdown) -> TokenBreakdown:
    return TokenBreakdown(
        input=value.input,
        output=value.output,
        reasoning=value.reasoning,
        cache_read=value.cache_read,
        cache_write=value.cache_write,
        cache_output=value.cache_output,
    )


def _to_public_call(value: CacheCallAnalysis) -> CacheCallRow:
    return CacheCallRow(
        ordinal=value.ordinal,
        harness=value.harness,
        source_session_id=value.source_session_id,
        source_row_id=value.source_row_id,
        source_message_id=value.source_message_id,
        provider_id=value.provider_id,
        model_id=value.model_id,
        thinking_level=value.thinking_level,
        agent=value.agent,
        created_ms=value.created_ms,
        completed_ms=value.completed_ms,
        tokens=_to_public_tokens(value.tokens),
        source_cost_usd=value.source_cost_usd,
        actual_cost_usd=value.actual_cost_usd,
        virtual_cost_usd=value.virtual_cost_usd,
        virtual_uncached_cost_usd=value.virtual_uncached_cost_usd,
        virtual_cache_savings_usd=value.virtual_cache_savings_usd,
        prompt_like_tokens=value.prompt_like_tokens,
        cache_reuse_ratio=value.cache_reuse_ratio,
        cache_presence_ratio=value.cache_presence_ratio,
        source_cost_per_1m_prompt_like=value.source_cost_per_1m_prompt_like,
        cache_status=value.cache_status,
        flags=value.flags,
    )


def _to_public_cluster(value: CacheClusterAnalysis) -> CacheClusterRow:
    return CacheClusterRow(
        provider_id=value.provider_id,
        model_id=value.model_id,
        thinking_level=value.thinking_level,
        prompt_like_min=value.prompt_like_min,
        prompt_like_max=value.prompt_like_max,
        call_count=value.call_count,
        hit_count=value.hit_count,
        miss_count=value.miss_count,
        median_hit_source_cost_usd=value.median_hit_source_cost_usd,
        median_miss_source_cost_usd=value.median_miss_source_cost_usd,
        estimated_source_loss_usd=value.estimated_source_loss_usd,
    )


__all__ = ["session_cache_analysis"]
