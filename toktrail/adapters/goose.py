from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig, normalize_identity
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.provider_identity import inferred_provider_from_model

GOOSE_HARNESS = "goose"
GOOSE_PARSER_VERSION = 1

GooseScanResult = ScanResult


def open_readonly_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.expanduser().resolve().as_posix(), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def scan_goose_sqlite(
    db_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
) -> GooseScanResult:
    resolved_path = db_path.expanduser()
    if not resolved_path.exists():
        return GooseScanResult(
            source_path=resolved_path,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    try:
        with closing(open_readonly_sqlite(resolved_path)) as conn:
            rows = _select_candidate_rows(conn, source_session_id=source_session_id)
    except (OSError, sqlite3.Error):
        return GooseScanResult(
            source_path=resolved_path,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    rows_skipped = 0
    events: list[UsageEvent] = []
    for row in rows:
        event = _parse_goose_row(row, include_raw_json=include_raw_json)
        if event is None:
            rows_skipped += 1
            continue
        events.append(event)

    return GooseScanResult(
        source_path=resolved_path,
        rows_seen=len(rows),
        rows_skipped=rows_skipped,
        events=events,
    )


def parse_goose_sqlite(db_path: Path) -> list[UsageEvent]:
    return scan_goose_sqlite(db_path).events


def list_goose_sessions(
    db_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[SourceSessionSummary]:
    scan = scan_goose_sqlite(db_path, include_raw_json=False)
    source_paths = {
        event.source_session_id: [scan.source_path] for event in scan.events
    }
    return summarize_events_by_source_session(
        GOOSE_HARNESS,
        scan.events,
        source_paths_by_session=source_paths,
        costing_config=costing_config,
    )


def _select_candidate_rows(
    conn: sqlite3.Connection,
    *,
    source_session_id: str | None,
) -> list[sqlite3.Row]:
    session_filter = ""
    params: list[str] = []
    if source_session_id is not None:
        session_filter = " AND id = ?"
        params.append(source_session_id)

    query = f"""
        SELECT
            id,
            model_config_json,
            provider_name,
            created_at,
            total_tokens,
            input_tokens,
            output_tokens,
            accumulated_total_tokens,
            accumulated_input_tokens,
            accumulated_output_tokens
        FROM sessions
        WHERE model_config_json IS NOT NULL
          AND TRIM(model_config_json) != ''
          {session_filter}
        ORDER BY created_at, id
    """
    return conn.execute(query, params).fetchall()


def _parse_goose_row(
    row: sqlite3.Row,
    *,
    include_raw_json: bool,
) -> UsageEvent | None:
    session_id = _as_str(row["id"])
    model_config_json = _as_str(row["model_config_json"])
    created_at = _as_str(row["created_at"])
    if session_id is None or model_config_json is None or created_at is None:
        return None

    model_id = _parse_model_config(model_config_json)
    if model_id is None:
        return None

    input_tokens = _first_non_negative_int(
        row["accumulated_input_tokens"],
        row["input_tokens"],
    )
    output_tokens = _first_non_negative_int(
        row["accumulated_output_tokens"],
        row["output_tokens"],
    )
    total_tokens = _first_non_negative_int(
        row["accumulated_total_tokens"],
        row["total_tokens"],
    )
    if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return None

    reasoning_tokens = max(total_tokens - input_tokens - output_tokens, 0)
    tokens = TokenBreakdown(
        input=input_tokens,
        output=output_tokens,
        reasoning=reasoning_tokens,
        cache_read=0,
        cache_write=0,
        cache_output=0,
    )
    provider_id = _resolved_provider(_as_str(row["provider_name"]), model_id)
    raw_json = None
    if include_raw_json:
        raw_json = json.dumps(
            {
                "id": session_id,
                "model_config_json": model_config_json,
                "provider_name": _as_str(row["provider_name"]),
                "created_at": created_at,
                "total_tokens": row["total_tokens"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "accumulated_total_tokens": row["accumulated_total_tokens"],
                "accumulated_input_tokens": row["accumulated_input_tokens"],
                "accumulated_output_tokens": row["accumulated_output_tokens"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    event = UsageEvent(
        harness=GOOSE_HARNESS,
        source_session_id=session_id,
        source_row_id=session_id,
        source_message_id=None,
        source_dedup_key=session_id,
        global_dedup_key=f"{GOOSE_HARNESS}:{session_id}",
        fingerprint_hash="",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=None,
        agent=None,
        created_ms=_parse_created_at_ms(created_at),
        completed_ms=None,
        tokens=tokens,
        source_cost_usd=Decimal(0),
        raw_json=raw_json,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


def _parse_model_config(value: str) -> str | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    model_name = _as_str(payload.get("model_name"))
    return model_name


def _parse_created_at_ms(value: str) -> int:
    # RFC3339 / ISO-8601, including a trailing Z.
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        pass
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return int(dt.timestamp() * 1000)

    return 0


def _resolved_provider(provider_name: str | None, model_id: str) -> str:
    if provider_name is not None:
        try:
            provider = normalize_identity(provider_name)
        except ValueError:
            provider = ""
        if provider:
            return provider

    inferred = inferred_provider_from_model(model_id)
    if inferred is not None:
        return inferred

    return GOOSE_HARNESS


def _first_non_negative_int(*values: object) -> int:
    for value in values:
        parsed = _as_non_negative_int(value, default=None)
        if parsed is not None:
            return parsed
    return 0


def _as_non_negative_int(value: object, default: int | None = 0) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(float(value)):
        return default
    return max(int(value), 0)


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


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
