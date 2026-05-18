from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.base import (
    ImportScanState,
    ImportSourceFileState,
    ScanResult,
    SourceSessionMetadata,
    SourceSessionSummary,
    build_import_source_file_state,
    decide_file_scan,
)
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig
from toktrail.models import TokenBreakdown, UsageEvent, normalize_thinking_level

PI_HARNESS = "pi"
PI_PARSER_VERSION = 1

PiScanResult = ScanResult
PiSessionSummary = SourceSessionSummary


def scan_pi_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: ImportScanState | None = None,
) -> PiScanResult:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return PiScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    file_paths = (
        [resolved_path]
        if resolved_path.is_file()
        else sorted(resolved_path.rglob("*.jsonl"))
    )

    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    metadata_by_key: dict[tuple[str, str], SourceSessionMetadata] = {}
    file_states = []
    for file_path in file_paths:
        scan = scan_pi_file(
            file_path,
            include_raw_json=include_raw_json,
            since_ms=since_ms,
            import_state=import_state,
            source_root=resolved_path if resolved_path.is_dir() else None,
        )
        rows_seen += scan.rows_seen
        rows_skipped += scan.rows_skipped
        file_states.extend(scan.file_states)
        for metadata in scan.session_metadata:
            key = (metadata.harness, metadata.source_session_id)
            metadata_by_key[key] = _merge_session_metadata(
                metadata_by_key.get(key),
                metadata,
            )
        if source_session_id is None:
            events.extend(scan.events)
            continue

        kept = [
            event
            for event in scan.events
            if event.source_session_id == source_session_id
        ]
        rows_skipped += len(scan.events) - len(kept)
        events.extend(kept)

    return PiScanResult(
        source_path=resolved_path,
        files_seen=len(file_paths),
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
        file_states=tuple(file_states),
        discovered_file_paths=tuple(str(path) for path in file_paths),
        session_metadata=tuple(
            metadata
            for metadata in metadata_by_key.values()
            if source_session_id is None
            or metadata.source_session_id == source_session_id
        ),
    )


def scan_pi_file(
    file_path: Path,
    *,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: ImportScanState | None = None,
    source_root: Path | None = None,
) -> PiScanResult:
    resolved_path = file_path.expanduser()
    decision = decide_file_scan(
        resolved_path,
        parser_version=PI_PARSER_VERSION,
        import_state=import_state,
        allow_resume=True,
    )
    if decision is None:
        return PiScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )
    if decision.mode == "skip":
        return PiScanResult(
            source_path=resolved_path,
            files_seen=1,
            rows_seen=0,
            rows_skipped=0,
            events=[],
            discovered_file_paths=(str(resolved_path),),
        )

    fallback_timestamp = _file_modified_timestamp_ms(resolved_path)
    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    session_id, session_header, line_number_offset = _pi_resume_state(
        decision.prior_state
    )
    start_offset = (
        decision.prior_state.last_file_offset
        if decision.mode == "resume" and decision.prior_state is not None
        else 0
    )
    last_committed_offset = start_offset
    last_committed_line_number = line_number_offset

    try:
        with resolved_path.open("rb") as handle:
            if start_offset > 0:
                handle.seek(start_offset)
            line_number = line_number_offset
            while True:
                raw_line = handle.readline()
                if raw_line == b"":
                    break
                line_number += 1
                line_end = handle.tell()
                line = raw_line.decode("utf-8", errors="replace")
                trimmed = line.strip()
                if not trimmed:
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue

                if session_id is None:
                    rows_seen += 1
                    header = _json_loads(trimmed)
                    if header is None or _as_str(header.get("type")) != "session":
                        return PiScanResult(
                            source_path=resolved_path,
                            files_seen=1,
                            rows_seen=rows_seen,
                            rows_skipped=rows_seen,
                            events=[],
                            discovered_file_paths=(str(resolved_path),),
                        )
                    header_id = _as_str(header.get("id"))
                    if header_id is None:
                        return PiScanResult(
                            source_path=resolved_path,
                            files_seen=1,
                            rows_seen=rows_seen,
                            rows_skipped=rows_seen,
                            events=[],
                            discovered_file_paths=(str(resolved_path),),
                        )
                    session_id = header_id
                    session_header = header
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue

                rows_seen += 1
                event = _parse_pi_entry_line(
                    file_path=resolved_path,
                    line_number=line_number,
                    session_id=session_id,
                    line_json=trimmed,
                    fallback_timestamp=fallback_timestamp,
                    include_raw_json=include_raw_json,
                )
                if event is None:
                    rows_skipped += 1
                    if not raw_line.endswith(b"\n"):
                        break
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue
                if since_ms is not None and event.created_ms < since_ms:
                    rows_skipped += 1
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue
                events.append(event)
                last_committed_offset = line_end
                last_committed_line_number = line_number
    except OSError:
        return PiScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    session_metadata: tuple[SourceSessionMetadata, ...] = ()
    if session_id is not None:
        session_metadata = (
            _build_pi_session_metadata(
                session_id=session_id,
                resolved_path=resolved_path,
                header=session_header,
                events=events,
                fallback_timestamp=fallback_timestamp,
            ),
        )

    return PiScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
        file_states=(
            build_import_source_file_state(
                harness=PI_HARNESS,
                source_path=source_root or resolved_path,
                file_path=resolved_path,
                signature=decision.signature,
                last_imported_created_ms=max(
                    (event.created_ms for event in events),
                    default=(
                        decision.prior_state.last_imported_created_ms
                        if decision.prior_state is not None
                        else None
                    ),
                ),
                last_file_offset=last_committed_offset,
                parser_version=PI_PARSER_VERSION,
                parser_state_json=_pi_file_state_json(
                    session_id=session_id,
                    header=session_header,
                    last_line_number=last_committed_line_number,
                ),
            ),
        ),
        discovered_file_paths=(str(resolved_path),),
        session_metadata=session_metadata,
    )


def parse_pi_file(path: Path) -> list[UsageEvent]:
    return scan_pi_file(path).events


def parse_pi_path(path: Path) -> list[UsageEvent]:
    return scan_pi_path(path).events


def list_pi_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[PiSessionSummary]:
    scan = scan_pi_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        PI_HARNESS,
        scan.events,
        source_paths_by_session=_pi_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


def _parse_pi_entry_line(
    *,
    file_path: Path,
    line_number: int,
    session_id: str,
    line_json: str,
    fallback_timestamp: int,
    include_raw_json: bool,
) -> UsageEvent | None:
    entry = _json_loads(line_json)
    if entry is None:
        return None

    if _as_str(entry.get("type")) != "message":
        return None

    message = entry.get("message")
    if not isinstance(message, dict):
        return None

    if _as_str(message.get("role")) != "assistant":
        return None

    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None

    model_id = _as_str(message.get("model"))
    if model_id is None:
        return None

    provider_id = _as_str(message.get("provider"))
    if provider_id is None:
        return None
    thinking_level = _thinking_level(message)

    created_ms = _parse_rfc3339_ms(entry.get("timestamp")) or fallback_timestamp
    source_message_id = _as_str(entry.get("id"))
    source_row_id = f"{file_path.as_posix()}:{line_number}"
    source_dedup_key = source_message_id or source_row_id
    token_breakdown = TokenBreakdown(
        input=_as_non_negative_int(usage.get("input")),
        output=_as_non_negative_int(usage.get("output")),
        reasoning=0,
        cache_read=_as_non_negative_int(usage.get("cacheRead")),
        cache_write=_as_non_negative_int(usage.get("cacheWrite")),
        cache_output=_first_non_negative_int(
            usage,
            "cacheOutput",
            "cachedOutput",
            "cachedOutputTokens",
        ),
    )

    event = UsageEvent(
        harness=PI_HARNESS,
        source_session_id=session_id,
        source_row_id=source_row_id,
        source_message_id=source_message_id,
        source_dedup_key=source_dedup_key,
        global_dedup_key=f"{PI_HARNESS}:{session_id}:{source_dedup_key}",
        fingerprint_hash="",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=thinking_level,
        agent=None,
        created_ms=created_ms,
        completed_ms=None,
        tokens=token_breakdown,
        source_cost_usd=Decimal(0),
        raw_json=line_json if include_raw_json else None,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _pi_source_paths_by_session(source_path: Path) -> dict[str, list[Path]]:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return {}

    file_paths = (
        [resolved_path]
        if resolved_path.is_file()
        else sorted(resolved_path.rglob("*.jsonl"))
    )
    grouped: dict[str, list[Path]] = {}
    for file_path in file_paths:
        scan = scan_pi_file(file_path, include_raw_json=False)
        for event in scan.events:
            grouped.setdefault(event.source_session_id, []).append(file_path)
    return grouped


def _build_pi_session_metadata(
    *,
    session_id: str,
    resolved_path: Path,
    header: dict[str, object] | None,
    events: list[UsageEvent],
    fallback_timestamp: int,
) -> SourceSessionMetadata:
    started_ms = (
        _parse_rfc3339_ms(header.get("timestamp")) if isinstance(header, dict) else None
    )
    if started_ms is None:
        started_ms = min(
            (event.created_ms for event in events),
            default=fallback_timestamp,
        )
    last_seen_ms = max((event.created_ms for event in events), default=started_ms)
    cwd = (
        _first_non_empty_str(
            header,
            "cwd",
            "working_directory",
            "current_working_directory",
            "source_dir",
            "project_dir",
            "workspace_dir",
        )
        if isinstance(header, dict)
        else None
    )
    source_dir = cwd or str(resolved_path.parent)
    return SourceSessionMetadata(
        harness=PI_HARNESS,
        source_session_id=session_id,
        source_paths=(str(resolved_path),),
        cwd=cwd,
        source_dir=source_dir,
        started_ms=started_ms,
        last_seen_ms=last_seen_ms,
    )


def _pi_resume_state(
    file_state: ImportSourceFileState | None,
) -> tuple[str | None, dict[str, object] | None, int]:
    if file_state is None or file_state.parser_state_json is None:
        return None, None, 0
    try:
        payload = json.loads(file_state.parser_state_json)
    except json.JSONDecodeError:
        return None, None, 0
    if not isinstance(payload, dict):
        return None, None, 0
    header = payload.get("header")
    session_header = header if isinstance(header, dict) else None
    return (
        _as_str(payload.get("session_id")),
        session_header,
        _as_non_negative_int(payload.get("last_line_number")),
    )


def _pi_file_state_json(
    *,
    session_id: str | None,
    header: dict[str, object] | None,
    last_line_number: int,
) -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "header": header,
            "last_line_number": last_line_number,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _merge_session_metadata(
    current: SourceSessionMetadata | None,
    incoming: SourceSessionMetadata,
) -> SourceSessionMetadata:
    if current is None:
        return incoming
    merged_paths = tuple(sorted(set(current.source_paths) | set(incoming.source_paths)))
    return SourceSessionMetadata(
        harness=incoming.harness,
        source_session_id=incoming.source_session_id,
        source_paths=merged_paths,
        cwd=current.cwd or incoming.cwd,
        source_dir=current.source_dir or incoming.source_dir,
        git_root=current.git_root or incoming.git_root,
        git_remote=current.git_remote or incoming.git_remote,
        session_title=current.session_title or incoming.session_title,
        started_ms=_min_optional_int(current.started_ms, incoming.started_ms),
        last_seen_ms=_max_optional_int(current.last_seen_ms, incoming.last_seen_ms),
    )


def _file_modified_timestamp_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def _json_loads(data_json: str) -> dict[str, object] | None:
    try:
        value = json.loads(data_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _as_non_negative_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(float(value)):
        return default
    return max(int(value), 0)


def _first_non_negative_int(mapping: dict[str, object], *keys: str) -> int:
    for key in keys:
        value = _as_non_negative_int(mapping.get(key))
        if value:
            return value
    return 0


def _first_non_empty_str(mapping: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = _as_str(mapping.get(key))
        if value is not None:
            return value
    return None


def _parse_rfc3339_ms(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _min_optional_int(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _max_optional_int(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _make_fingerprint(event: UsageEvent) -> str:
    payload = {
        "source_session_id": event.source_session_id,
        "source_dedup_key": event.source_dedup_key,
        "created_ms": event.created_ms,
        "model_id": event.model_id,
        "provider_id": event.provider_id,
        "input": event.tokens.input,
        "output": event.tokens.output,
        "reasoning": event.tokens.reasoning,
        "cache_read": event.tokens.cache_read,
        "cache_write": event.tokens.cache_write,
        "cache_output": event.tokens.cache_output,
        "source_cost_usd": str(event.source_cost_usd),
        "agent": event.agent,
        "thinking_level": event.thinking_level,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _thinking_level(message: dict[str, object]) -> str | None:
    for key in (
        "reasoningEffort",
        "reasoning_effort",
        "thinkingLevel",
        "thinking_level",
    ):
        normalized = normalize_thinking_level(message.get(key))
        if normalized is not None:
            return normalized
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return None
    for key in (
        "reasoningEffort",
        "reasoning_effort",
        "thinkingLevel",
        "thinking_level",
    ):
        normalized = normalize_thinking_level(metadata.get(key))
        if normalized is not None:
            return normalized
    return None
