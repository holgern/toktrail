from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig, normalize_identity
from toktrail.models import TokenBreakdown, UsageEvent, normalize_thinking_level
from toktrail.provider_identity import inferred_provider_from_model

VIBE_HARNESS = "vibe"
VIBE_PARSER_VERSION = 1

VibeScanResult = ScanResult
VibeSessionSummary = SourceSessionSummary


def scan_vibe_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
) -> VibeScanResult:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return VibeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    meta_paths = _meta_paths(resolved_path)

    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    for file_path in meta_paths:
        scan = scan_vibe_meta_file(file_path, include_raw_json=include_raw_json)
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

    return VibeScanResult(
        source_path=resolved_path,
        files_seen=len(meta_paths),
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def scan_vibe_meta_file(
    file_path: Path,
    *,
    include_raw_json: bool = True,
) -> VibeScanResult:
    resolved_path = file_path.expanduser()
    if not resolved_path.exists() or not resolved_path.is_file():
        return VibeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    try:
        data_json = resolved_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return VibeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    event = _parse_vibe_meta(
        resolved_path,
        data_json,
        include_raw_json=include_raw_json,
    )
    return VibeScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=1,
        rows_skipped=0 if event is not None else 1,
        events=[] if event is None else [event],
    )


def parse_vibe_file(path: Path) -> list[UsageEvent]:
    return scan_vibe_meta_file(path).events


def parse_vibe_path(path: Path) -> list[UsageEvent]:
    return scan_vibe_path(path).events


def list_vibe_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[VibeSessionSummary]:
    scan = scan_vibe_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        VIBE_HARNESS,
        scan.events,
        source_paths_by_session=_vibe_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


def _meta_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.name == "meta.json" else []
    direct = path / "meta.json"
    if direct.exists() and direct.is_file():
        return [direct]
    return sorted(path.rglob("meta.json"))


def _parse_vibe_meta(
    path: Path,
    data_json: str,
    *,
    include_raw_json: bool,
) -> UsageEvent | None:
    meta = _json_loads(data_json)
    if meta is None:
        return None

    stats = _as_mapping(meta.get("stats"))
    if stats is None:
        return None

    tokens = TokenBreakdown(
        input=_as_non_negative_int(stats.get("session_prompt_tokens")),
        output=_as_non_negative_int(stats.get("session_completion_tokens")),
        reasoning=0,
        cache_read=0,
        cache_write=0,
    )
    if tokens.total == 0:
        return None

    session_id = _as_str(meta.get("session_id")) or path.parent.name or path.stem
    created_ms = _parse_rfc3339_ms(
        meta.get("start_time")
    ) or _file_modified_timestamp_ms(path)
    if created_ms == 0:
        return None

    completed_ms = _parse_rfc3339_ms(meta.get("end_time"))
    if completed_ms is not None and completed_ms < created_ms:
        completed_ms = None

    provider_id, model_id, thinking_level = _model_identity(meta)
    source_message_id = (
        _last_assistant_message_id(path.with_name("messages.jsonl")) or session_id
    )
    source_cost_usd = _source_cost(stats, tokens)

    raw_json = (
        json.dumps(meta, sort_keys=True, separators=(",", ":"))
        if include_raw_json
        else None
    )

    event = UsageEvent(
        harness=VIBE_HARNESS,
        source_session_id=session_id,
        source_row_id=str(path),
        source_message_id=source_message_id,
        source_dedup_key=f"session:{session_id}",
        global_dedup_key=f"{VIBE_HARNESS}:session:{session_id}",
        fingerprint_hash="",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=thinking_level,
        agent=None,
        created_ms=created_ms,
        completed_ms=completed_ms,
        tokens=tokens,
        source_cost_usd=source_cost_usd,
        raw_json=raw_json,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _model_identity(meta: dict[str, object]) -> tuple[str, str, str | None]:
    config = _as_mapping(meta.get("config"))
    if config is None:
        return "unknown", "unknown", None

    active_model = _as_str(config.get("active_model"))
    if not active_model:
        return "unknown", "unknown", None

    models = config.get("models")
    if not isinstance(models, list):
        inferred = inferred_provider_from_model(active_model)
        return inferred or "mistral", active_model, None

    for model_record in models:
        model = _as_mapping(model_record)
        if model is None:
            continue
        alias = _as_str(model.get("alias"))
        name = _as_str(model.get("name"))
        if alias == active_model or name == active_model:
            resolved_provider = _as_str(model.get("provider"))
            if resolved_provider:
                try:
                    provider_id = normalize_identity(resolved_provider)
                except ValueError:
                    provider_id = ""
                if not provider_id:
                    inferred = inferred_provider_from_model(name or active_model)
                    provider_id = inferred or resolved_provider
            else:
                inferred = inferred_provider_from_model(name or active_model)
                provider_id = inferred or "mistral"
            model_id = name or active_model
            thinking_str = _as_str(model.get("thinking"))
            thinking_level = (
                normalize_thinking_level(thinking_str) if thinking_str else None
            )
            return provider_id, model_id, thinking_level

    inferred = inferred_provider_from_model(active_model)
    return inferred or "mistral", active_model, None


def _source_cost(stats: dict[str, object], tokens: TokenBreakdown) -> Decimal:
    explicit = _as_non_negative_decimal(stats.get("session_cost"))
    if explicit > 0:
        return explicit
    input_price = _as_non_negative_decimal(stats.get("input_price_per_million"))
    output_price = _as_non_negative_decimal(stats.get("output_price_per_million"))
    return Decimal(tokens.input) * input_price / Decimal(1_000_000) + Decimal(
        tokens.output
    ) * output_price / Decimal(1_000_000)


def _last_assistant_message_id(jsonl_path: Path) -> str | None:
    if not jsonl_path.exists() or not jsonl_path.is_file():
        return None
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as handle:
            last_assistant_id = None
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("role") == "assistant":
                    msg_id = _as_str(row.get("message_id"))
                    if msg_id:
                        last_assistant_id = msg_id
            return last_assistant_id
    except OSError:
        return None


def _vibe_source_paths_by_session(source_path: Path) -> dict[str, list[Path]]:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return {}
    meta_paths = _meta_paths(resolved_path)
    grouped: dict[str, list[Path]] = {}
    for file_path in meta_paths:
        scan = scan_vibe_meta_file(file_path, include_raw_json=False)
        for event in scan.events:
            grouped.setdefault(event.source_session_id, []).append(file_path)
    return grouped


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


def _as_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return value


def _as_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _as_non_negative_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    numeric = float(value)
    if not math.isfinite(numeric):
        return default
    return max(int(numeric), 0)


def _as_non_negative_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return Decimal(0)
    numeric = float(value)
    if not math.isfinite(numeric):
        return Decimal(0)
    if numeric < 0:
        return Decimal(0)
    return Decimal(str(numeric))


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
        "thinking_level": event.thinking_level,
        "created_ms": event.created_ms,
        "completed_ms": event.completed_ms,
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
