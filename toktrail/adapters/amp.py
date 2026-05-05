from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.provider_identity import inferred_provider_from_model

AMP_HARNESS = "amp"
AMP_PARSER_VERSION = 1

AmpScanResult = ScanResult
AmpSessionSummary = SourceSessionSummary


@dataclass(frozen=True)
class _AmpUsageRecord:
    model_id: str
    created_ms: int
    has_explicit_timestamp: bool
    record_index: int
    origin: str
    message_id: int | None
    ledger_to_message_id: int | None
    tokens: TokenBreakdown
    source_cost_usd: Decimal
    raw_json: str | None

    def matches_message_usage(self, other: _AmpUsageRecord) -> bool:
        return self.model_id == other.model_id and self.tokens == other.tokens


def scan_amp_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> AmpScanResult:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return AmpScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    file_paths = (
        [resolved_path]
        if resolved_path.is_file()
        else sorted(resolved_path.rglob("*.json"))
    )

    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    for file_path in file_paths:
        scan = scan_amp_file(file_path, include_raw_json=include_raw_json)
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

    return AmpScanResult(
        source_path=resolved_path,
        files_seen=len(file_paths),
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def scan_amp_file(
    file_path: Path,
    *,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> AmpScanResult:
    resolved_path = file_path.expanduser()
    if not resolved_path.exists() or not resolved_path.is_file():
        return AmpScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    try:
        data_json = resolved_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return AmpScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    parsed = _json_loads(data_json)
    if parsed is None:
        return AmpScanResult(
            source_path=resolved_path,
            files_seen=1,
            rows_seen=1,
            rows_skipped=1,
            events=[],
        )

    file_mtime_ms = _file_modified_timestamp_ms(resolved_path)
    events, rows_seen, rows_skipped = _parse_amp_thread(
        resolved_path,
        parsed,
        file_mtime_ms=file_mtime_ms,
        include_raw_json=include_raw_json,
    )
    return AmpScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def parse_amp_file(path: Path) -> list[UsageEvent]:
    return scan_amp_file(path).events


def parse_amp_path(path: Path) -> list[UsageEvent]:
    return scan_amp_path(path).events


def list_amp_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[AmpSessionSummary]:
    scan = scan_amp_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        AMP_HARNESS,
        scan.events,
        source_paths_by_session=_amp_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


def _parse_amp_thread(
    path: Path,
    thread: dict[str, object],
    *,
    file_mtime_ms: int,
    include_raw_json: bool,
) -> tuple[list[UsageEvent], int, int]:
    thread_id = _as_str(thread.get("id")) or path.stem
    thread_created_ms = _as_positive_int(thread.get("created"))
    base_ms = thread_created_ms or file_mtime_ms

    ledger_records, ledger_seen, ledger_skipped = _parse_amp_ledger_records(
        thread,
        base_ms=base_ms,
        include_raw_json=include_raw_json,
    )
    message_records, message_seen, message_skipped = _parse_amp_message_records(
        thread,
        base_ms=base_ms,
        include_raw_json=include_raw_json,
    )

    records: list[_AmpUsageRecord]
    if not ledger_records:
        records = message_records
    else:
        consumed = [False] * len(ledger_records)
        merged_records: list[_AmpUsageRecord] = list(ledger_records)
        for message_record in message_records:
            index = _find_matching_ledger_record(
                message_record,
                ledger_records,
                consumed,
            )
            if index is None:
                merged_records.append(message_record)
                continue
            consumed[index] = True
            merged_records[index] = _merge_amp_records(
                ledger_records[index],
                message_record,
            )
        records = merged_records

    events = [
        _record_to_event(record, thread_id=thread_id, source_path=path)
        for record in sorted(records, key=lambda record: record.created_ms)
        if record.created_ms > 0 and record.tokens.total > 0
    ]
    rows_seen = ledger_seen + message_seen
    rows_skipped = ledger_skipped + message_skipped + len(records) - len(events)
    return events, rows_seen, rows_skipped


def _parse_amp_ledger_records(
    thread: dict[str, object],
    *,
    base_ms: int,
    include_raw_json: bool,
) -> tuple[list[_AmpUsageRecord], int, int]:
    ledger = _as_mapping(thread.get("usageLedger"))
    raw_events = ledger.get("events") if ledger is not None else None
    if not isinstance(raw_events, list):
        return [], 0, 0

    records: list[_AmpUsageRecord] = []
    skipped = 0
    for index, raw_event in enumerate(raw_events):
        event = _as_mapping(raw_event)
        if event is None:
            skipped += 1
            continue
        model_id = _as_str(event.get("model"))
        if model_id is None:
            skipped += 1
            continue
        tokens_raw = _as_mapping(event.get("tokens")) or {}
        tokens = TokenBreakdown(
            input=_as_non_negative_int(tokens_raw.get("input")),
            output=_as_non_negative_int(tokens_raw.get("output")),
            reasoning=0,
            cache_read=_as_non_negative_int(tokens_raw.get("cacheReadInputTokens")),
            cache_write=_as_non_negative_int(
                tokens_raw.get("cacheCreationInputTokens")
            ),
            cache_output=_as_non_negative_int(
                tokens_raw.get("cacheReadOutputTokens")
                or tokens_raw.get("cachedOutputTokens")
            ),
        )
        explicit_ms = _parse_rfc3339_ms(event.get("timestamp"))
        raw_json = (
            json.dumps(
                {
                    "source": "usageLedger.events",
                    "index": index,
                    "event": event,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if include_raw_json
            else None
        )
        records.append(
            _AmpUsageRecord(
                model_id=model_id,
                created_ms=explicit_ms if explicit_ms is not None else base_ms,
                has_explicit_timestamp=explicit_ms is not None,
                record_index=index,
                origin="ledger",
                message_id=None,
                ledger_to_message_id=_as_positive_int(event.get("toMessageId")),
                tokens=tokens,
                source_cost_usd=_as_non_negative_decimal(event.get("credits")),
                raw_json=raw_json,
            )
        )
    return records, len(raw_events), skipped


def _parse_amp_message_records(
    thread: dict[str, object],
    *,
    base_ms: int,
    include_raw_json: bool,
) -> tuple[list[_AmpUsageRecord], int, int]:
    raw_messages = thread.get("messages")
    if not isinstance(raw_messages, list):
        return [], 0, 0

    records: list[_AmpUsageRecord] = []
    skipped = 0
    for index, raw_message in enumerate(raw_messages):
        message = _as_mapping(raw_message)
        if message is None:
            skipped += 1
            continue
        if message.get("role") != "assistant":
            skipped += 1
            continue
        usage = _as_mapping(message.get("usage"))
        if usage is None:
            skipped += 1
            continue
        model_id = _as_str(usage.get("model"))
        if model_id is None:
            skipped += 1
            continue
        message_id = _as_positive_int(message.get("messageId"))
        tokens = TokenBreakdown(
            input=_as_non_negative_int(usage.get("inputTokens")),
            output=_as_non_negative_int(usage.get("outputTokens")),
            reasoning=0,
            cache_read=_as_non_negative_int(usage.get("cacheReadInputTokens")),
            cache_write=_as_non_negative_int(usage.get("cacheCreationInputTokens")),
        )
        offset_ms = (message_id or 0) * 1000
        raw_json = (
            json.dumps(
                {
                    "source": "messages",
                    "index": index,
                    "message": message,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if include_raw_json
            else None
        )
        records.append(
            _AmpUsageRecord(
                model_id=model_id,
                created_ms=base_ms + offset_ms if base_ms > 0 else 0,
                has_explicit_timestamp=False,
                record_index=index,
                origin="message",
                message_id=message_id,
                ledger_to_message_id=None,
                tokens=tokens,
                source_cost_usd=_as_non_negative_decimal(usage.get("credits")),
                raw_json=raw_json,
            )
        )
    return records, len(raw_messages), skipped


def _find_matching_ledger_record(
    message_record: _AmpUsageRecord,
    ledger_records: list[_AmpUsageRecord],
    consumed: list[bool],
) -> int | None:
    for index, ledger_record in enumerate(ledger_records):
        if consumed[index]:
            continue
        if (
            message_record.message_id is not None
            and ledger_record.ledger_to_message_id == message_record.message_id
        ):
            return index

    for index, ledger_record in enumerate(ledger_records):
        if consumed[index]:
            continue
        if ledger_record.matches_message_usage(message_record):
            return index
    return None


def _merge_amp_records(
    ledger_record: _AmpUsageRecord,
    message_record: _AmpUsageRecord,
) -> _AmpUsageRecord:
    created_ms = (
        ledger_record.created_ms
        if ledger_record.has_explicit_timestamp
        else message_record.created_ms
    )
    source_cost_usd = ledger_record.source_cost_usd
    if source_cost_usd == 0 and message_record.source_cost_usd > 0:
        source_cost_usd = message_record.source_cost_usd
    return replace(
        ledger_record,
        created_ms=created_ms,
        message_id=message_record.message_id,
        source_cost_usd=source_cost_usd,
    )


def _record_to_event(
    record: _AmpUsageRecord,
    *,
    thread_id: str,
    source_path: Path,
) -> UsageEvent:
    source_message_id, source_dedup_key = _event_source_identity(record)
    event = UsageEvent(
        harness=AMP_HARNESS,
        source_session_id=thread_id,
        source_row_id=str(source_path),
        source_message_id=source_message_id,
        source_dedup_key=source_dedup_key,
        global_dedup_key=f"{AMP_HARNESS}:{thread_id}:{source_dedup_key}",
        fingerprint_hash="",
        provider_id=_provider_from_model(record.model_id),
        model_id=record.model_id,
        thinking_level=None,
        agent=None,
        created_ms=record.created_ms,
        completed_ms=None,
        tokens=record.tokens,
        source_cost_usd=record.source_cost_usd,
        raw_json=record.raw_json,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _provider_from_model(model_id: str) -> str:
    return inferred_provider_from_model(model_id) or "anthropic"


def _event_source_identity(record: _AmpUsageRecord) -> tuple[str | None, str]:
    if record.message_id is not None:
        source_message_id = str(record.message_id)
        return source_message_id, f"message:{source_message_id}"
    if record.ledger_to_message_id is not None:
        source_message_id = str(record.ledger_to_message_id)
        return source_message_id, f"message:{source_message_id}"
    if record.origin == "ledger":
        return (
            None,
            (
                "ledger:"
                f"{record.record_index}:{record.created_ms}:{record.model_id}:"
                f"{record.tokens.input}:{record.tokens.output}:"
                f"{record.tokens.cache_read}:{record.tokens.cache_write}"
            ),
        )
    return (
        None,
        (
            "message-row:"
            f"{record.record_index}:{record.model_id}:"
            f"{record.tokens.input}:{record.tokens.output}:"
            f"{record.tokens.cache_read}:{record.tokens.cache_write}"
        ),
    )


def _amp_source_paths_by_session(source_path: Path) -> dict[str, list[Path]]:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return {}
    file_paths = (
        [resolved_path]
        if resolved_path.is_file()
        else sorted(resolved_path.rglob("*.json"))
    )
    paths: dict[str, list[Path]] = {}
    for file_path in file_paths:
        try:
            data_json = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parsed = _json_loads(data_json)
        if parsed is None:
            continue
        thread_id = _as_str(parsed.get("id")) or file_path.stem
        paths.setdefault(thread_id, []).append(file_path)
    return paths


def _file_modified_timestamp_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def _json_loads(data_json: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(data_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _as_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return value


def _as_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _as_positive_int(value: object) -> int | None:
    numeric = _number_value(value)
    if numeric is None or numeric <= 0:
        return None
    return int(numeric)


def _as_non_negative_int(value: object) -> int:
    numeric = _number_value(value)
    if numeric is None or numeric < 0:
        return 0
    return int(numeric)


def _as_non_negative_float(value: object) -> float:
    numeric = _number_value(value)
    if numeric is None or numeric < 0:
        return 0.0
    return float(numeric)


def _as_non_negative_decimal(value: object) -> Decimal:
    numeric = _number_value(value)
    if numeric is None or numeric < 0:
        return Decimal(0)
    return Decimal(str(numeric))


def _number_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _parse_rfc3339_ms(value: object) -> int | None:
    raw = _as_str(value)
    if raw is None:
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
        "harness": event.harness,
        "source_session_id": event.source_session_id,
        "source_dedup_key": event.source_dedup_key,
        "provider_id": event.provider_id,
        "model_id": event.model_id,
        "created_ms": event.created_ms,
        "input": event.tokens.input,
        "output": event.tokens.output,
        "reasoning": event.tokens.reasoning,
        "cache_read": event.tokens.cache_read,
        "cache_write": event.tokens.cache_write,
        "source_cost_usd": str(event.source_cost_usd),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
