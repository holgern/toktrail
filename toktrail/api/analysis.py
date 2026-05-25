from __future__ import annotations

import json
import sqlite3
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
    _missing_source_path_message,
    _open_state_db,
    _validate_source_path,
)
from toktrail.api.imports import import_usage
from toktrail.api.models import (
    CacheCallRow,
    CacheClusterRow,
    CostTotals,
    SessionCacheAnalysisReport,
    SessionCompactReport,
    SessionDigest,
    SessionToolCallReport,
    SessionToolHealth,
    SessionTotals,
    TokenBreakdown,
    ToolCallRow,
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
from toktrail.reporting import UsageReportFilter, UsageSessionRow
from toktrail.session_digests import (
    build_session_digest,
    digest_source_fingerprint,
    public_digest_from_internal,
)


def session_cache_analysis(
    *,
    db_path: Path | None = None,
    config_path: Path | None = None,
    harness: str,
    source_session_id: str | None = None,
    last: bool = False,
    source_path: Path | None = None,
    refresh: bool = True,
    use_active_run: bool = False,
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
                unpriced_count=analysis.unpriced_count,
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


def session_digest(
    *,
    db_path: Path | None = None,
    config_path: Path | None = None,
    harness: str,
    source_session_id: str | None = None,
    last: bool = False,
    source_path: Path | None = None,
    refresh: bool = True,
    persist: bool = False,
    include_snippets: bool = False,
) -> object:
    if include_snippets:
        msg = "include_snippets is not supported for phase-1 session digests."
        raise InvalidAPIUsageError(msg)
    if source_session_id is not None and last:
        msg = "source_session_id and last=True cannot be used together."
        raise InvalidAPIUsageError(msg)

    definition = _get_harness(harness)
    harness_name = definition.name
    costing_config = _load_costing_config(config_path)
    if refresh:
        import_usage(
            db_path,
            harness_name,
            source_path=source_path,
            source_session_id=source_session_id,
            use_active_session=False,
            include_raw_json=False,
        )

    conn, _ = _open_state_db(db_path)
    try:
        usage_session = _resolve_usage_session_for_digest(
            conn=conn,
            harness=harness_name,
            source_session_id=source_session_id,
            last=last,
            costing_config=costing_config,
        )
        transcript_events = []
        event_source = _resolve_digest_source_path(
            definition=definition,
            source_path=source_path,
            usage_source_paths=usage_session.source_paths,
        )
        if event_source is not None and definition.extract_session_events is not None:
            transcript_events = list(  # type: ignore[call-overload]
                definition.extract_session_events(
                    event_source,
                    source_session_id=usage_session.source_session_id,
                )
            )
        digest = build_session_digest(
            usage_session=usage_session,
            transcript_events=transcript_events,
            source_fingerprint=digest_source_fingerprint(transcript_events),
        )
        if persist:
            db_module.upsert_source_session_digest(conn, digest)
            conn.commit()
    except ValueError as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()
    return public_digest_from_internal(digest)


def session_report(
    *,
    db_path: Path | None = None,
    config_path: Path | None = None,
    harness: str,
    source_session_id: str | None = None,
    last: bool = False,
    source_path: Path | None = None,
    refresh: bool = True,
    persist: bool = False,
    include_snippets: bool = False,
) -> SessionCompactReport:
    if include_snippets:
        msg = "include_snippets is not supported for compact session reports."
        raise InvalidAPIUsageError(msg)
    if source_session_id is not None and last:
        msg = "source_session_id and last=True cannot be used together."
        raise InvalidAPIUsageError(msg)

    definition = _get_harness(harness)
    harness_name = definition.name
    costing_config = _load_costing_config(config_path)
    if refresh:
        import_usage(
            db_path,
            harness_name,
            source_path=source_path,
            source_session_id=source_session_id,
            use_active_session=False,
            include_raw_json=False,
        )

    conn, _ = _open_state_db(db_path)
    try:
        usage_session = _resolve_usage_session_for_digest(
            conn=conn,
            harness=harness_name,
            source_session_id=source_session_id,
            last=last,
            costing_config=costing_config,
        )
        digest = _load_or_build_session_digest(
            conn=conn,
            definition=definition,
            source_path=source_path,
            usage_session=usage_session,
            persist=persist,
        )
    except ValueError as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()
    return _compact_report_from_usage_session(usage_session, digest)


def _load_or_build_session_digest(
    *,
    conn: sqlite3.Connection,
    definition: object,
    source_path: Path | None,
    usage_session: UsageSessionRow,
    persist: bool,
) -> SessionDigest | None:
    if usage_session.origin_machine_id is not None:
        persisted = db_module.get_source_session_digest(
            conn,
            origin_machine_id=usage_session.origin_machine_id,
            harness=usage_session.harness,
            source_session_id=usage_session.source_session_id,
        )
        if persisted is not None:
            return public_digest_from_internal(persisted)  # type: ignore[return-value]

    event_source = _resolve_digest_source_path(
        definition=definition,
        source_path=source_path,
        usage_source_paths=usage_session.source_paths,
    )
    transcript_events = []
    if event_source is not None and definition.extract_session_events is not None:  # type: ignore[attr-defined]
        transcript_events = list(
            definition.extract_session_events(  # type: ignore[attr-defined]
                event_source,
                source_session_id=usage_session.source_session_id,
            )
        )
    digest = build_session_digest(
        usage_session=usage_session,
        transcript_events=transcript_events,
        source_fingerprint=digest_source_fingerprint(transcript_events),
    )
    if persist:
        db_module.upsert_source_session_digest(conn, digest)
        conn.commit()
    return public_digest_from_internal(digest)  # type: ignore[return-value]


def _compact_report_from_usage_session(
    usage_session: UsageSessionRow,
    digest: SessionDigest | None,
) -> SessionCompactReport:
    return SessionCompactReport(
        harness=usage_session.harness,
        source_session_id=usage_session.source_session_id,
        origin_machine_id=usage_session.origin_machine_id,
        machine_label=usage_session.machine_label,
        area_path=usage_session.area_path,
        cwd=usage_session.cwd,
        source_dir=usage_session.source_dir,
        git_root=usage_session.git_root,
        git_remote=usage_session.git_remote,
        session_title=usage_session.session_title,
        started_ms=usage_session.first_ms,
        last_seen_ms=usage_session.last_ms,
        message_count=usage_session.message_count,
        usage=SessionTotals(
            tokens=_to_public_tokens(usage_session.tokens),
            costs=CostTotals(
                source_cost_usd=usage_session.costs.source_cost_usd,
                actual_cost_usd=usage_session.costs.actual_cost_usd,
                virtual_cost_usd=usage_session.costs.virtual_cost_usd,
                unpriced_count=usage_session.costs.unpriced_count,
            ),
            message_count=usage_session.message_count,
        ),
        models=usage_session.models,
        providers=usage_session.providers,
        summary=digest.summary if digest is not None else None,
        tool_health=(
            digest.tool_health
            if digest is not None
            else SessionToolHealth(warnings=("no-session-digest",))
        ),
        digest_available=digest is not None,
        generated_at_ms=digest.generated_at_ms if digest is not None else None,
        source_fingerprint=digest.source_fingerprint if digest is not None else None,
    )


def _resolve_usage_session_for_digest(
    *,
    conn: sqlite3.Connection,
    harness: str,
    source_session_id: str | None,
    last: bool,
    costing_config: CostingConfig,
) -> UsageSessionRow:
    from toktrail.reporting import UsageSessionsFilter

    report = db_module.summarize_usage_sessions(
        conn,
        UsageSessionsFilter(
            harness=harness,
            source_session_id=source_session_id,
            limit=1 if last else None,
            order="desc",
        ),
        costing_config=costing_config,
    )
    if source_session_id is not None:
        if not report.sessions:
            msg = f"Source session not found for harness {harness}: {source_session_id}"
            raise SourcePathError(msg)
        return report.sessions[0]  # type: ignore[no-any-return]
    if last:
        if not report.sessions:
            msg = f"No usage events found for harness {harness}."
            raise SourcePathError(msg)
        return report.sessions[0]  # type: ignore[no-any-return]
    if len(report.sessions) == 1:
        return report.sessions[0]  # type: ignore[no-any-return]
    if not report.sessions:
        msg = f"No usage events found for harness {harness}."
        raise SourcePathError(msg)
    candidates = ", ".join(row.source_session_id for row in report.sessions[:10])
    msg = (
        f"Multiple source sessions found for harness {harness}: {candidates}. "
        "Provide source_session_id or use last=True."
    )
    raise AmbiguousSourceSessionError(msg)


def _resolve_digest_source_path(
    *,
    definition: object,
    source_path: Path | None,
    usage_source_paths: tuple[str, ...],
) -> Path | None:
    if source_path is not None:
        return definition.resolve_source_path(source_path)  # type: ignore[no-any-return, attr-defined]
    for value in usage_source_paths:
        path = Path(value).expanduser()
        if path.exists():
            return path
    return definition.resolve_source_path(None)  # type: ignore[no-any-return, attr-defined]


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
            source_session_id=selected_source_session,  # type: ignore[arg-type]
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
    return selected_events  # type: ignore[no-any-return]


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
                f"No source sessions found for harness {harness} at {resolved_source}."
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
) -> object:
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
        missing_price_kinds=value.missing_price_kinds,
        context_tokens=value.context_tokens,
        actual_price_context_label=value.actual_price_context_label,
        virtual_price_context_label=value.virtual_price_context_label,
        prompt_like_tokens=value.prompt_like_tokens,
        cache_reuse_ratio=value.cache_reuse_ratio,
        cache_presence_ratio=value.cache_presence_ratio,
        source_cost_per_1m_prompt_like=value.source_cost_per_1m_prompt_like,
        source_cost_per_1m_total_tokens=value.source_cost_per_1m_total_tokens,
        virtual_cost_per_1m_prompt_like=value.virtual_cost_per_1m_prompt_like,
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
        call_ordinals=value.call_ordinals,
    )


def session_tool_call_analysis(
    *,
    harness: str,
    db_path: Path | None = None,
    config_path: Path | None = None,
    source_path: Path | None = None,
    source_session_id: str | None = None,
    last: bool = False,
    bad_only: bool = True,
    include_output: bool = False,
    include_raw_json: bool = False,
    limit: int | None = None,
    max_snippet_chars: int = 1000,
) -> SessionToolCallReport:
    """Analyze tool calls in a source session for failures and diagnostics.

    This is a read-only operation. It does not import or persist any data.
    Currently only 'codex' harness is supported.
    """
    if source_session_id is not None and last:
        msg = "source_session_id and last=True cannot be used together."
        raise InvalidAPIUsageError(msg)

    if include_raw_json and not _tool_call_analysis_json_allowed():
        pass  # raw_json is allowed in API, just documented as JSON-only in CLI

    definition = _get_harness(harness)
    harness_name = definition.name

    if harness_name != "codex":
        msg = f"Tool-call analysis is not supported for harness {harness!r}."
        raise InvalidAPIUsageError(msg)

    # Resolve source path
    resolved_source = definition.resolve_source_path(source_path)
    if resolved_source is None or not resolved_source.exists():
        msg = _missing_source_path_message(
            harness_name,
            resolved_source,
            explicit_source=source_path,
        )
        raise SourcePathError(msg)

    # Import the scanner
    from toktrail.adapters.codex_tool_calls import scan_codex_tool_calls

    # Resolve session
    selected_session_id = source_session_id
    if selected_session_id is None:
        costing_config = _load_costing_config(config_path)
        try:
            sessions = definition.list_sessions(
                resolved_source, costing_config=costing_config
            )
        except Exception:
            sessions = ()  # type: ignore[assignment]

        if last and sessions:
            selected_session_id = max(
                sessions,
                key=lambda s: (s.last_created_ms, s.source_session_id),
            ).source_session_id
        elif sessions:
            selected_session_id = sessions[0].source_session_id

    # Fallback: if no usage sessions found, try newest .jsonl by mtime
    if selected_session_id is None:
        if not last:
            # Need an explicit session or --last
            if resolved_source.is_file():
                selected_session_id = resolved_source.stem
            else:
                jsonl_files = sorted(
                    resolved_source.rglob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                )
                if not jsonl_files:
                    msg = (
                        f"No source sessions found for harness"
                        f" {harness_name} at {resolved_source}."
                    )
                    raise SourcePathError(msg)
                selected_session_id = jsonl_files[-1].stem

    scan_result = scan_codex_tool_calls(
        resolved_source,
        source_session_id=selected_session_id,
        include_raw_json=include_raw_json,
        include_output=include_output,
        max_snippet_chars=max_snippet_chars,
    )

    # Convert to public model
    calls = tuple(
        ToolCallRow(
            ordinal=call.ordinal,
            tool_name=call.tool_name,
            status=call.status,
            source_path=str(call.source_path),
            line_number=call.line_number,
            created_ms=call.created_ms,
            completed_ms=call.completed_ms,
            call_id=call.call_id,
            cwd=call.cwd,
            command=call.command,
            arguments=(
                json.loads(call.arguments_json) if call.arguments_json else None
            ),
            exit_code=call.exit_code,
            duration_ms=call.duration_ms,
            error=call.error,
            stdout_snippet=call.stdout_snippet,
            stderr_snippet=call.stderr_snippet,
        )
        for call in scan_result.calls
    )

    if bad_only:
        calls = tuple(c for c in calls if c.is_bad)

    if limit is not None and limit < len(calls):
        calls = calls[:limit]

    return SessionToolCallReport(
        harness=harness_name,
        source_session_id=selected_session_id or "unknown",
        source_paths=(str(resolved_source),),
        tool_call_count=scan_result.tool_call_count,
        failure_count=scan_result.failure_count,
        timeout_count=scan_result.timeout_count,
        calls=calls,
        warnings=scan_result.warnings,
    )


def _tool_call_analysis_json_allowed() -> bool:
    # Placeholder: in CLI, --raw-tool-json requires --json.
    # In the API function, raw_json is always allowed (the CLI enforces the constraint).
    return True


__all__ = [
    "session_cache_analysis",
    "session_digest",
    "session_report",
    "session_tool_call_analysis",
]
