from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from toktrail.models import TokenBreakdown, UsageEvent

PI_HARNESS = "pi"


@dataclass(frozen=True)
class PiScanResult:
    source_path: Path
    files_seen: int
    rows_seen: int
    rows_skipped: int
    events: list[UsageEvent]


@dataclass(frozen=True)
class PiSessionSummary:
    source_session_id: str
    first_created_ms: int
    last_created_ms: int
    assistant_message_count: int
    tokens: TokenBreakdown
    cost_usd: float


def scan_pi_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
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
    for file_path in file_paths:
        scan = scan_pi_file(file_path, include_raw_json=include_raw_json)
        rows_seen += scan.rows_seen
        rows_skipped += scan.rows_skipped
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
    )


def scan_pi_file(
    file_path: Path,
    *,
    include_raw_json: bool = True,
) -> PiScanResult:
    resolved_path = file_path.expanduser()
    if not resolved_path.exists() or not resolved_path.is_file():
        return PiScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    fallback_timestamp = _file_modified_timestamp_ms(resolved_path)
    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    session_id: str | None = None

    try:
        with resolved_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                trimmed = line.strip()
                if not trimmed:
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
                        )
                    header_id = _as_str(header.get("id"))
                    if header_id is None:
                        return PiScanResult(
                            source_path=resolved_path,
                            files_seen=1,
                            rows_seen=rows_seen,
                            rows_skipped=rows_seen,
                            events=[],
                        )
                    session_id = header_id
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
                    continue
                events.append(event)
    except OSError:
        return PiScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    return PiScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def parse_pi_file(path: Path) -> list[UsageEvent]:
    return scan_pi_file(path).events


def parse_pi_path(path: Path) -> list[UsageEvent]:
    return scan_pi_path(path).events


def list_pi_sessions(source_path: Path) -> list[PiSessionSummary]:
    scan = scan_pi_path(source_path, include_raw_json=False)
    grouped: dict[str, PiSessionSummary] = {}
    for event in scan.events:
        existing = grouped.get(event.source_session_id)
        if existing is None:
            grouped[event.source_session_id] = PiSessionSummary(
                source_session_id=event.source_session_id,
                first_created_ms=event.created_ms,
                last_created_ms=event.created_ms,
                assistant_message_count=1,
                tokens=event.tokens,
                cost_usd=event.cost_usd,
            )
            continue

        grouped[event.source_session_id] = PiSessionSummary(
            source_session_id=existing.source_session_id,
            first_created_ms=min(existing.first_created_ms, event.created_ms),
            last_created_ms=max(existing.last_created_ms, event.created_ms),
            assistant_message_count=existing.assistant_message_count + 1,
            tokens=TokenBreakdown(
                input=existing.tokens.input + event.tokens.input,
                output=existing.tokens.output + event.tokens.output,
                reasoning=existing.tokens.reasoning + event.tokens.reasoning,
                cache_read=existing.tokens.cache_read + event.tokens.cache_read,
                cache_write=existing.tokens.cache_write + event.tokens.cache_write,
            ),
            cost_usd=existing.cost_usd + event.cost_usd,
        )

    return sorted(
        grouped.values(),
        key=lambda summary: (summary.last_created_ms, summary.source_session_id),
        reverse=True,
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
        agent=None,
        created_ms=created_ms,
        completed_ms=None,
        tokens=token_breakdown,
        cost_usd=0.0,
        raw_json=line_json if include_raw_json else None,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


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
        "cost_usd": event.cost_usd,
        "agent": event.agent,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
