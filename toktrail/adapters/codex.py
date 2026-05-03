from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig
from toktrail.models import TokenBreakdown, UsageEvent

CODEX_HARNESS = "codex"
CODEX_PARSER_VERSION = 1

CodexScanResult = ScanResult
CodexSessionSummary = SourceSessionSummary


@dataclass(frozen=True)
class _CodexTotals:
    input: int = 0
    output: int = 0
    cached: int = 0
    reasoning: int = 0

    @classmethod
    def from_usage(cls, usage: Mapping[str, object]) -> _CodexTotals:
        return cls(
            input=_as_non_negative_int(usage.get("input_tokens")),
            output=_as_non_negative_int(usage.get("output_tokens")),
            cached=max(
                _as_non_negative_int(usage.get("cached_input_tokens")),
                _as_non_negative_int(usage.get("cache_read_input_tokens")),
            ),
            reasoning=_as_non_negative_int(usage.get("reasoning_output_tokens")),
        )

    def delta_from(self, previous: _CodexTotals) -> _CodexTotals | None:
        if (
            self.input < previous.input
            or self.output < previous.output
            or self.cached < previous.cached
            or self.reasoning < previous.reasoning
        ):
            return None
        return _CodexTotals(
            input=self.input - previous.input,
            output=self.output - previous.output,
            cached=self.cached - previous.cached,
            reasoning=self.reasoning - previous.reasoning,
        )

    def saturating_add(self, other: _CodexTotals) -> _CodexTotals:
        return _CodexTotals(
            input=max(self.input + other.input, 0),
            output=max(self.output + other.output, 0),
            cached=max(self.cached + other.cached, 0),
            reasoning=max(self.reasoning + other.reasoning, 0),
        )

    @property
    def total(self) -> int:
        return self.input + self.output + self.cached + self.reasoning

    def looks_like_stale_regression(
        self,
        previous: _CodexTotals,
        last: _CodexTotals,
    ) -> bool:
        previous_total = previous.total
        current_total = self.total
        last_total = last.total
        if previous_total <= 0 or current_total <= 0 or last_total <= 0:
            return False
        return (
            current_total * 100 >= previous_total * 98
            or current_total + last_total * 2 >= previous_total
        )

    def into_tokens(self) -> TokenBreakdown:
        clamped_cached = min(max(self.cached, 0), max(self.input, 0))
        return TokenBreakdown(
            input=max(self.input - clamped_cached, 0),
            output=max(self.output, 0),
            cache_read=clamped_cached,
            cache_write=0,
            cache_output=0,
            reasoning=max(self.reasoning, 0),
        )


@dataclass
class _CodexParseState:
    current_model: str | None = None
    previous_totals: _CodexTotals | None = None
    session_is_headless: bool = False
    session_provider: str | None = None
    session_agent: str | None = None


def scan_codex_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
) -> CodexScanResult:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return CodexScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    if resolved_path.is_file():
        return scan_codex_file(
            resolved_path,
            source_session_id=source_session_id,
            include_raw_json=include_raw_json,
        )

    file_paths = sorted(
        path for path in resolved_path.rglob("*.jsonl") if path.is_file()
    )
    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    for file_path in file_paths:
        scan = scan_codex_file(file_path, include_raw_json=include_raw_json)
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

    return CodexScanResult(
        source_path=resolved_path,
        files_seen=len(file_paths),
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def scan_codex_file(
    file_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
) -> CodexScanResult:
    resolved_path = file_path.expanduser()
    if not resolved_path.exists() or not resolved_path.is_file():
        return CodexScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    fallback_timestamp = _file_modified_timestamp_ms(resolved_path)
    session_id = _session_id_from_path(resolved_path)
    state = _CodexParseState()
    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []

    try:
        with resolved_path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    rows_seen += 1
                    rows_skipped += 1
                    break

                trimmed = line.strip()
                if not trimmed:
                    continue

                rows_seen += 1
                event, skipped = _parse_codex_line(
                    file_path=resolved_path,
                    line_number=line_number,
                    session_id=session_id,
                    line_json=trimmed,
                    fallback_timestamp=fallback_timestamp,
                    state=state,
                    include_raw_json=include_raw_json,
                )
                if event is None:
                    if skipped:
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
        return CodexScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    return CodexScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def parse_codex_file(path: Path) -> list[UsageEvent]:
    return scan_codex_file(path).events


def parse_codex_path(path: Path) -> list[UsageEvent]:
    return scan_codex_path(path).events


def list_codex_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[CodexSessionSummary]:
    scan = scan_codex_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        CODEX_HARNESS,
        scan.events,
        source_paths_by_session=_codex_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


def _parse_codex_line(
    *,
    file_path: Path,
    line_number: int,
    session_id: str,
    line_json: str,
    fallback_timestamp: int,
    state: _CodexParseState,
    include_raw_json: bool,
) -> tuple[UsageEvent | None, bool]:
    entry = _json_loads(line_json)
    if entry is None:
        return None, True

    if _handle_session_meta(entry, state):
        return None, False
    if _handle_turn_context(entry, state):
        return None, False

    token_count = _parse_structured_token_count(
        entry,
        file_path=file_path,
        line_number=line_number,
        session_id=session_id,
        line_json=line_json,
        fallback_timestamp=fallback_timestamp,
        state=state,
        include_raw_json=include_raw_json,
    )
    if token_count is not None:
        return token_count

    return _parse_headless_usage(
        entry,
        file_path=file_path,
        line_number=line_number,
        session_id=session_id,
        line_json=line_json,
        fallback_timestamp=fallback_timestamp,
        state=state,
        include_raw_json=include_raw_json,
    )


def _handle_session_meta(entry: Mapping[str, object], state: _CodexParseState) -> bool:
    if _as_str(entry.get("type")) != "session_meta":
        return False
    payload = _as_mapping(entry.get("payload"))
    if payload is None:
        return False
    if _as_str(payload.get("source")) == "exec":
        state.session_is_headless = True
    provider = _as_str(payload.get("model_provider"))
    if provider is not None:
        state.session_provider = provider
    agent = _as_str(payload.get("agent_nickname"))
    if agent is not None:
        state.session_agent = agent
    return True


def _handle_turn_context(entry: Mapping[str, object], state: _CodexParseState) -> bool:
    if _as_str(entry.get("type")) != "turn_context":
        return False
    payload = _as_mapping(entry.get("payload"))
    if payload is None:
        return False
    model = _extract_model_from_payload(payload)
    if model is not None:
        state.current_model = model
    return True


def _parse_structured_token_count(
    entry: Mapping[str, object],
    *,
    file_path: Path,
    line_number: int,
    session_id: str,
    line_json: str,
    fallback_timestamp: int,
    state: _CodexParseState,
    include_raw_json: bool,
) -> tuple[UsageEvent | None, bool] | None:
    if _as_str(entry.get("type")) != "event_msg":
        return None
    payload = _as_mapping(entry.get("payload"))
    if payload is None or _as_str(payload.get("type")) != "token_count":
        return None

    payload_model = _extract_model_from_payload(payload)
    if payload_model is not None:
        state.current_model = payload_model

    info = _as_mapping(payload.get("info"))
    if info is None:
        return None, True

    info_model = _extract_model_from_info(info)
    if info_model is not None:
        state.current_model = info_model

    total_usage = _totals_from_value(info.get("total_token_usage"))
    last_usage = _totals_from_value(info.get("last_token_usage"))
    previous = state.previous_totals

    tokens: TokenBreakdown | None = None
    next_baseline: _CodexTotals | None = previous

    if total_usage is not None and last_usage is not None:
        if previous is not None:
            if total_usage == previous:
                return None, True
            if total_usage.delta_from(
                previous
            ) is None and total_usage.looks_like_stale_regression(previous, last_usage):
                return None, True
        tokens = last_usage.into_tokens()
        next_baseline = total_usage
    elif total_usage is not None and last_usage is None:
        if previous is not None:
            if total_usage == previous:
                return None, True
            delta = total_usage.delta_from(previous)
            if delta is None:
                state.previous_totals = total_usage
                return None, True
            tokens = delta.into_tokens()
            next_baseline = total_usage
        else:
            tokens = total_usage.into_tokens()
            next_baseline = total_usage
    elif total_usage is None and last_usage is not None:
        tokens = last_usage.into_tokens()
        if previous is not None:
            next_baseline = previous.saturating_add(last_usage)
        else:
            next_baseline = None
    else:
        return None, True

    if tokens is None or _is_zero_token_row(tokens):
        return None, True

    state.previous_totals = next_baseline
    return (
        _build_usage_event(
            file_path=file_path,
            line_number=line_number,
            session_id=session_id,
            line_json=line_json,
            fallback_timestamp=fallback_timestamp,
            entry=entry,
            state=state,
            include_raw_json=include_raw_json,
            model_id=state.current_model or "unknown",
            tokens=tokens,
        ),
        False,
    )


def _parse_headless_usage(
    value: Mapping[str, object],
    *,
    file_path: Path,
    line_number: int,
    session_id: str,
    line_json: str,
    fallback_timestamp: int,
    state: _CodexParseState,
    include_raw_json: bool,
) -> tuple[UsageEvent | None, bool]:
    usage = _headless_usage_mapping(value)
    if usage is None:
        return None, True

    input_tokens = _first_usage_int(usage, ("input_tokens", "prompt_tokens", "input"))
    output_tokens = _first_usage_int(
        usage, ("output_tokens", "completion_tokens", "output")
    )
    cached_tokens = _first_usage_int(
        usage,
        ("cached_input_tokens", "cache_read_input_tokens", "cached_tokens"),
    )

    tokens = TokenBreakdown(
        input=max(input_tokens - cached_tokens, 0),
        output=max(output_tokens, 0),
        cache_read=max(cached_tokens, 0),
        cache_write=0,
        reasoning=0,
    )
    if tokens.input == 0 and tokens.output == 0 and tokens.cache_read == 0:
        return None, True

    model_id = _extract_headless_model(value)
    if model_id is not None:
        state.current_model = model_id

    return (
        _build_usage_event(
            file_path=file_path,
            line_number=line_number,
            session_id=session_id,
            line_json=line_json,
            fallback_timestamp=fallback_timestamp,
            entry=value,
            state=state,
            include_raw_json=include_raw_json,
            model_id=state.current_model or "unknown",
            tokens=tokens,
        ),
        False,
    )


def _build_usage_event(
    *,
    file_path: Path,
    line_number: int,
    session_id: str,
    line_json: str,
    fallback_timestamp: int,
    entry: Mapping[str, object],
    state: _CodexParseState,
    include_raw_json: bool,
    model_id: str,
    tokens: TokenBreakdown,
) -> UsageEvent:
    created_ms = _event_timestamp_ms(entry) or fallback_timestamp
    source_row_id = f"{file_path.as_posix()}:{line_number}"
    source_dedup_key = source_row_id
    event = UsageEvent(
        harness=CODEX_HARNESS,
        source_session_id=session_id,
        source_row_id=source_row_id,
        source_message_id=None,
        source_dedup_key=source_dedup_key,
        global_dedup_key=f"{CODEX_HARNESS}:{session_id}:{source_dedup_key}",
        fingerprint_hash="",
        provider_id=state.session_provider or "openai",
        model_id=model_id,
        thinking_level=None,
        agent="headless" if state.session_is_headless else state.session_agent,
        created_ms=created_ms,
        completed_ms=None,
        tokens=tokens,
        source_cost_usd=Decimal(0),
        raw_json=line_json if include_raw_json else None,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _event_timestamp_ms(entry: Mapping[str, object]) -> int | None:
    for value in (
        entry.get("timestamp"),
        entry.get("time"),
        entry.get("created_at"),
        _nested_value(entry.get("data"), "timestamp"),
    ):
        parsed = _timestamp_ms_from_value(value)
        if parsed is not None:
            return parsed
    return None


def _extract_model_from_payload(payload: Mapping[str, object]) -> str | None:
    for candidate in (
        _nested_value(payload.get("model_info"), "slug"),
        payload.get("model"),
        payload.get("model_name"),
        _nested_value(payload.get("info"), "model"),
        _nested_value(payload.get("info"), "model_name"),
    ):
        model = _as_str(candidate)
        if model is not None:
            return model
    return None


def _extract_model_from_info(info: Mapping[str, object]) -> str | None:
    for candidate in (info.get("model"), info.get("model_name")):
        model = _as_str(candidate)
        if model is not None:
            return model
    return None


def _extract_headless_model(entry: Mapping[str, object]) -> str | None:
    for candidate in (
        entry.get("model"),
        entry.get("model_name"),
        _nested_value(entry.get("data"), "model"),
        _nested_value(entry.get("data"), "model_name"),
        _nested_value(entry.get("response"), "model"),
    ):
        model = _as_str(candidate)
        if model is not None:
            return model
    return None


def _headless_usage_mapping(entry: Mapping[str, object]) -> Mapping[str, object] | None:
    for candidate in (
        entry.get("usage"),
        _nested_value(entry.get("data"), "usage"),
        _nested_value(entry.get("result"), "usage"),
        _nested_value(entry.get("response"), "usage"),
    ):
        mapping = _as_mapping(candidate)
        if mapping is not None:
            return mapping
    return None


def _first_usage_int(usage: Mapping[str, object], keys: tuple[str, ...]) -> int:
    for key in keys:
        candidate = _value_as_int(usage.get(key))
        if candidate is not None:
            return max(candidate, 0)
    return 0


def _is_zero_token_row(tokens: TokenBreakdown) -> bool:
    return (
        tokens.input == 0
        and tokens.output == 0
        and tokens.cache_read == 0
        and tokens.reasoning == 0
    )


def _totals_from_value(value: object) -> _CodexTotals | None:
    mapping = _as_mapping(value)
    if mapping is None:
        return None
    return _CodexTotals.from_usage(mapping)


def _codex_source_paths_by_session(source_path: Path) -> dict[str, list[Path]]:
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
        scan = scan_codex_file(file_path, include_raw_json=False)
        for event in scan.events:
            grouped.setdefault(event.source_session_id, []).append(file_path)
    return grouped


def _session_id_from_path(path: Path) -> str:
    return path.stem or "unknown"


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


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _nested_value(value: object, key: str) -> object | None:
    mapping = _as_mapping(value)
    if mapping is None:
        return None
    return mapping.get(key)


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
    return None


def _as_non_negative_int(value: object, default: int = 0) -> int:
    parsed = _value_as_int(value)
    if parsed is None:
        return default
    return max(parsed, 0)


def _parse_rfc3339_ms(value: str) -> int | None:
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


def _timestamp_ms_from_value(value: object) -> int | None:
    if isinstance(value, str):
        return _parse_rfc3339_ms(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if abs(number) >= 10_000_000_000:
        return int(number)
    return int(number * 1000)


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
        "agent": event.agent,
        "thinking_level": event.thinking_level,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "CODEX_HARNESS",
    "CodexScanResult",
    "CodexSessionSummary",
    "_CodexTotals",
    "list_codex_sessions",
    "parse_codex_file",
    "parse_codex_path",
    "scan_codex_file",
    "scan_codex_path",
]
