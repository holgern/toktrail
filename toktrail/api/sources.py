from __future__ import annotations

from pathlib import Path
from time import time

from toktrail.api._common import (
    _get_harness,
    _load_costing_config,
    _validate_source_path,
)
from toktrail.api._conversions import _to_public_source_summary, _to_public_usage_event
from toktrail.api.models import (
    ScanUsageResult,
    SourceSessionDiff,
    SourceSessionSnapshot,
    SourceSessionSummary,
)
from toktrail.api.paths import resolve_source_path
from toktrail.errors import InvalidAPIUsageError


def list_source_sessions(
    harness: str,
    *,
    source_path: Path | None = None,
    source_session_id: str | None = None,
    last: bool = False,
    limit: int | None = None,
    sort: str = "last",
    config_path: Path | None = None,
) -> tuple[SourceSessionSummary, ...]:
    if source_session_id is not None and last:
        msg = "source_session_id and last=True cannot be used together."
        raise InvalidAPIUsageError(msg)
    definition = _get_harness(harness)
    resolved = _validate_source_path(
        definition.name,
        resolve_source_path(definition.name, source_path),
        explicit_source=source_path,
        allow_missing_default=True,
    )
    if resolved is None:
        return ()
    costing_config = _load_costing_config(config_path)
    summaries = [
        _to_public_source_summary(summary)
        for summary in definition.list_sessions(resolved, costing_config=costing_config)
    ]
    ordered = _sorted_source_sessions(summaries, sort=sort)
    if source_session_id is not None:
        ordered = [
            summary
            for summary in ordered
            if summary.source_session_id == source_session_id
        ]
    if last:
        return tuple(_sorted_source_sessions(ordered, sort="last")[:1])
    if limit is not None:
        ordered = ordered[:limit]
    return tuple(ordered)


def capture_source_snapshot(
    harness: str,
    *,
    source_path: Path | None = None,
    config_path: Path | None = None,
) -> SourceSessionSnapshot:
    definition = _get_harness(harness)
    resolved = _validate_source_path(
        definition.name,
        resolve_source_path(definition.name, source_path),
        explicit_source=source_path,
        allow_missing_default=True,
        allow_missing_explicit=definition.name == "copilot",
    )
    if resolved is None or not resolved.exists():
        sessions: tuple[SourceSessionSummary, ...] = ()
    else:
        sessions = list_source_sessions(
            definition.name,
            source_path=resolved,
            config_path=config_path,
        )
    return SourceSessionSnapshot(
        harness=definition.name,
        source_path=resolved,
        captured_ms=int(time() * 1000),
        sessions=sessions,
    )


def diff_source_snapshots(
    before: SourceSessionSnapshot,
    after: SourceSessionSnapshot,
) -> SourceSessionDiff:
    if before.harness != after.harness:
        msg = (
            "Source snapshots must use the same harness: "
            f"{before.harness!r} != {after.harness!r}"
        )
        raise InvalidAPIUsageError(msg)

    before_map = {summary.source_session_id: summary for summary in before.sessions}
    after_map = {summary.source_session_id: summary for summary in after.sessions}

    new_sessions = tuple(
        summary
        for session_id, summary in after_map.items()
        if session_id not in before_map
    )
    updated_sessions = tuple(
        summary
        for session_id, summary in after_map.items()
        if session_id in before_map
        and _source_summary_identity(summary)
        != _source_summary_identity(before_map[session_id])
    )
    unchanged_sessions = tuple(
        summary
        for session_id, summary in after_map.items()
        if session_id in before_map
        and _source_summary_identity(summary)
        == _source_summary_identity(before_map[session_id])
    )
    return SourceSessionDiff(
        harness=after.harness,
        before_count=len(before.sessions),
        after_count=len(after.sessions),
        new_sessions=new_sessions,
        updated_sessions=updated_sessions,
        unchanged_sessions=unchanged_sessions,
    )


def scan_usage(
    harness: str,
    *,
    source_path: Path | None = None,
    source_session_id: str | None = None,
    include_raw_json: bool = False,
) -> ScanUsageResult:
    definition = _get_harness(harness)
    resolved = _validate_source_path(
        definition.name,
        resolve_source_path(definition.name, source_path),
        explicit_source=source_path,
        allow_missing_default=True,
    )
    if resolved is None:
        return ScanUsageResult(
            harness=definition.name,
            source_path=Path("."),
            source_session_id=source_session_id,
            rows_seen=0,
            rows_skipped=0,
            events=(),
            files_seen=0,
        )
    scan = definition.scan(
        resolved,
        source_session_id=source_session_id,
        include_raw_json=include_raw_json,
    )
    return ScanUsageResult(
        harness=definition.name,
        source_path=scan.source_path,
        source_session_id=source_session_id,
        rows_seen=scan.rows_seen,
        rows_skipped=scan.rows_skipped,
        events=tuple(
            _to_public_usage_event(event, include_raw_json=include_raw_json)
            for event in scan.events
        ),
        files_seen=scan.files_seen,
    )


def _source_summary_identity(summary: SourceSessionSummary) -> tuple[object, ...]:
    return (
        summary.tokens.input,
        summary.tokens.output,
        summary.tokens.reasoning,
        summary.tokens.cache_read,
        summary.tokens.cache_write,
        summary.assistant_message_count,
        summary.first_created_ms,
        summary.last_created_ms,
        summary.models,
        summary.providers,
        summary.source_paths,
        summary.source_cost_usd,
        summary.actual_cost_usd,
        summary.virtual_cost_usd,
        summary.savings_usd,
        summary.unpriced_count,
    )


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
    if sort == "first":
        return sorted(
            summaries,
            key=lambda summary: (summary.first_created_ms, summary.source_session_id),
            reverse=True,
        )
    if sort == "messages":
        return sorted(
            summaries,
            key=lambda summary: (
                summary.assistant_message_count,
                summary.last_created_ms,
                summary.source_session_id,
            ),
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
    msg = (
        "Unsupported sort. Use last, first, messages, tokens, "
        "actual, virtual, or savings."
    )
    raise InvalidAPIUsageError(msg)


__all__ = [
    "capture_source_snapshot",
    "diff_source_snapshots",
    "list_source_sessions",
    "scan_usage",
]
