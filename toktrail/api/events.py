from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import time
from typing import Any

from toktrail import db as db_module
from toktrail.api._common import _open_state_db
from toktrail.api.models import ImportUsageResult, TokenBreakdown, UsageEvent
from toktrail.config import normalize_identity
from toktrail.errors import InvalidAPIUsageError, RunNotFoundError, UsageImportError
from toktrail.models import (
    TokenBreakdown as InternalTokenBreakdown,
)
from toktrail.models import UsageEvent as InternalUsageEvent
from toktrail.models import normalize_thinking_level


def _now_ms() -> int:
    return int(time() * 1000)


def _ensure_non_empty(value: str, *, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        msg = f"{field_name} must not be empty."
        raise InvalidAPIUsageError(msg)
    return stripped


def _normalize_identity_or_raise(value: str, *, field_name: str) -> str:
    try:
        return normalize_identity(value)
    except ValueError as exc:
        msg = f"Invalid {field_name}: {value!r}"
        raise InvalidAPIUsageError(msg) from exc


def _normalize_decimal(value: Decimal | str | float | int) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - Decimal errors are version-specific
        msg = f"Invalid source_cost_usd value: {value!r}"
        raise InvalidAPIUsageError(msg) from exc
    if decimal_value < Decimal(0):
        msg = "source_cost_usd must be non-negative."
        raise InvalidAPIUsageError(msg)
    return decimal_value


def _serialize_raw_json(raw_json: Mapping[str, Any] | str | None) -> str | None:
    if raw_json is None:
        return None
    if isinstance(raw_json, str):
        return raw_json
    return json.dumps(raw_json, sort_keys=True, separators=(",", ":"))


def _to_internal_event(event: UsageEvent) -> InternalUsageEvent:
    return InternalUsageEvent(
        harness=event.harness,
        source_session_id=event.source_session_id,
        source_row_id=event.source_row_id,
        source_message_id=event.source_message_id,
        source_dedup_key=event.source_dedup_key,
        global_dedup_key=event.global_dedup_key,
        fingerprint_hash=event.fingerprint_hash,
        provider_id=event.provider_id,
        model_id=event.model_id,
        thinking_level=event.thinking_level,
        agent=event.agent,
        created_ms=event.created_ms,
        completed_ms=event.completed_ms,
        tokens=InternalTokenBreakdown(
            input=event.tokens.input,
            output=event.tokens.output,
            reasoning=event.tokens.reasoning,
            cache_read=event.tokens.cache_read,
            cache_write=event.tokens.cache_write,
            cache_output=event.tokens.cache_output,
        ),
        source_cost_usd=event.source_cost_usd,
        raw_json=event.raw_json,
    )


def _event_fingerprint_payload(event: UsageEvent) -> dict[str, object]:
    return {
        "harness": event.harness,
        "source_session_id": event.source_session_id,
        "source_row_id": event.source_row_id,
        "source_message_id": event.source_message_id,
        "source_dedup_key": event.source_dedup_key,
        "provider_id": event.provider_id,
        "model_id": event.model_id,
        "thinking_level": event.thinking_level,
        "agent": event.agent,
        "created_ms": event.created_ms,
        "completed_ms": event.completed_ms,
        "input": event.tokens.input,
        "output": event.tokens.output,
        "reasoning": event.tokens.reasoning,
        "cache_read": event.tokens.cache_read,
        "cache_write": event.tokens.cache_write,
        "cache_output": event.tokens.cache_output,
        "source_cost_usd": str(event.source_cost_usd),
    }


def _make_fingerprint(event: UsageEvent) -> str:
    payload = _event_fingerprint_payload(event)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _default_source_dedup_key(
    *,
    source_message_id: str | None,
    harness: str,
    source_session_id: str,
    provider_id: str,
    model_id: str,
    tokens: TokenBreakdown,
    created_ms: int,
    completed_ms: int | None,
    thinking_level: str | None,
    agent: str | None,
) -> str:
    if source_message_id is not None:
        return source_message_id
    payload = {
        "harness": harness,
        "source_session_id": source_session_id,
        "provider_id": provider_id,
        "model_id": model_id,
        "created_ms": created_ms,
        "completed_ms": completed_ms,
        "thinking_level": thinking_level,
        "agent": agent,
        "input": tokens.input,
        "output": tokens.output,
        "reasoning": tokens.reasoning,
        "cache_read": tokens.cache_read,
        "cache_write": tokens.cache_write,
        "cache_output": tokens.cache_output,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_run(
    db_path: Path | None,
    *,
    session_id: int | None,
    use_active_session: bool,
) -> tuple[sqlite3.Connection, int | None, Any]:
    conn, _ = _open_state_db(db_path)
    selected_session_id = session_id
    tracking_session = None
    if selected_session_id is None and use_active_session:
        selected_session_id = db_module.get_active_tracking_session(conn)
    if selected_session_id is not None:
        tracking_session = db_module.get_tracking_session(conn, selected_session_id)
        if tracking_session is None:
            msg = f"Run not found: {selected_session_id}"
            raise RunNotFoundError(msg)
    return conn, selected_session_id, tracking_session


def _build_import_result(
    *,
    run_id: int | None,
    events: list[UsageEvent],
    rows_inserted: int,
    rows_linked: int,
    rows_scope_excluded: int,
    rows_skipped: int,
) -> ImportUsageResult:
    harnesses = {event.harness for event in events}
    source_session_ids = {event.source_session_id for event in events}
    first_event_ms = min((event.created_ms for event in events), default=None)
    last_event_ms = max((event.created_ms for event in events), default=None)
    return ImportUsageResult(
        run_id=run_id,
        harness=next(iter(harnesses)) if len(harnesses) == 1 else "mixed",
        source_path=None,
        source_session_id=(
            next(iter(source_session_ids)) if len(source_session_ids) == 1 else None
        ),
        rows_seen=len(events),
        rows_imported=rows_inserted,
        rows_linked=rows_linked,
        rows_scope_excluded=rows_scope_excluded,
        rows_skipped=rows_skipped,
        events_seen=len(events),
        events_imported=rows_inserted,
        events_skipped=rows_skipped,
        first_event_ms=first_event_ms,
        last_event_ms=last_event_ms,
    )


def record_usage_event(
    db_path: Path | None,
    *,
    provider_id: str,
    model_id: str,
    tokens: TokenBreakdown,
    harness: str = "api",
    source_session_id: str = "api",
    source_row_id: str | None = None,
    source_message_id: str | None = None,
    source_dedup_key: str | None = None,
    thinking_level: str | None = None,
    agent: str | None = None,
    source_cost_usd: Decimal | str | float | int = Decimal(0),
    created_ms: int | None = None,
    completed_ms: int | None = None,
    session_id: int | None = None,
    use_active_session: bool = True,
    raw_json: Mapping[str, Any] | str | None = None,
    include_raw_json: bool = False,
) -> ImportUsageResult:
    normalized_harness = _normalize_identity_or_raise(harness, field_name="harness")
    normalized_provider = _normalize_identity_or_raise(
        provider_id,
        field_name="provider_id",
    )
    normalized_model = _ensure_non_empty(model_id, field_name="model_id")
    normalized_source_session = _ensure_non_empty(
        source_session_id,
        field_name="source_session_id",
    )
    normalized_source_message_id = (
        _ensure_non_empty(source_message_id, field_name="source_message_id")
        if source_message_id is not None
        else None
    )
    normalized_source_row_id = (
        _ensure_non_empty(source_row_id, field_name="source_row_id")
        if source_row_id is not None
        else None
    )
    normalized_agent = (
        _normalize_identity_or_raise(agent, field_name="agent")
        if agent is not None
        else None
    )
    normalized_thinking = normalize_thinking_level(thinking_level)
    event_created_ms = created_ms if created_ms is not None else _now_ms()
    event_completed_ms = completed_ms
    normalized_cost = _normalize_decimal(source_cost_usd)
    serialized_raw_json = _serialize_raw_json(raw_json) if include_raw_json else None

    dedup_key = source_dedup_key
    if dedup_key is not None:
        dedup_key = _ensure_non_empty(dedup_key, field_name="source_dedup_key")
    else:
        dedup_key = _default_source_dedup_key(
            source_message_id=normalized_source_message_id,
            harness=normalized_harness,
            source_session_id=normalized_source_session,
            provider_id=normalized_provider,
            model_id=normalized_model,
            tokens=tokens,
            created_ms=event_created_ms,
            completed_ms=event_completed_ms,
            thinking_level=normalized_thinking,
            agent=normalized_agent,
        )

    event = UsageEvent(
        harness=normalized_harness,
        source_session_id=normalized_source_session,
        source_row_id=normalized_source_row_id,
        source_message_id=normalized_source_message_id,
        source_dedup_key=dedup_key,
        global_dedup_key=f"{normalized_harness}:{normalized_source_session}:{dedup_key}",
        fingerprint_hash="",
        provider_id=normalized_provider,
        model_id=normalized_model,
        thinking_level=normalized_thinking,
        agent=normalized_agent,
        created_ms=event_created_ms,
        completed_ms=event_completed_ms,
        tokens=tokens,
        source_cost_usd=normalized_cost,
        raw_json=serialized_raw_json,
    )
    finalized_event = replace(event, fingerprint_hash=_make_fingerprint(event))
    return record_usage_events(
        db_path,
        (finalized_event,),
        session_id=session_id,
        use_active_session=use_active_session,
    )


def record_usage_events(
    db_path: Path | None,
    events: Iterable[UsageEvent],
    *,
    session_id: int | None = None,
    use_active_session: bool = True,
) -> ImportUsageResult:
    public_events = list(events)
    if not public_events:
        return ImportUsageResult(
            run_id=session_id,
            harness="api",
            source_path=None,
            source_session_id=None,
            rows_seen=0,
            rows_imported=0,
            rows_skipped=0,
            events_seen=0,
            events_imported=0,
            events_skipped=0,
        )

    internal_events = [_to_internal_event(event) for event in public_events]
    conn: sqlite3.Connection | None = None
    try:
        conn, selected_session_id, tracking_session = _resolve_run(
            db_path,
            session_id=session_id,
            use_active_session=use_active_session,
        )
        try:
            insert_result = db_module.insert_usage_events(
                conn,
                selected_session_id,
                internal_events,
                link_scope=(
                    tracking_session.scope if tracking_session is not None else None
                ),
            )
        except (sqlite3.Error, ValueError) as exc:
            msg = f"Failed to record usage events: {exc}"
            raise UsageImportError(msg) from exc
    finally:
        if conn is not None:
            conn.close()

    return _build_import_result(
        run_id=selected_session_id,
        events=public_events,
        rows_inserted=insert_result.rows_inserted,
        rows_linked=insert_result.rows_linked,
        rows_scope_excluded=insert_result.rows_scope_excluded,
        rows_skipped=insert_result.rows_skipped,
    )


__all__ = ["record_usage_event", "record_usage_events"]
