"""Shared helper utilities for adapter implementations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from toktrail.adapters.base import SourceSessionMetadata
from toktrail.models import UsageEvent


def as_non_empty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def json_object_or_none(data_json: str) -> dict[str, object] | None:
    try:
        value = json.loads(data_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def file_modified_timestamp_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def parse_rfc3339_ms(value: object) -> int | None:
    raw = as_non_empty_str(value)
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


def merge_source_session_metadata(
    current: SourceSessionMetadata | None,
    incoming: SourceSessionMetadata,
) -> SourceSessionMetadata:
    if current is None:
        return incoming

    merged_paths = tuple(sorted(set(current.source_paths) | set(incoming.source_paths)))
    return SourceSessionMetadata(
        harness=incoming.harness,
        source_session_id=incoming.source_session_id,
        source_paths=merged_paths,
        cwd=current.cwd or incoming.cwd,
        source_dir=current.source_dir or incoming.source_dir,
        git_root=current.git_root or incoming.git_root,
        git_remote=current.git_remote or incoming.git_remote,
        session_title=current.session_title or incoming.session_title,
        started_ms=_min_optional_int(current.started_ms, incoming.started_ms),
        last_seen_ms=_max_optional_int(current.last_seen_ms, incoming.last_seen_ms),
    )


def fingerprint_usage_event(event: UsageEvent) -> str:
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
    return sha256(payload.encode("utf-8")).hexdigest()


def _min_optional_int(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _max_optional_int(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)
