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
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.provider_identity import inferred_provider_from_model

DROID_HARNESS = "droid"
DROID_PARSER_VERSION = 1

DroidScanResult = ScanResult
DroidSessionSummary = SourceSessionSummary


def scan_droid_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> DroidScanResult:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return DroidScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    file_paths = (
        [resolved_path]
        if resolved_path.is_file()
        else sorted(resolved_path.rglob("*.settings.json"))
    )

    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    for file_path in file_paths:
        scan = scan_droid_file(file_path, include_raw_json=include_raw_json)
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

    return DroidScanResult(
        source_path=resolved_path,
        files_seen=len(file_paths),
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def scan_droid_file(
    file_path: Path,
    *,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> DroidScanResult:
    resolved_path = file_path.expanduser()
    if not resolved_path.exists() or not resolved_path.is_file():
        return DroidScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    try:
        data_json = resolved_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return DroidScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    event = _parse_droid_settings(
        resolved_path,
        data_json,
        include_raw_json=include_raw_json,
    )
    return DroidScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=1,
        rows_skipped=0 if event is not None else 1,
        events=[] if event is None else [event],
    )


def parse_droid_file(path: Path) -> list[UsageEvent]:
    return scan_droid_file(path).events


def parse_droid_path(path: Path) -> list[UsageEvent]:
    return scan_droid_path(path).events


def list_droid_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[DroidSessionSummary]:
    scan = scan_droid_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        DROID_HARNESS,
        scan.events,
        source_paths_by_session=_droid_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


def _parse_droid_settings(
    path: Path,
    data_json: str,
    *,
    include_raw_json: bool,
) -> UsageEvent | None:
    settings = _json_loads(data_json)
    if settings is None:
        return None

    usage = settings.get("tokenUsage")
    if not isinstance(usage, dict):
        return None

    tokens = TokenBreakdown(
        input=_as_non_negative_int(usage.get("inputTokens")),
        output=_as_non_negative_int(usage.get("outputTokens")),
        reasoning=_as_non_negative_int(usage.get("thinkingTokens")),
        cache_read=_as_non_negative_int(usage.get("cacheReadTokens")),
        cache_write=_as_non_negative_int(usage.get("cacheCreationTokens")),
        cache_output=_as_non_negative_int(usage.get("cacheOutputTokens")),
    )
    if tokens.total == 0:
        return None

    session_id = _session_id_from_settings_path(path)
    raw_model = _as_str(settings.get("model"))
    provider_id = _resolved_provider(_as_str(settings.get("providerLock")), raw_model)
    model_id = (
        _normalize_model_name(raw_model)
        if raw_model is not None
        else _extract_model_from_jsonl(_jsonl_path_for_settings(path))
        or _default_model_from_provider(provider_id)
    )

    created_ms = _parse_rfc3339_ms(settings.get("providerLockTimestamp"))
    if created_ms is None:
        created_ms = _file_modified_timestamp_ms(path)
    if created_ms == 0:
        return None

    raw_json = (
        json.dumps(settings, sort_keys=True, separators=(",", ":"))
        if include_raw_json
        else None
    )

    event = UsageEvent(
        harness=DROID_HARNESS,
        source_session_id=session_id,
        source_row_id=str(path),
        source_message_id=None,
        source_dedup_key=session_id,
        global_dedup_key=f"{DROID_HARNESS}:{session_id}",
        fingerprint_hash="",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=None,
        agent=None,
        created_ms=created_ms,
        completed_ms=None,
        tokens=tokens,
        source_cost_usd=Decimal(0),
        raw_json=raw_json,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _normalize_model_name(model: str) -> str:
    normalized = model.removeprefix("custom:")

    result: list[str] = []
    in_bracket = False
    for ch in normalized:
        if ch == "[":
            in_bracket = True
            continue
        if ch == "]":
            in_bracket = False
            continue
        if not in_bracket:
            result.append(ch)

    normalized = "".join(result).rstrip("-").lower().replace(".", "-")

    collapsed: list[str] = []
    last_was_hyphen = False
    for ch in normalized:
        if ch == "-":
            if not last_was_hyphen:
                collapsed.append(ch)
            last_was_hyphen = True
        else:
            collapsed.append(ch)
            last_was_hyphen = False
    return "".join(collapsed)


def _resolved_provider(provider_lock: str | None, model: str | None) -> str:
    if provider_lock is not None:
        try:
            provider = normalize_identity(provider_lock)
        except ValueError:
            provider = ""
        if provider:
            return provider

    inferred = inferred_provider_from_model(model or "")
    return inferred or "unknown"


def _default_model_from_provider(provider: str) -> str:
    try:
        normalized = normalize_identity(provider)
    except ValueError:
        normalized = provider
    if normalized == "anthropic":
        return "claude-unknown"
    if normalized == "openai":
        return "gpt-unknown"
    if normalized == "google":
        return "gemini-unknown"
    if normalized == "xai":
        return "grok-unknown"
    return f"{normalized}-unknown"


def _extract_model_from_jsonl(jsonl_path: Path) -> str | None:
    if not jsonl_path.exists() or not jsonl_path.is_file():
        return None
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(500), handle, strict=False):
                pos = line.find("Model:")
                if pos < 0:
                    continue
                after_model = line[pos + len("Model:") :]
                chars: list[str] = []
                for ch in after_model:
                    if ch in {"[", "\\", '"'}:
                        break
                    chars.append(ch)
                model_name = "".join(chars).strip()
                if model_name:
                    return _normalize_model_name(model_name)
    except OSError:
        return None
    return None


def _droid_source_paths_by_session(source_path: Path) -> dict[str, list[Path]]:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return {}
    file_paths = (
        [resolved_path]
        if resolved_path.is_file()
        else sorted(resolved_path.rglob("*.settings.json"))
    )
    grouped: dict[str, list[Path]] = {}
    for file_path in file_paths:
        scan = scan_droid_file(file_path, include_raw_json=False)
        for event in scan.events:
            grouped.setdefault(event.source_session_id, []).append(file_path)
    return grouped


def _jsonl_path_for_settings(path: Path) -> Path:
    name = path.name
    if name.endswith(".settings.json"):
        return path.with_name(name[: -len(".settings.json")] + ".jsonl")
    return Path(str(path).replace(".settings.json", ".jsonl"))


def _session_id_from_settings_path(path: Path) -> str:
    name = path.name
    if name.endswith(".settings.json"):
        return name[: -len(".settings.json")] or "unknown"
    if name.endswith(".json"):
        stem = name[: -len(".json")]
    else:
        stem = path.stem
    return stem.removesuffix(".settings") or "unknown"


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
    payload = "|".join(
        [
            event.harness,
            event.source_session_id,
            event.source_dedup_key,
            event.provider_id,
            event.model_id,
            str(event.created_ms),
            str(event.tokens.input),
            str(event.tokens.output),
            str(event.tokens.reasoning),
            str(event.tokens.cache_read),
            str(event.tokens.cache_write),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
