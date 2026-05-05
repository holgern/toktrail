from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig
from toktrail.models import TokenBreakdown, UsageEvent, normalize_thinking_level
from toktrail.provider_identity import inferred_provider_from_model

COPILOT_HARNESS = "copilot"
COPILOT_PARSER_VERSION = 1

CopilotScanResult = ScanResult


def scan_copilot_file(
    path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> CopilotScanResult:
    resolved_path = path.expanduser()
    if not resolved_path.exists():
        return CopilotScanResult(
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

    try:
        with resolved_path.open("r", encoding="utf-8", errors="replace") as file:
            for line_number, line in enumerate(file, start=1):
                trimmed = line.strip()
                if not trimmed:
                    continue

                rows_seen += 1
                event = _parse_copilot_line(
                    trimmed,
                    line_number=line_number,
                    fallback_timestamp=fallback_timestamp,
                    include_raw_json=include_raw_json,
                )
                if event is None:
                    rows_skipped += 1
                    continue
                if (
                    source_session_id is not None
                    and event.source_session_id != source_session_id
                ):
                    rows_skipped += 1
                    continue
                events.append(event)
    except OSError:
        return CopilotScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    return CopilotScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def scan_copilot_path(
    path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> CopilotScanResult:
    resolved_path = path.expanduser()
    if not resolved_path.exists():
        return CopilotScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    if resolved_path.is_file():
        return scan_copilot_file(
            resolved_path,
            source_session_id=source_session_id,
            include_raw_json=include_raw_json,
        )

    file_paths = sorted(resolved_path.rglob("*.jsonl"))
    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    for file_path in file_paths:
        scan = scan_copilot_file(
            file_path,
            source_session_id=source_session_id,
            include_raw_json=include_raw_json,
        )
        rows_seen += scan.rows_seen
        rows_skipped += scan.rows_skipped
        events.extend(scan.events)

    return CopilotScanResult(
        source_path=resolved_path,
        files_seen=len(file_paths),
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def parse_copilot_file(path: Path) -> list[UsageEvent]:
    return scan_copilot_file(path).events


def list_copilot_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[SourceSessionSummary]:
    scan = scan_copilot_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        COPILOT_HARNESS,
        scan.events,
        source_paths_by_session=_copilot_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


def _parse_copilot_line(
    line: str,
    *,
    line_number: int,
    fallback_timestamp: int,
    include_raw_json: bool,
) -> UsageEvent | None:
    try:
        span = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(span, dict) or not _is_chat_span(span):
        return None

    attributes = span.get("attributes")
    if not isinstance(attributes, dict):
        return None

    input_tokens = _attr_int(attributes, "gen_ai.usage.input_tokens")
    output_tokens = _attr_int(attributes, "gen_ai.usage.output_tokens")
    cache_read = _attr_int(attributes, "gen_ai.usage.cache_read.input_tokens")
    cache_write = _attr_int(attributes, "gen_ai.usage.cache_write.input_tokens")
    reasoning = _attr_int(attributes, "gen_ai.usage.reasoning.output_tokens")

    tokens = _normalize_input_tokens(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        reasoning=reasoning,
    )
    if tokens.total == 0:
        return None

    model = (
        _first_non_empty_attr(
            attributes,
            ["gen_ai.response.model", "gen_ai.request.model"],
        )
        or "unknown"
    )
    provider_id = inferred_provider_from_model(model) or "github-copilot"
    thinking_level = normalize_thinking_level(
        _first_non_empty_attr(
            attributes,
            [
                "gen_ai.request.reasoning_effort",
                "gen_ai.openai.request.reasoning_effort",
                "gen_ai.request.thinking_level",
                "github.copilot.chat.reasoning_effort",
                "github.copilot.chat.thinking_level",
            ],
        )
    )

    trace_id = _as_str(span.get("traceId")) or "unknown-trace"
    span_id = _as_str(span.get("spanId")) or "unknown-span"
    dedup_key = f"{trace_id}:{span_id}"

    session_id = (
        _first_non_empty_attr(
            attributes,
            [
                "gen_ai.conversation.id",
                "github.copilot.interaction_id",
                "gen_ai.response.id",
            ],
        )
        or trace_id
    )

    end_time_ms = _timestamp_ms_from_value(span.get("endTime"))
    start_time_ms = _timestamp_ms_from_value(span.get("startTime"))
    timestamp_ms = end_time_ms or start_time_ms or fallback_timestamp

    response_id = _first_non_empty_attr(
        attributes,
        ["gen_ai.response.id", "github.copilot.interaction_id"],
    )

    event = UsageEvent(
        harness=COPILOT_HARNESS,
        source_session_id=session_id,
        source_row_id=str(line_number),
        source_message_id=response_id or span_id,
        source_dedup_key=dedup_key,
        global_dedup_key=f"{COPILOT_HARNESS}:{dedup_key}",
        fingerprint_hash="",
        provider_id=provider_id,
        model_id=model,
        thinking_level=thinking_level,
        agent=None,
        created_ms=timestamp_ms,
        completed_ms=end_time_ms,
        tokens=tokens,
        source_cost_usd=Decimal(0),
        raw_json=line if include_raw_json else None,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _is_chat_span(value: dict[str, Any]) -> bool:
    if value.get("type") != "span":
        return False

    attributes = value.get("attributes")
    if isinstance(attributes, dict):
        operation = attributes.get("gen_ai.operation.name")
        if operation == "chat":
            return True

    name = value.get("name")
    return isinstance(name, str) and name.startswith("chat ")


def _attr_int(attributes: dict[str, Any], key: str) -> int:
    parsed = _value_as_int(attributes.get(key))
    return max(parsed or 0, 0)


def _normalize_input_tokens(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
    cache_output: int = 0,
    reasoning: int,
) -> TokenBreakdown:
    cache_read_for_input = min(max(cache_read, 0), max(input_tokens, 0))
    return TokenBreakdown(
        input=max(input_tokens - cache_read_for_input, 0),
        output=max(output_tokens, 0),
        cache_read=max(cache_read, 0),
        cache_write=max(cache_write, 0),
        cache_output=max(cache_output, 0),
        reasoning=max(reasoning, 0),
    )


def _first_non_empty_attr(attributes: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = _as_str(attributes.get(key))
        if value is not None:
            return value
    return None


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _value_as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _timestamp_ms_from_value(value: object) -> int | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    seconds = _value_as_int(value[0])
    nanos = _value_as_int(value[1])
    if seconds is None or nanos is None:
        return None
    return seconds * 1000 + nanos // 1_000_000


def _file_modified_timestamp_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def _make_fingerprint(event: UsageEvent) -> str:
    payload = {
        "source_dedup_key": event.source_dedup_key,
        "source_session_id": event.source_session_id,
        "created_ms": event.created_ms,
        "completed_ms": event.completed_ms,
        "model_id": event.model_id,
        "provider_id": event.provider_id,
        "input": event.tokens.input,
        "output": event.tokens.output,
        "reasoning": event.tokens.reasoning,
        "cache_read": event.tokens.cache_read,
        "cache_write": event.tokens.cache_write,
        "thinking_level": event.thinking_level,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _copilot_source_paths_by_session(source_path: Path) -> dict[str, list[Path]]:
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
        scan = scan_copilot_file(file_path, include_raw_json=False)
        for event in scan.events:
            grouped.setdefault(event.source_session_id, []).append(file_path)
    return grouped
