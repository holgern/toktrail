from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from toktrail.adapters._common import (
    as_non_empty_str,
    file_modified_timestamp_ms,
    json_object_or_none,
    merge_source_session_metadata,
    parse_rfc3339_ms,
)
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
from toktrail.config import CostingConfig, normalize_identity
from toktrail.models import TokenBreakdown, UsageEvent, normalize_thinking_level

HARNESSBRIDGE_SOURCE = "harnessbridge"
HARNESSBRIDGE_PARSER_VERSION = 1

HarnessbridgeScanResult = ScanResult
HarnessbridgeSessionSummary = SourceSessionSummary


@dataclass(frozen=True)
class _SessionHeader:
    session_id: str | None = None
    harness: str | None = None
    accounting: str | None = None
    started_ms: int | None = None
    provider_id: str | None = None
    model_id: str | None = None
    cwd: str | None = None
    source_dir: str | None = None
    git_root: str | None = None
    git_remote: str | None = None
    session_title: str | None = None


def scan_harnessbridge_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: ImportScanState | None = None,
) -> HarnessbridgeScanResult:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return HarnessbridgeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    if resolved_path.is_file():
        return scan_harnessbridge_file(
            resolved_path,
            source_session_id=source_session_id,
            include_raw_json=include_raw_json,
            since_ms=since_ms,
            import_state=import_state,
        )

    file_paths = sorted(
        path for path in resolved_path.rglob("*.jsonl") if path.is_file()
    )
    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    metadata_by_key: dict[tuple[str, str], SourceSessionMetadata] = {}
    file_states = []
    for file_path in file_paths:
        scan = scan_harnessbridge_file(
            file_path,
            source_session_id=source_session_id,
            include_raw_json=include_raw_json,
            since_ms=since_ms,
            import_state=import_state,
            source_root=resolved_path,
        )
        rows_seen += scan.rows_seen
        rows_skipped += scan.rows_skipped
        events.extend(scan.events)
        file_states.extend(scan.file_states)
        for metadata in scan.session_metadata:
            key = (metadata.harness, metadata.source_session_id)
            metadata_by_key[key] = _merge_session_metadata(
                metadata_by_key.get(key),
                metadata,
            )

    return HarnessbridgeScanResult(
        source_path=resolved_path,
        files_seen=len(file_paths),
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
        file_states=tuple(file_states),
        discovered_file_paths=tuple(str(path) for path in file_paths),
        session_metadata=tuple(metadata_by_key.values()),
    )


def scan_harnessbridge_file(
    file_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: ImportScanState | None = None,
    source_root: Path | None = None,
) -> HarnessbridgeScanResult:
    resolved_path = file_path.expanduser()
    decision = decide_file_scan(
        resolved_path,
        parser_version=HARNESSBRIDGE_PARSER_VERSION,
        import_state=import_state,
        allow_resume=True,
    )
    if decision is None:
        return HarnessbridgeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )
    if decision.mode == "skip":
        return HarnessbridgeScanResult(
            source_path=resolved_path,
            files_seen=1,
            rows_seen=0,
            rows_skipped=0,
            events=[],
            discovered_file_paths=(str(resolved_path),),
        )

    fallback_timestamp = _file_modified_timestamp_ms(resolved_path)
    header, line_number_offset = _harnessbridge_resume_state(decision.prior_state)
    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    metadata_by_key: dict[tuple[str, str], SourceSessionMetadata] = {}
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

                rows_seen += 1
                row = _json_loads(trimmed)
                if row is None:
                    rows_skipped += 1
                    if not raw_line.endswith(b"\n"):
                        break
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue

                row_type = _as_str(row.get("type"))
                if row_type == "session":
                    header = _merge_session_header(row, header)
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue
                if row_type != "usage":
                    rows_skipped += 1
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue

                event = _parse_usage_row(
                    file_path=resolved_path,
                    line_number=line_number,
                    row=row,
                    header=header,
                    fallback_timestamp=fallback_timestamp,
                    include_raw_json=include_raw_json,
                    raw_json=trimmed,
                )
                if event is None:
                    rows_skipped += 1
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue
                if (
                    source_session_id is not None
                    and event.source_session_id != source_session_id
                ):
                    rows_skipped += 1
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue
                if since_ms is not None and event.created_ms < since_ms:
                    rows_skipped += 1
                    last_committed_offset = line_end
                    last_committed_line_number = line_number
                    continue
                events.append(event)
                key = (event.harness, event.source_session_id)
                metadata_by_key[key] = _merge_session_metadata(
                    metadata_by_key.get(key),
                    _build_session_metadata_from_event(
                        file_path=resolved_path,
                        header=header,
                        event=event,
                    ),
                )
                last_committed_offset = line_end
                last_committed_line_number = line_number
    except OSError:
        return HarnessbridgeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    if (
        not metadata_by_key
        and header.session_id is not None
        and (source_session_id is None or header.session_id == source_session_id)
    ):
        fallback_harness = header.harness or HARNESSBRIDGE_SOURCE
        key = (fallback_harness, header.session_id)
        metadata_by_key[key] = SourceSessionMetadata(
            harness=fallback_harness,
            source_session_id=header.session_id,
            source_paths=(str(resolved_path),),
            cwd=header.cwd,
            source_dir=header.source_dir or header.cwd or str(resolved_path.parent),
            git_root=header.git_root,
            git_remote=header.git_remote,
            session_title=header.session_title,
            started_ms=header.started_ms,
            last_seen_ms=header.started_ms,
        )

    return HarnessbridgeScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
        file_states=(
            build_import_source_file_state(
                harness=HARNESSBRIDGE_SOURCE,
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
                parser_version=HARNESSBRIDGE_PARSER_VERSION,
                parser_state_json=_harnessbridge_file_state_json(
                    header=header,
                    last_line_number=last_committed_line_number,
                ),
            ),
        ),
        discovered_file_paths=(str(resolved_path),),
        session_metadata=tuple(metadata_by_key.values()),
    )


def parse_harnessbridge_file(path: Path) -> list[UsageEvent]:
    return scan_harnessbridge_file(path).events


def parse_harnessbridge_path(path: Path) -> list[UsageEvent]:
    return scan_harnessbridge_path(path).events


def list_harnessbridge_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[HarnessbridgeSessionSummary]:
    scan = scan_harnessbridge_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        HARNESSBRIDGE_SOURCE,
        scan.events,
        source_paths_by_session=_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


def _merge_session_header(
    row: dict[str, object],
    previous: _SessionHeader,
) -> _SessionHeader:
    cwd = _first_non_empty_str(
        row,
        "cwd",
        "working_directory",
        "current_working_directory",
    )
    source_dir = _first_non_empty_str(
        row,
        "source_dir",
        "directory",
        "project_dir",
        "workspace_dir",
    )
    git_remote = _first_non_empty_str(row, "git_remote", "remote_url")
    return _SessionHeader(
        session_id=_as_str(row.get("id")) or previous.session_id,
        harness=_normalized_identity(row.get("harness")) or previous.harness,
        accounting=_accounting_mode(row.get("accounting")) or previous.accounting,
        started_ms=_timestamp_ms_from_value(row.get("started_ms"))
        or _parse_rfc3339_ms(row.get("started_at"))
        or previous.started_ms,
        provider_id=(
            _normalized_identity(row.get("provider_id"))
            or _normalized_identity(row.get("provider"))
            or previous.provider_id
        ),
        model_id=(
            _as_str(row.get("model_id"))
            or _as_str(row.get("model"))
            or previous.model_id
        ),
        cwd=cwd or previous.cwd,
        source_dir=source_dir or previous.source_dir,
        git_root=_first_non_empty_str(row, "git_root", "repository_root", "repo_root")
        or previous.git_root,
        git_remote=git_remote or previous.git_remote,
        session_title=(
            _first_non_empty_str(row, "session_title", "title", "name")
            or previous.session_title
        ),
    )


def _parse_usage_row(
    *,
    file_path: Path,
    line_number: int,
    row: dict[str, object],
    header: _SessionHeader,
    fallback_timestamp: int,
    include_raw_json: bool,
    raw_json: str,
) -> UsageEvent | None:
    if _accounting_mode(row.get("accounting")) == "mirror":
        return None
    if row.get("accounting") is None and header.accounting == "mirror":
        return None

    event_harness = _normalized_identity(row.get("harness")) or header.harness
    if event_harness is None:
        return None

    tokens = _token_breakdown(row.get("tokens"))
    if tokens.accounting_total == 0:
        return None

    resolved_source_session_id = (
        _as_str(row.get("source_session_id"))
        or _as_str(row.get("session_id"))
        or header.session_id
        or file_path.stem
    )
    source_message_id = _as_str(row.get("source_message_id"))
    source_row_id = f"{file_path.as_posix()}:{line_number}"
    source_dedup_key = (
        _as_str(row.get("source_dedup_key"))
        or source_message_id
        or _as_str(row.get("id"))
        or source_row_id
    )
    explicit_global_dedup_key = row.get("global_dedup_key") or row.get("dedup_key")
    created_ms = (
        _timestamp_ms_from_value(row.get("created_ms"))
        or _parse_rfc3339_ms(row.get("created_at"))
        or _parse_rfc3339_ms(row.get("timestamp"))
        or header.started_ms
        or fallback_timestamp
    )
    completed_ms = (
        _timestamp_ms_from_value(row.get("completed_ms"))
        or _parse_rfc3339_ms(row.get("completed_at"))
        or _parse_rfc3339_ms(row.get("timestamp"))
    )
    if completed_ms is not None and completed_ms < created_ms:
        completed_ms = None

    provider_id = (
        _normalized_identity(row.get("provider_id"))
        or _normalized_identity(row.get("provider"))
        or header.provider_id
        or "unknown"
    )
    model_id = (
        _as_str(row.get("model_id"))
        or _as_str(row.get("model"))
        or header.model_id
        or "unknown"
    )
    model_id = _strip_provider_prefix(model_id, provider_id)

    event = UsageEvent(
        harness=event_harness,
        source_session_id=resolved_source_session_id,
        source_row_id=source_row_id,
        source_message_id=source_message_id,
        source_dedup_key=source_dedup_key,
        global_dedup_key=_global_dedup_key(
            explicit_global_dedup_key,
            harness=event_harness,
            source_session_id=resolved_source_session_id,
            source_dedup_key=source_dedup_key,
        ),
        fingerprint_hash="",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=normalize_thinking_level(row.get("thinking_level")),
        agent=_normalized_identity(row.get("agent")),
        created_ms=created_ms,
        completed_ms=completed_ms,
        tokens=tokens,
        source_cost_usd=_source_cost(row),
        raw_json=raw_json if include_raw_json else None,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _source_paths_by_session(source_path: Path) -> dict[str, list[Path]]:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return {}

    file_paths = (
        [resolved_path]
        if resolved_path.is_file()
        else sorted(path for path in resolved_path.rglob("*.jsonl") if path.is_file())
    )
    grouped: dict[str, list[Path]] = {}
    for file_path in file_paths:
        scan = scan_harnessbridge_file(file_path, include_raw_json=False)
        for event in scan.events:
            grouped.setdefault(event.source_session_id, []).append(file_path)
    return grouped


def _build_session_metadata_from_event(
    *,
    file_path: Path,
    header: _SessionHeader,
    event: UsageEvent,
) -> SourceSessionMetadata:
    source_dir = (
        header.source_dir or header.cwd or header.git_root or str(file_path.parent)
    )
    return SourceSessionMetadata(
        harness=event.harness,
        source_session_id=event.source_session_id,
        source_paths=(str(file_path),),
        cwd=header.cwd,
        source_dir=source_dir,
        git_root=header.git_root,
        git_remote=header.git_remote,
        session_title=header.session_title,
        started_ms=header.started_ms,
        last_seen_ms=event.created_ms,
    )


def _harnessbridge_resume_state(
    file_state: ImportSourceFileState | None,
) -> tuple[_SessionHeader, int]:
    if file_state is None or file_state.parser_state_json is None:
        return _SessionHeader(), 0
    try:
        payload = json.loads(file_state.parser_state_json)
    except json.JSONDecodeError:
        return _SessionHeader(), 0
    if not isinstance(payload, dict):
        return _SessionHeader(), 0
    return (
        _SessionHeader(
            session_id=_as_str(payload.get("session_id")),
            harness=_normalized_identity(payload.get("harness")),
            accounting=_accounting_mode(payload.get("accounting")),
            started_ms=_timestamp_ms_from_value(payload.get("started_ms")),
            provider_id=_normalized_identity(payload.get("provider_id")),
            model_id=_as_str(payload.get("model_id")),
            cwd=_as_str(payload.get("cwd")),
            source_dir=_as_str(payload.get("source_dir")),
            git_root=_as_str(payload.get("git_root")),
            git_remote=_as_str(payload.get("git_remote")),
            session_title=_as_str(payload.get("session_title")),
        ),
        _as_non_negative_int(payload.get("last_line_number"), 0),
    )


def _harnessbridge_file_state_json(
    *,
    header: _SessionHeader,
    last_line_number: int,
) -> str:
    return json.dumps(
        {
            "session_id": header.session_id,
            "harness": header.harness,
            "accounting": header.accounting,
            "started_ms": header.started_ms,
            "provider_id": header.provider_id,
            "model_id": header.model_id,
            "cwd": header.cwd,
            "source_dir": header.source_dir,
            "git_root": header.git_root,
            "git_remote": header.git_remote,
            "session_title": header.session_title,
            "last_line_number": last_line_number,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _merge_session_metadata(
    current: SourceSessionMetadata | None,
    incoming: SourceSessionMetadata,
) -> SourceSessionMetadata:
    return merge_source_session_metadata(current, incoming)


def _global_dedup_key(
    value: object,
    *,
    harness: str,
    source_session_id: str,
    source_dedup_key: str,
) -> str:
    explicit = _as_str(value)
    if explicit is None:
        return (
            f"{HARNESSBRIDGE_SOURCE}:{harness}:{source_session_id}:{source_dedup_key}"
        )
    if explicit.startswith(f"{HARNESSBRIDGE_SOURCE}:"):
        return explicit
    return f"{HARNESSBRIDGE_SOURCE}:{explicit}"


def _strip_provider_prefix(model_id: str, provider_id: str) -> str:
    prefix = f"{provider_id}/"
    if model_id.lower().startswith(prefix.lower()):
        stripped = model_id[len(prefix) :]
        return stripped or model_id
    return model_id


def _token_breakdown(value: object) -> TokenBreakdown:
    mapping = _as_mapping(value) or {}
    return TokenBreakdown(
        input=_as_non_negative_int(mapping.get("input")),
        output=_as_non_negative_int(mapping.get("output")),
        reasoning=_as_non_negative_int(mapping.get("reasoning")),
        cache_read=_first_non_negative_int(mapping, "cache_read", "cacheRead"),
        cache_write=_first_non_negative_int(mapping, "cache_write", "cacheWrite"),
        cache_output=_first_non_negative_int(
            mapping,
            "cache_output",
            "cacheOutput",
        ),
    )


def _source_cost(row: Mapping[str, object]) -> Decimal:
    direct = _as_non_negative_decimal(row.get("source_cost_usd"))
    if direct > 0:
        return direct

    cost = _as_mapping(row.get("cost"))
    if cost is not None:
        nested = _as_non_negative_decimal(cost.get("total"))
        if nested > 0:
            return nested

    raw = _as_mapping(row.get("raw"))
    native_usage = _as_mapping(raw.get("native_usage")) if raw is not None else None
    nested_cost = (
        _as_mapping(native_usage.get("cost")) if native_usage is not None else None
    )
    if nested_cost is not None:
        return _as_non_negative_decimal(nested_cost.get("total"))
    return Decimal(0)


def _accounting_mode(value: object) -> str | None:
    normalized = _normalized_identity(value)
    if normalized in {"primary", "mirror"}:
        return normalized
    return None


def _json_loads(data_json: str) -> dict[str, object] | None:
    return json_object_or_none(data_json)


def _as_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return value


def _as_str(value: object) -> str | None:
    return as_non_empty_str(value)


def _first_non_empty_str(mapping: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = _as_str(mapping.get(key))
        if value is not None:
            return value
    return None


def _normalized_identity(value: object) -> str | None:
    raw = _as_str(value)
    if raw is None:
        return None
    try:
        return normalize_identity(raw)
    except ValueError:
        return None


def _first_non_negative_int(mapping: Mapping[str, object], *keys: str) -> int:
    for key in keys:
        value = _as_non_negative_int(mapping.get(key))
        if value:
            return value
    return 0


def _as_non_negative_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    numeric = float(value)
    if not math.isfinite(numeric):
        return default
    return max(int(numeric), 0)


def _as_non_negative_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        return Decimal(0)
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    if numeric < 0:
        return Decimal(0)
    return numeric


def _parse_rfc3339_ms(value: object) -> int | None:
    return parse_rfc3339_ms(value)


def _timestamp_ms_from_value(value: object) -> int | None:
    if isinstance(value, str):
        parsed = _as_non_negative_decimal(value)
        return int(parsed) if parsed > 0 else _parse_rfc3339_ms(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    if abs(numeric) >= 10_000_000_000:
        return max(int(numeric), 0)
    return max(int(numeric * 1000), 0)


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


def _file_modified_timestamp_ms(path: Path) -> int:
    return file_modified_timestamp_ms(path)


def _make_fingerprint(event: UsageEvent) -> str:
    payload = {
        "source": HARNESSBRIDGE_SOURCE,
        "harness": event.harness,
        "source_session_id": event.source_session_id,
        "source_dedup_key": event.source_dedup_key,
        "created_ms": event.created_ms,
        "completed_ms": event.completed_ms,
        "provider_id": event.provider_id,
        "model_id": event.model_id,
        "thinking_level": event.thinking_level,
        "agent": event.agent,
        "input": event.tokens.input,
        "output": event.tokens.output,
        "reasoning": event.tokens.reasoning,
        "cache_read": event.tokens.cache_read,
        "cache_write": event.tokens.cache_write,
        "cache_output": event.tokens.cache_output,
        "source_cost_usd": str(event.source_cost_usd),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "HARNESSBRIDGE_PARSER_VERSION",
    "HARNESSBRIDGE_SOURCE",
    "HarnessbridgeScanResult",
    "HarnessbridgeSessionSummary",
    "list_harnessbridge_sessions",
    "parse_harnessbridge_file",
    "parse_harnessbridge_path",
    "scan_harnessbridge_file",
    "scan_harnessbridge_path",
]
