from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig
from toktrail.models import TokenBreakdown, UsageEvent, normalize_thinking_level

OPENCODE_HARNESS = "opencode"
OPENCODE_PARSER_VERSION = 1

OpenCodeScanResult = ScanResult


def open_readonly_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.expanduser().resolve().as_posix(), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def scan_opencode_sqlite(
    db_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> OpenCodeScanResult:
    resolved_path = db_path.expanduser()
    if not resolved_path.exists():
        return OpenCodeScanResult(
            source_path=resolved_path,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    try:
        with closing(open_readonly_sqlite(resolved_path)) as conn:
            rows = _select_candidate_rows(
                conn,
                source_session_id=source_session_id,
                since_ms=since_ms,
            )
    except (OSError, sqlite3.Error):
        return OpenCodeScanResult(
            source_path=resolved_path,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    rows_skipped = 0
    events: list[UsageEvent] = []
    fingerprints: dict[str, int] = {}
    for row in rows:
        event = _parse_opencode_row(
            row_id=str(row["id"]),
            session_id=str(row["session_id"]),
            data_json=str(row["data"]),
            include_raw_json=include_raw_json,
        )
        if event is None:
            rows_skipped += 1
            continue
        if since_ms is not None and event.created_ms < since_ms:
            rows_skipped += 1
            continue

        existing_index = fingerprints.get(event.fingerprint_hash)
        if existing_index is None:
            fingerprints[event.fingerprint_hash] = len(events)
            events.append(event)
            continue

        retained = events[existing_index]
        if retained.source_message_id is None and event.source_message_id is not None:
            events[existing_index] = replace(
                retained,
                source_message_id=event.source_message_id,
                source_dedup_key=event.source_message_id,
                global_dedup_key=f"{OPENCODE_HARNESS}:{event.source_message_id}",
            )
        rows_skipped += 1

    return OpenCodeScanResult(
        source_path=resolved_path,
        rows_seen=len(rows),
        rows_skipped=rows_skipped,
        events=events,
    )


def parse_opencode_sqlite(db_path: Path) -> list[UsageEvent]:
    return scan_opencode_sqlite(db_path).events


def list_opencode_sessions(
    db_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[SourceSessionSummary]:
    scan = scan_opencode_sqlite(db_path, include_raw_json=False)
    source_paths = {
        event.source_session_id: [scan.source_path] for event in scan.events
    }
    return summarize_events_by_source_session(
        OPENCODE_HARNESS,
        scan.events,
        source_paths_by_session=source_paths,
        costing_config=costing_config,
    )


def parse_opencode_row(
    row_id: str, session_id: str, data_json: str
) -> UsageEvent | None:
    return _parse_opencode_row(
        row_id=row_id,
        session_id=session_id,
        data_json=data_json,
        include_raw_json=True,
    )


def normalize_opencode_agent_name(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "omo": "omo",
        "build": "build",
        "plan": "plan",
    }
    return aliases.get(normalized, normalized or "unknown")


def _parse_opencode_row(
    *,
    row_id: str,
    session_id: str,
    data_json: str,
    include_raw_json: bool,
) -> UsageEvent | None:
    payload = _json_loads(data_json)
    if payload is None:
        return None

    role = _as_str(payload.get("role"))
    if role != "assistant":
        return None

    tokens_value = payload.get("tokens")
    if not isinstance(tokens_value, dict):
        return None

    model_id = _as_str(payload.get("modelID"))
    if model_id is None:
        return None

    time_value = payload.get("time")
    if not isinstance(time_value, dict):
        return None

    created_ms = _timestamp_ms(time_value.get("created"))
    if created_ms is None:
        return None

    completed_ms = _timestamp_ms(time_value.get("completed"))
    source_message_id = _as_str(payload.get("id"))
    provider_id = _as_str(payload.get("providerID")) or "unknown"
    thinking_level = _thinking_level(payload)
    agent = _normalized_agent(payload)
    token_breakdown = TokenBreakdown(
        input=_as_non_negative_int(tokens_value.get("input")),
        output=_as_non_negative_int(tokens_value.get("output")),
        reasoning=_as_non_negative_int(tokens_value.get("reasoning")),
        cache_read=_as_non_negative_int(_nested_cache_value(tokens_value, "read")),
        cache_write=_as_non_negative_int(_nested_cache_value(tokens_value, "write")),
        cache_output=_as_non_negative_int(_nested_cache_value(tokens_value, "output")),
    )
    cost_usd = _as_non_negative_decimal(payload.get("cost"))
    source_dedup_key = source_message_id or row_id
    event = UsageEvent(
        harness=OPENCODE_HARNESS,
        source_session_id=session_id,
        source_row_id=row_id,
        source_message_id=source_message_id,
        source_dedup_key=source_dedup_key,
        global_dedup_key=f"{OPENCODE_HARNESS}:{source_dedup_key}",
        fingerprint_hash="",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=thinking_level,
        agent=agent,
        created_ms=created_ms,
        completed_ms=completed_ms,
        tokens=token_breakdown,
        source_cost_usd=cost_usd,
        raw_json=data_json if include_raw_json else None,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _select_candidate_rows(
    conn: sqlite3.Connection,
    *,
    source_session_id: str | None,
    since_ms: int | None = None,
) -> list[sqlite3.Row]:
    session_filter = ""
    since_filter = ""
    params: list[object] = []
    if source_session_id is not None:
        session_filter = " AND m.session_id = ?"
        params.append(source_session_id)
    if since_ms is not None:
        since_filter = (
            " AND CAST(json_extract(m.data, '$.time.created') * 1000 AS INTEGER) >= ?"
        )
        params.append(since_ms)

    json_query = f"""
        SELECT m.id, m.session_id, m.data
        FROM message m
        WHERE json_extract(m.data, '$.role') = 'assistant'
          AND json_extract(m.data, '$.tokens') IS NOT NULL
          {session_filter}
          {since_filter}
        ORDER BY m.rowid
    """
    fallback_query = f"""
        SELECT m.id, m.session_id, m.data
        FROM message m
        WHERE 1 = 1
          {session_filter}
        ORDER BY m.rowid
    """

    try:
        return conn.execute(json_query, params).fetchall()
    except sqlite3.OperationalError:
        return conn.execute(fallback_query, params).fetchall()


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


def _as_non_negative_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(float(value)):
        return default
    return max(float(value), 0.0)


def _as_non_negative_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return Decimal(0)
    try:
        f = float(value)
        if not math.isfinite(f):
            return Decimal(0)
        return max(Decimal(str(value)), Decimal(0))
    except (ValueError, TypeError):
        return Decimal(0)


def _timestamp_ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return int(value)


def _make_fingerprint(event: UsageEvent) -> str:
    payload = {
        "created_ms": event.created_ms,
        "completed_ms": event.completed_ms,
        "model_id": event.model_id,
        "provider_id": event.provider_id,
        "input": event.tokens.input,
        "output": event.tokens.output,
        "reasoning": event.tokens.reasoning,
        "cache_read": event.tokens.cache_read,
        "cache_write": event.tokens.cache_write,
        "source_cost_usd": str(event.source_cost_usd),
        "thinking_level": event.thinking_level,
        "agent": event.agent,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _nested_cache_value(tokens_value: dict[str, object], key: str) -> object:
    cache_value = tokens_value.get("cache")
    if isinstance(cache_value, dict):
        return cache_value.get(key)
    return None


def _normalized_agent(payload: dict[str, object]) -> str | None:
    mode = _as_str(payload.get("mode"))
    if mode is not None:
        return normalize_opencode_agent_name(mode)
    agent = _as_str(payload.get("agent"))
    if agent is not None:
        return normalize_opencode_agent_name(agent)
    return None


def _thinking_level(payload: dict[str, object]) -> str | None:
    for key in (
        "reasoningEffort",
        "reasoning_effort",
        "thinkingLevel",
        "thinking_level",
    ):
        normalized = normalize_thinking_level(payload.get(key))
        if normalized is not None:
            return normalized
    request = payload.get("request")
    if not isinstance(request, dict):
        return None
    for key in (
        "reasoningEffort",
        "reasoning_effort",
        "thinkingLevel",
        "thinking_level",
    ):
        normalized = normalize_thinking_level(request.get(key))
        if normalized is not None:
            return normalized
    return None
