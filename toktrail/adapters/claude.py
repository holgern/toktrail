from __future__ import annotations

import hashlib
import json
import math
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig
from toktrail.models import TokenBreakdown, UsageEvent

CLAUDE_HARNESS = "claude"
CLAUDE_PARSER_VERSION = 1

ClaudeScanResult = ScanResult
ClaudeSessionSummary = SourceSessionSummary

ParentSubagentTypeCache = dict[Path, dict[str, str]]


@dataclass
class _ClaudeHeadlessState:
    model: str | None = None
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_output: int = 0
    timestamp_ms: int | None = None


def scan_claude_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> ClaudeScanResult:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return ClaudeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    if resolved_path.is_file():
        file_paths = [resolved_path]
    else:
        collected: list[Path] = []
        for pattern in ("*.jsonl", "*.json"):
            collected.extend(resolved_path.rglob(pattern))
        file_paths = sorted(p for p in collected if not p.name.endswith(".meta.json"))

    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    parent_cache: ParentSubagentTypeCache = {}

    for file_path in file_paths:
        scan = scan_claude_file(
            file_path,
            include_raw_json=include_raw_json,
            parent_cache=parent_cache,
        )
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

    return ClaudeScanResult(
        source_path=resolved_path,
        files_seen=len(file_paths),
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def scan_claude_file(
    file_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    parent_cache: ParentSubagentTypeCache | None = None,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> ClaudeScanResult:
    resolved_path = file_path.expanduser()
    if not resolved_path.exists() or not resolved_path.is_file():
        return ClaudeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    if resolved_path.name.endswith(".meta.json"):
        return ClaudeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    try:
        raw_text = resolved_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ClaudeScanResult(
            source_path=resolved_path,
            files_seen=0,
            rows_seen=0,
            rows_skipped=0,
            events=[],
        )

    if resolved_path.suffix == ".json" and not resolved_path.name.endswith(".jsonl"):
        events, rows_seen, rows_skipped = _parse_single_json_file(
            resolved_path,
            raw_text,
            include_raw_json=include_raw_json,
            parent_cache=parent_cache or {},
        )
    else:
        events, rows_seen, rows_skipped = _parse_jsonl_file(
            resolved_path,
            raw_text,
            include_raw_json=include_raw_json,
            parent_cache=parent_cache or {},
        )

    if source_session_id is not None:
        kept = [
            event for event in events if event.source_session_id == source_session_id
        ]
        rows_skipped += len(events) - len(kept)
        events = kept

    return ClaudeScanResult(
        source_path=resolved_path,
        files_seen=1,
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        events=events,
    )


def parse_claude_file(path: Path) -> list[UsageEvent]:
    return scan_claude_file(path).events


def parse_claude_path(path: Path) -> list[UsageEvent]:
    return scan_claude_path(path).events


def list_claude_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[ClaudeSessionSummary]:
    scan = scan_claude_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        CLAUDE_HARNESS,
        scan.events,
        source_paths_by_session=_claude_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


# ---------------------------------------------------------------------------
# Internal: single JSON file parsing
# ---------------------------------------------------------------------------


def _parse_single_json_file(
    path: Path,
    raw_text: str,
    *,
    include_raw_json: bool,
    parent_cache: ParentSubagentTypeCache,
) -> tuple[list[UsageEvent], int, int]:
    parsed = _json_loads(raw_text)
    if parsed is None:
        return [], 1, 1

    file_mtime_ms = _file_modified_timestamp_ms(path)

    # Try headless streaming format first
    if isinstance(parsed.get("type"), str) and parsed["type"] in (
        "message_start",
        "message_delta",
        "message_stop",
    ):
        events, rows_seen, rows_skipped = _parse_headless_json_file(
            path,
            [parsed],
            file_mtime_ms=file_mtime_ms,
            include_raw_json=include_raw_json,
            parent_cache=parent_cache,
        )
        return events, rows_seen, rows_skipped

    # Try assistant-style or direct usage
    events = _parse_single_json_object(
        path,
        parsed,
        file_mtime_ms=file_mtime_ms,
        include_raw_json=include_raw_json,
        parent_cache=parent_cache,
    )
    if events:
        return events, 1, 0

    return [], 1, 1


def _parse_single_json_object(
    path: Path,
    entry: dict[str, object],
    *,
    file_mtime_ms: int,
    include_raw_json: bool,
    parent_cache: ParentSubagentTypeCache,
) -> list[UsageEvent]:
    session_id, is_sidechain, agent = _resolve_file_metadata(
        path, entry, parent_cache=parent_cache
    )
    source_session_id = session_id or path.stem

    model = _extract_model(entry)
    usage = _extract_usage(entry)
    timestamp_ms = _extract_timestamp(entry) or file_mtime_ms

    if model is None or usage is None:
        return []

    raw_json = (
        json.dumps(entry, sort_keys=True, separators=(",", ":"))
        if include_raw_json
        else None
    )

    event = _build_event(
        source_session_id=source_session_id,
        source_row_id=f"{path.as_posix()}:0",
        source_message_id=None,
        source_dedup_key=f"{path.as_posix()}:0",
        model=model,
        tokens=usage,
        created_ms=timestamp_ms,
        agent=agent if is_sidechain else None,
        raw_json=raw_json,
    )
    return [event]


# ---------------------------------------------------------------------------
# Internal: JSONL file parsing
# ---------------------------------------------------------------------------


def _parse_jsonl_file(
    path: Path,
    raw_text: str,
    *,
    include_raw_json: bool,
    parent_cache: ParentSubagentTypeCache,
) -> tuple[list[UsageEvent], int, int]:
    lines = raw_text.splitlines()
    file_mtime_ms = _file_modified_timestamp_ms(path)

    # Check if this looks like a headless streaming file
    first_non_empty = _first_jsonl_entry(lines)
    if first_non_empty is not None:
        entry_type = _as_str(first_non_empty.get("type"))
        if entry_type in ("message_start", "message_delta", "message_stop"):
            return _parse_headless_json_file(
                path,
                lines,
                file_mtime_ms=file_mtime_ms,
                include_raw_json=include_raw_json,
                parent_cache=parent_cache,
            )

    return _parse_regular_jsonl(
        path,
        lines,
        file_mtime_ms=file_mtime_ms,
        include_raw_json=include_raw_json,
        parent_cache=parent_cache,
    )


def _first_jsonl_entry(lines: list[str]) -> dict[str, object] | None:
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        parsed = _json_loads(stripped)
        if parsed is not None:
            return parsed
    return None


def _parse_regular_jsonl(
    path: Path,
    lines: list[str],
    *,
    file_mtime_ms: int,
    include_raw_json: bool,
    parent_cache: ParentSubagentTypeCache,
) -> tuple[list[UsageEvent], int, int]:
    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    processed_hashes: dict[str, int] = {}

    session_id: str | None = None
    is_sidechain = False
    agent: str | None = None
    metadata_resolved = False

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        rows_seen += 1
        entry = _json_loads(stripped)
        if entry is None:
            rows_skipped += 1
            continue

        if not metadata_resolved:
            session_id, is_sidechain, agent = _resolve_file_metadata(
                path, entry, parent_cache=parent_cache
            )
            metadata_resolved = True

        source_session_id = session_id or path.stem
        entry_type = _as_str(entry.get("type"))

        if entry_type == "user":
            rows_skipped += 1
            continue

        if entry_type != "assistant":
            rows_skipped += 1
            continue

        message = _as_mapping(entry.get("message"))
        if message is None:
            rows_skipped += 1
            continue

        usage_mapping = _as_mapping(message.get("usage"))
        if usage_mapping is None:
            rows_skipped += 1
            continue

        model = _as_str(message.get("model"))
        if model is None or not model.strip():
            # Model is required for assistant events.
            # If this row has a dedup key but no model, skip it without
            # recording in processed_hashes to avoid stale indexes.
            rows_skipped += 1
            continue

        tokens = _extract_usage_from_mapping(usage_mapping)
        timestamp_ms = _extract_timestamp(entry) or file_mtime_ms

        message_id = _as_str(message.get("id"))
        request_id = _as_str(entry.get("requestId"))
        source_message_id: str | None

        source_row_id = f"{path.as_posix()}:{line_number}"

        if message_id is not None and request_id is not None:
            dedup_key = f"{message_id}:{request_id}"
            source_message_id = message_id
            # composite dedup key
        else:
            dedup_key = source_row_id
            source_message_id = message_id
            # row-based dedup; no composite key available

        raw_json = (
            json.dumps(entry, sort_keys=True, separators=(",", ":"))
            if include_raw_json
            else None
        )

        existing_index = processed_hashes.get(dedup_key)
        if existing_index is not None:
            existing = events[existing_index]
            merged_tokens = TokenBreakdown(
                input=max(existing.tokens.input, tokens.input),
                output=max(existing.tokens.output, tokens.output),
                reasoning=0,
                cache_read=max(existing.tokens.cache_read, tokens.cache_read),
                cache_write=max(existing.tokens.cache_write, tokens.cache_write),
                cache_output=max(existing.tokens.cache_output, tokens.cache_output),
            )
            updated = replace(existing, tokens=merged_tokens)
            updated = replace(updated, fingerprint_hash=_make_fingerprint(updated))
            events[existing_index] = updated
            continue

        event = _build_event(
            source_session_id=source_session_id,
            source_row_id=source_row_id,
            source_message_id=source_message_id,
            source_dedup_key=dedup_key,
            model=model,
            tokens=tokens,
            created_ms=timestamp_ms,
            agent=agent if is_sidechain else None,
            raw_json=raw_json,
        )
        events.append(event)
        processed_hashes[dedup_key] = len(events) - 1

    return events, rows_seen, rows_skipped


# ---------------------------------------------------------------------------
# Internal: headless streaming JSON/JSONL
# ---------------------------------------------------------------------------


def _parse_headless_json_file(
    path: Path,
    lines_or_entries: Sequence[str | dict[str, object]],
    *,
    file_mtime_ms: int,
    include_raw_json: bool,
    parent_cache: ParentSubagentTypeCache,
) -> tuple[list[UsageEvent], int, int]:
    session_id, is_sidechain, agent = _resolve_file_metadata(
        path, {}, parent_cache=parent_cache
    )
    source_session_id = session_id or path.stem

    rows_seen = 0
    rows_skipped = 0
    events: list[UsageEvent] = []
    state = _ClaudeHeadlessState()

    def finalize_state() -> None:
        nonlocal rows_skipped
        if state.model is None:
            return
        if (
            state.input == 0
            and state.output == 0
            and state.cache_read == 0
            and state.cache_write == 0
        ):
            return
        line_idx = rows_seen
        event = _build_event(
            source_session_id=source_session_id,
            source_row_id=f"{path.as_posix()}:{line_idx}",
            source_message_id=None,
            source_dedup_key=f"{path.as_posix()}:{line_idx}",
            model=state.model,
            tokens=TokenBreakdown(
                input=state.input,
                output=state.output,
                reasoning=0,
                cache_read=state.cache_read,
                cache_write=state.cache_write,
                cache_output=state.cache_output,
            ),
            created_ms=state.timestamp_ms or file_mtime_ms,
            agent=agent if is_sidechain else None,
            raw_json=None,
        )
        events.append(event)

    for item in lines_or_entries:
        if isinstance(item, str):
            stripped = item.strip()
            if not stripped:
                continue
            entry = _json_loads(stripped)
            if entry is None:
                rows_seen += 1
                rows_skipped += 1
                continue
        elif isinstance(item, dict):
            entry = item

        rows_seen += 1
        entry_type = _as_str(entry.get("type"))

        if entry_type == "message_start":
            finalize_state()
            state = _ClaudeHeadlessState()
            msg = _as_mapping(entry.get("message"))
            if msg is not None:
                state.model = _as_str(msg.get("model"))
                state.timestamp_ms = _extract_timestamp(msg) or _extract_timestamp(
                    entry
                )
                usage = _as_mapping(msg.get("usage"))
                if usage is not None:
                    _merge_headless_usage(state, usage)
            elif _as_str(entry.get("model")):
                state.model = _as_str(entry.get("model"))
                state.timestamp_ms = _extract_timestamp(entry)
                usage = _as_mapping(entry.get("usage"))
                if usage is not None:
                    _merge_headless_usage(state, usage)

        elif entry_type == "message_delta":
            delta = _as_mapping(entry.get("delta")) or entry
            usage = _as_mapping(delta.get("usage"))
            if usage is not None:
                _merge_headless_usage(state, usage)
            if state.model is None:
                model = _as_str(delta.get("model"))
                if model:
                    state.model = model

        elif entry_type == "message_stop":
            finalize_state()
            state = _ClaudeHeadlessState()

        else:
            # Try direct usage row
            model = _extract_model(entry)
            usage_tokens = _extract_usage(entry)
            if model is not None and usage_tokens is not None:
                finalize_state()
                state = _ClaudeHeadlessState()
                line_idx = rows_seen
                event = _build_event(
                    source_session_id=source_session_id,
                    source_row_id=f"{path.as_posix()}:{line_idx}",
                    source_message_id=None,
                    source_dedup_key=f"{path.as_posix()}:{line_idx}",
                    model=model,
                    tokens=usage_tokens,
                    created_ms=_extract_timestamp(entry) or file_mtime_ms,
                    agent=agent if is_sidechain else None,
                    raw_json=None,
                )
                events.append(event)

    finalize_state()
    return events, rows_seen, rows_skipped


def _merge_headless_usage(
    state: _ClaudeHeadlessState, usage: Mapping[str, object]
) -> None:
    state.input = max(state.input, _as_non_negative_int(usage.get("input_tokens")))
    state.output = max(state.output, _as_non_negative_int(usage.get("output_tokens")))
    state.cache_read = max(
        state.cache_read,
        _as_non_negative_int(usage.get("cache_read_input_tokens")),
    )
    state.cache_write = max(
        state.cache_write,
        _as_non_negative_int(usage.get("cache_creation_input_tokens")),
    )


# ---------------------------------------------------------------------------
# Internal: file metadata resolution
# ---------------------------------------------------------------------------


def _resolve_file_metadata(
    path: Path,
    first_entry: dict[str, object],
    *,
    parent_cache: ParentSubagentTypeCache,
) -> tuple[str | None, bool, str | None]:
    """Return (session_id, is_sidechain, agent_name)."""
    is_sidechain = bool(first_entry.get("isSidechain"))

    if is_sidechain:
        session_id = _as_str(first_entry.get("sessionId")) or path.stem
        agent = _resolve_sidechain_agent(path, parent_cache=parent_cache)
        return session_id, True, agent

    # Main session: session_id from file stem or entry
    session_id = _as_str(first_entry.get("sessionId")) or path.stem
    return session_id, False, None


def _resolve_sidechain_agent(
    path: Path,
    *,
    parent_cache: ParentSubagentTypeCache,
) -> str:
    """Resolve agent name for a sidechain file."""
    # Tier 1: sibling meta sidecar
    meta_path = path.with_name(path.stem + ".meta.json")
    if meta_path.exists():
        try:
            meta_text = meta_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            meta_text = None
        if meta_text is not None:
            meta_data = _json_loads(meta_text)
            if meta_data is not None:
                agent_type = _as_str(meta_data.get("agentType"))
                if agent_type:
                    return _normalize_agent_name(agent_type)

    # Tier 2: parent session inference
    agent_id = _sidechain_agent_id_from_stem(path.stem)
    if agent_id is not None:
        parent_path = _find_parent_jsonl(path)
        if parent_path is not None:
            parent_types = _load_parent_subagent_types(
                parent_path, parent_cache=parent_cache
            )
            subagent_type = parent_types.get(agent_id)
            if subagent_type:
                return _normalize_agent_name(subagent_type)

    # Tier 3: fallback
    return "Claude Code Subagent"


def _find_parent_jsonl(sidechain_path: Path) -> Path | None:
    """Find the parent session JSONL for a sidechain file."""
    parent_dir = sidechain_path.parent

    # Nested layout: .../projects/<workspace>/<parent-session>/subagents/agent-X.jsonl
    if parent_dir.name == "subagents":
        workspace_dir = parent_dir.parent
        for candidate in sorted(workspace_dir.glob("*.jsonl")):
            if candidate.name.endswith(".meta.json"):
                continue
            return candidate
        return None

    # Flat layout: .../projects/<workspace>/agent-X.jsonl
    workspace_dir = parent_dir
    for candidate in sorted(workspace_dir.glob("*.jsonl")):
        if candidate.name.endswith(".meta.json"):
            continue
        if candidate.stem == sidechain_path.stem:
            continue
        return candidate

    return None


def _load_parent_subagent_types(
    parent_path: Path,
    *,
    parent_cache: ParentSubagentTypeCache,
) -> dict[str, str]:
    if parent_path in parent_cache:
        return parent_cache[parent_path]

    try:
        raw_text = parent_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        parent_cache[parent_path] = {}
        return {}

    mapping: dict[str, str] = {}
    tool_use_blocks: dict[str, str] = {}

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        entry = _json_loads(stripped)
        if entry is None:
            continue
        entry_type = _as_str(entry.get("type"))
        if entry_type == "assistant":
            _extract_tool_use_agents(entry, tool_use_blocks)
        elif entry_type == "user":
            _extract_tool_result_agents(entry, tool_use_blocks, mapping)

    parent_cache[parent_path] = mapping
    return mapping


def _extract_tool_use_agents(
    entry: dict[str, object],
    tool_use_blocks: dict[str, str],
) -> None:
    message = _as_mapping(entry.get("message"))
    if message is None:
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        block_map = _as_mapping(block)
        if block_map is None:
            continue
        if _as_str(block_map.get("type")) != "tool_use":
            continue
        name = _as_str(block_map.get("name"))
        block_id = _as_str(block_map.get("id"))
        inp = _as_mapping(block_map.get("input"))
        if name == "Agent" and block_id and inp:
            sub_type = _as_str(inp.get("subagent_type"))
            if sub_type:
                tool_use_blocks[block_id] = sub_type


def _extract_tool_result_agents(
    entry: dict[str, object],
    tool_use_blocks: dict[str, str],
    mapping: dict[str, str],
) -> None:
    message = _as_mapping(entry.get("message"))
    if message is None:
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        block_map = _as_mapping(block)
        if block_map is None:
            continue
        if _as_str(block_map.get("type")) != "tool_result":
            continue
        tool_use_id = _as_str(block_map.get("tool_use_id"))
        if not tool_use_id:
            continue
        text = _extract_text_from_content_block(block_map)
        if text is None:
            continue
        agent_id = _extract_agent_id_from_text(text)
        if agent_id and tool_use_id in tool_use_blocks:
            mapping[agent_id] = tool_use_blocks[tool_use_id]


def _extract_text_from_content_block(
    block: dict[str, object],
) -> str | None:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            item_map = _as_mapping(item)
            if item_map is None:
                continue
            if _as_str(item_map.get("type")) == "text":
                text = _as_str(item_map.get("text"))
                if text:
                    return text
    return None


_AGENT_ID_RE = re.compile(r"agentId:\s*([A-Za-z0-9]+)")


def _extract_agent_id_from_text(text: str) -> str | None:
    match = _AGENT_ID_RE.search(text)
    if match:
        return match.group(1)
    return None


def _sidechain_agent_id_from_stem(stem: str) -> str | None:
    agent_stem = stem.removeprefix("agent-")
    if agent_stem == stem:
        return None
    if "-" not in agent_stem:
        return agent_stem
    trailing = agent_stem.rsplit("-", 1)[-1]
    if all(ch in string.hexdigits for ch in trailing):
        return trailing
    return agent_stem


def _normalize_agent_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        return "Claude Code Subagent"

    # Strip common prefixes
    for prefix in ("claude-code-", "oh-my-claudecode:"):
        if name.lower().startswith(prefix):
            name = name[len(prefix) :]
            break

    # Replace hyphens with spaces, title-case
    name = name.replace("-", " ").strip()
    if not name:
        return "Claude Code Subagent"

    return name.title()


# ---------------------------------------------------------------------------
# Internal: model / usage / timestamp extraction
# ---------------------------------------------------------------------------


def _extract_model(entry: dict[str, object]) -> str | None:
    model = _as_str(entry.get("model"))
    if model:
        return model
    message = _as_mapping(entry.get("message"))
    if message is not None:
        model = _as_str(message.get("model"))
        if model:
            return model
    return None


def _extract_usage(entry: dict[str, object]) -> TokenBreakdown | None:
    usage = _as_mapping(entry.get("usage"))
    if usage is not None:
        return _extract_usage_from_mapping(usage)
    message = _as_mapping(entry.get("message"))
    if message is not None:
        usage = _as_mapping(message.get("usage"))
        if usage is not None:
            return _extract_usage_from_mapping(usage)
    return None


def _extract_usage_from_mapping(usage: Mapping[str, object]) -> TokenBreakdown:
    return TokenBreakdown(
        input=_as_non_negative_int(usage.get("input_tokens")),
        output=_as_non_negative_int(usage.get("output_tokens")),
        reasoning=0,
        cache_read=_as_non_negative_int(usage.get("cache_read_input_tokens")),
        cache_write=_as_non_negative_int(usage.get("cache_creation_input_tokens")),
        cache_output=_as_non_negative_int(usage.get("cache_read_output_tokens")),
    )


def _extract_timestamp(entry: dict[str, object]) -> int | None:
    ts = _parse_iso_ms(entry.get("timestamp"))
    if ts is not None:
        return ts
    ts = _parse_iso_ms(entry.get("created_at"))
    if ts is not None:
        return ts
    message = _as_mapping(entry.get("message"))
    if message is not None:
        ts = _parse_iso_ms(message.get("created_at"))
        if ts is not None:
            return ts
    return None


# ---------------------------------------------------------------------------
# Internal: event construction
# ---------------------------------------------------------------------------


def _build_event(
    *,
    source_session_id: str,
    source_row_id: str,
    source_message_id: str | None,
    source_dedup_key: str,
    model: str,
    tokens: TokenBreakdown,
    created_ms: int,
    agent: str | None,
    raw_json: str | None,
) -> UsageEvent:
    event = UsageEvent(
        harness=CLAUDE_HARNESS,
        source_session_id=source_session_id,
        source_row_id=source_row_id,
        source_message_id=source_message_id,
        source_dedup_key=source_dedup_key,
        global_dedup_key=f"claude:{source_session_id}:{source_dedup_key}",
        fingerprint_hash="",
        provider_id="anthropic",
        model_id=model,
        thinking_level=None,
        agent=agent,
        created_ms=created_ms,
        completed_ms=None,
        tokens=tokens,
        source_cost_usd=Decimal(0),
        raw_json=raw_json,
    )
    return replace(event, fingerprint_hash=_make_fingerprint(event))


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
        "agent": event.agent,
        "thinking_level": event.thinking_level,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Internal: source path helpers
# ---------------------------------------------------------------------------


def _claude_source_paths_by_session(
    source_path: Path,
) -> dict[str, list[Path]]:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return {}

    if resolved_path.is_file():
        file_paths = [resolved_path]
    else:
        collected: list[Path] = []
        for pattern in ("*.jsonl", "*.json"):
            collected.extend(resolved_path.rglob(pattern))
        file_paths = sorted(p for p in collected if not p.name.endswith(".meta.json"))

    paths: dict[str, list[Path]] = {}
    for file_path in file_paths:
        try:
            raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if file_path.suffix == ".json" and not file_path.name.endswith(".jsonl"):
            parsed = _json_loads(raw_text)
            if parsed is not None:
                session_id = _as_str(parsed.get("sessionId")) or file_path.stem
                paths.setdefault(session_id, []).append(file_path)
            continue

        # JSONL: look at first line for session info
        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = _json_loads(stripped)
            if parsed is not None:
                if parsed.get("isSidechain"):
                    session_id = _as_str(parsed.get("sessionId")) or file_path.stem
                else:
                    session_id = _as_str(parsed.get("sessionId")) or file_path.stem
                paths.setdefault(session_id, []).append(file_path)
            break

    return paths


# ---------------------------------------------------------------------------
# Internal: utility helpers
# ---------------------------------------------------------------------------


def _file_modified_timestamp_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def _json_loads(text: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(text)
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


def _as_non_negative_int(value: object) -> int:
    numeric = _number_value(value)
    if numeric is None or numeric < 0:
        return 0
    return int(numeric)


def _number_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _parse_iso_ms(value: object) -> int | None:
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
