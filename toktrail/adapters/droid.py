from __future__ import annotations

import json
import math
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from toktrail.adapters._common import (
    as_non_empty_str,
    file_modified_timestamp_ms,
    fingerprint_usage_event,
    json_object_or_none,
    parse_rfc3339_ms,
)
from toktrail.adapters.base import (
    ImportScanState,
    ScanResult,
    SourceSessionSummary,
    build_import_source_file_state,
    decide_file_scan,
)
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
    import_state: ImportScanState | None = None,
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
    file_states = []
    for file_path in file_paths:
        scan = scan_droid_file(
            file_path,
            include_raw_json=include_raw_json,
            since_ms=since_ms,
            import_state=import_state,
            source_root=resolved_path if resolved_path.is_dir() else None,
        )
        rows_seen += scan.rows_seen
        rows_skipped += scan.rows_skipped
        file_states.extend(scan.file_states)
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
        file_states=tuple(file_states),
        discovered_file_paths=tuple(str(path) for path in file_paths),
    )


def scan_droid_file(
    file_path: Path,
    *,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: ImportScanState | None = None,
    source_root: Path | None = None,
) -> DroidScanResult:
    resolved_path = file_path.expanduser()
    decision = decide_file_scan(
        resolved_path,
        parser_version=DROID_PARSER_VERSION,
        import_state=import_state,
        allow_resume=False,
    )
    if decision is None:
        return DroidScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )
    if decision.mode == "skip":
        return DroidScanResult(
            source_path=resolved_path,
            files_seen=1,
            rows_seen=0,
            rows_skipped=0,
            events=[],
            discovered_file_paths=(str(resolved_path),),
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
    events = [] if event is None else [event]
    if since_ms is not None:
        kept = [entry for entry in events if entry.created_ms >= since_ms]
        skipped = len(events) - len(kept)
        events = kept
    else:
        skipped = 0
    return DroidScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=1,
        rows_skipped=(0 if event is not None else 1) + skipped,
        events=events,
        file_states=(
            build_import_source_file_state(
                harness=DROID_HARNESS,
                source_path=source_root or resolved_path,
                file_path=resolved_path,
                signature=decision.signature,
                last_imported_created_ms=max(
                    (entry.created_ms for entry in events),
                    default=(
                        decision.prior_state.last_imported_created_ms
                        if decision.prior_state is not None
                        else None
                    ),
                ),
                last_file_offset=decision.signature.size,
                parser_version=DROID_PARSER_VERSION,
            ),
        ),
        discovered_file_paths=(str(resolved_path),),
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
    if tokens.accounting_total == 0:
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
    return file_modified_timestamp_ms(path)


def _json_loads(data_json: str) -> dict[str, object] | None:
    return json_object_or_none(data_json)


def _as_str(value: object) -> str | None:
    return as_non_empty_str(value)


def _as_non_negative_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(float(value)):
        return default
    return max(int(value), 0)


def _parse_rfc3339_ms(value: object) -> int | None:
    return parse_rfc3339_ms(value)


def _make_fingerprint(event: UsageEvent) -> str:
    return fingerprint_usage_event(event)
