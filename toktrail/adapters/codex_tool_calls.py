"""Read-only Codex tool-call scanner for session health diagnostics.

This module extracts tool calls (commands, function calls, and their results)
from Codex JSONL source logs. It produces ``CodexToolCall`` rows for display
and reporting, *not* for persistence in the usage_events table.

Tool-call diagnostics are separate from token accounting. No rows are written
to SQLite. The scanner is intentionally read-only.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BAD_STATUSES = frozenset({"failed", "timeout", "cancelled"})

# Key path prefixes for nested payloads in Codex JSONL.
CANDIDATE_PATHS: tuple[tuple[str, ...], ...] = (
    (),
    ("payload",),
    ("payload", "item"),
    ("payload", "result"),
    ("payload", "output"),
    ("data",),
    ("data", "item"),
    ("result",),
    ("response",),
)


@dataclass(frozen=True)
class CodexToolCall:
    ordinal: int
    source_session_id: str
    source_path: Path
    line_number: int
    result_line_number: int | None
    created_ms: int | None
    completed_ms: int | None
    call_id: str | None
    tool_name: str
    status: str  # success | failed | timeout | cancelled | unknown
    failure_kind: str | None
    cwd: str | None
    command: str | None
    arguments_json: str | None
    exit_code: int | None
    duration_ms: int | None
    error: str | None
    stdout_snippet: str | None
    stderr_snippet: str | None
    raw_json: str | None = None


@dataclass(frozen=True)
class CodexToolCallScanResult:
    source_path: Path
    source_session_id: str | None
    files_seen: int
    rows_seen: int
    tool_call_count: int
    failure_count: int
    timeout_count: int
    calls: tuple[CodexToolCall, ...]
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Public scanner
# ---------------------------------------------------------------------------


def scan_codex_tool_calls(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = False,
    include_output: bool = False,
    max_snippet_chars: int = 1000,
) -> CodexToolCallScanResult:
    """Scan Codex JSONL source logs for tool-call events.

    This is read-only: it never writes to SQLite.
    """
    resolved = source_path.expanduser()
    if not resolved.exists():
        return CodexToolCallScanResult(
            source_path=resolved,
            source_session_id=source_session_id,
            files_seen=0,
            rows_seen=0,
            tool_call_count=0,
            failure_count=0,
            timeout_count=0,
            calls=(),
        )

    if resolved.is_file():
        file_paths = [resolved]
    else:
        file_paths = sorted(p for p in resolved.rglob("*.jsonl") if p.is_file())

    if not file_paths:
        return CodexToolCallScanResult(
            source_path=resolved,
            source_session_id=source_session_id,
            files_seen=0,
            rows_seen=0,
            tool_call_count=0,
            failure_count=0,
            timeout_count=0,
            calls=(),
        )

    all_calls: list[CodexToolCall] = []
    total_rows = 0
    warnings: list[str] = []

    for file_path in file_paths:
        session_id = _session_id_from_path(file_path)
        if source_session_id is not None and session_id != source_session_id:
            continue
        rows, calls, file_warnings = _scan_codex_tool_call_file(
            file_path,
            source_session_id=session_id,
            include_raw_json=include_raw_json,
            include_output=include_output,
            max_snippet_chars=max_snippet_chars,
        )
        total_rows += rows
        all_calls.extend(calls)
        warnings.extend(file_warnings)

    # Re-assign ordinals globally across all files
    calls_with_ordinals: list[CodexToolCall] = []
    for idx, call in enumerate(all_calls, start=1):
        calls_with_ordinals.append(
            CodexToolCall(
                ordinal=idx,
                source_session_id=call.source_session_id,
                source_path=call.source_path,
                line_number=call.line_number,
                result_line_number=call.result_line_number,
                created_ms=call.created_ms,
                completed_ms=call.completed_ms,
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=call.status,
                failure_kind=call.failure_kind,
                cwd=call.cwd,
                command=call.command,
                arguments_json=call.arguments_json,
                exit_code=call.exit_code,
                duration_ms=call.duration_ms,
                error=call.error,
                stdout_snippet=call.stdout_snippet,
                stderr_snippet=call.stderr_snippet,
                raw_json=call.raw_json,
            )
        )

    failure_count = sum(1 for c in calls_with_ordinals if c.status in BAD_STATUSES)
    timeout_count = sum(1 for c in calls_with_ordinals if c.status == "timeout")

    return CodexToolCallScanResult(
        source_path=resolved,
        source_session_id=source_session_id,
        files_seen=len(file_paths),
        rows_seen=total_rows,
        tool_call_count=len(calls_with_ordinals),
        failure_count=failure_count,
        timeout_count=timeout_count,
        calls=tuple(calls_with_ordinals),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# File-level scanner
# ---------------------------------------------------------------------------


def _scan_codex_tool_call_file(
    file_path: Path,
    *,
    source_session_id: str,
    include_raw_json: bool = False,
    include_output: bool = False,
    max_snippet_chars: int = 1000,
) -> tuple[int, list[CodexToolCall], list[str]]:
    """Scan a single Codex JSONL file for tool-call events."""
    pending_by_call_id: dict[str, _PendingCall] = {}
    results: list[CodexToolCall] = []
    lines_scanned = 0
    local_ordinal = 0
    warnings: list[str] = []

    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                lines_scanned += 1

                normalized = _normalize_tool_event(entry)
                if normalized is None:
                    continue

                if normalized["role"] == "start":
                    call_id = normalized.get("call_id")
                    if call_id is not None and call_id in pending_by_call_id:
                        prev = pending_by_call_id.pop(call_id)
                        local_ordinal += 1
                        results.append(
                            _pending_to_call(
                                prev,
                                ordinal=local_ordinal,
                                source_session_id=source_session_id,
                                file_path=file_path,
                                include_raw_json=include_raw_json,
                            )
                        )
                    if call_id is not None:
                        pending_by_call_id[call_id] = _PendingCall(
                            line_number=line_number,
                            created_ms=normalized.get("created_ms"),
                            call_id=call_id,
                            tool_name=normalized.get("tool_name", "unknown"),
                            cwd=normalized.get("cwd"),
                            command=normalized.get("command"),
                            arguments_json=normalized.get("arguments_json"),
                            raw_json=(stripped if include_raw_json else None),
                        )
                    else:
                        local_ordinal += 1
                        status, failure_kind = _classify_status(normalized)
                        results.append(
                            CodexToolCall(
                                ordinal=local_ordinal,
                                source_session_id=source_session_id,
                                source_path=file_path,
                                line_number=line_number,
                                result_line_number=None,
                                created_ms=normalized.get("created_ms"),
                                completed_ms=None,
                                call_id=None,
                                tool_name=normalized.get("tool_name", "unknown"),
                                status=status,
                                failure_kind=failure_kind,
                                cwd=normalized.get("cwd"),
                                command=normalized.get("command"),
                                arguments_json=normalized.get("arguments_json"),
                                exit_code=normalized.get("exit_code"),
                                duration_ms=normalized.get("duration_ms"),
                                error=normalized.get("error"),
                                stdout_snippet=(
                                    _snippet(
                                        normalized.get("stdout"),
                                        limit=max_snippet_chars,
                                    )
                                    if include_output
                                    else None
                                ),
                                stderr_snippet=(
                                    _snippet(
                                        normalized.get("stderr"),
                                        limit=max_snippet_chars,
                                    )
                                    if include_output
                                    else None
                                ),
                                raw_json=(stripped if include_raw_json else None),
                            )
                        )

                elif normalized["role"] == "result":
                    call_id = normalized.get("call_id")
                    if call_id is not None and call_id in pending_by_call_id:
                        prev = pending_by_call_id.pop(call_id)
                        local_ordinal += 1
                        results.append(
                            _merge_call_and_result(
                                prev=prev,
                                result=normalized,
                                ordinal=local_ordinal,
                                result_line_number=line_number,
                                source_session_id=source_session_id,
                                file_path=file_path,
                                include_raw_json=include_raw_json,
                                include_output=include_output,
                                max_snippet_chars=max_snippet_chars,
                            )
                        )
                    else:
                        local_ordinal += 1
                        status, failure_kind = _classify_status(normalized)
                        results.append(
                            CodexToolCall(
                                ordinal=local_ordinal,
                                source_session_id=source_session_id,
                                source_path=file_path,
                                line_number=line_number,
                                result_line_number=None,
                                created_ms=normalized.get("created_ms"),
                                completed_ms=normalized.get("completed_ms"),
                                call_id=call_id,
                                tool_name=normalized.get("tool_name", "unknown"),
                                status=status,
                                failure_kind=failure_kind,
                                cwd=normalized.get("cwd"),
                                command=normalized.get("command"),
                                arguments_json=normalized.get("arguments_json"),
                                exit_code=normalized.get("exit_code"),
                                duration_ms=normalized.get("duration_ms"),
                                error=normalized.get("error"),
                                stdout_snippet=(
                                    _snippet(
                                        normalized.get("stdout"),
                                        limit=max_snippet_chars,
                                    )
                                    if include_output
                                    else None
                                ),
                                stderr_snippet=(
                                    _snippet(
                                        normalized.get("stderr"),
                                        limit=max_snippet_chars,
                                    )
                                    if include_output
                                    else None
                                ),
                                raw_json=(stripped if include_raw_json else None),
                            )
                        )

                elif normalized["role"] == "standalone":
                    local_ordinal += 1
                    status, failure_kind = _classify_status(normalized)
                    results.append(
                        CodexToolCall(
                            ordinal=local_ordinal,
                            source_session_id=source_session_id,
                            source_path=file_path,
                            line_number=line_number,
                            result_line_number=None,
                            created_ms=normalized.get("created_ms"),
                            completed_ms=normalized.get("completed_ms"),
                            call_id=normalized.get("call_id"),
                            tool_name=normalized.get("tool_name", "unknown"),
                            status=status,
                            failure_kind=failure_kind,
                            cwd=normalized.get("cwd"),
                            command=normalized.get("command"),
                            arguments_json=normalized.get("arguments_json"),
                            exit_code=normalized.get("exit_code"),
                            duration_ms=normalized.get("duration_ms"),
                            error=normalized.get("error"),
                            stdout_snippet=(
                                _snippet(
                                    normalized.get("stdout"),
                                    limit=max_snippet_chars,
                                )
                                if include_output
                                else None
                            ),
                            stderr_snippet=(
                                _snippet(
                                    normalized.get("stderr"),
                                    limit=max_snippet_chars,
                                )
                                if include_output
                                else None
                            ),
                            raw_json=(stripped if include_raw_json else None),
                        )
                    )
    except OSError:
        warnings.append(f"Could not read {file_path}")

    # Flush remaining pending starts as unknown-status calls
    for pending in pending_by_call_id.values():
        local_ordinal += 1
        results.append(
            CodexToolCall(
                ordinal=local_ordinal,
                source_session_id=source_session_id,
                source_path=file_path,
                line_number=pending.line_number,
                result_line_number=None,
                created_ms=pending.created_ms,
                completed_ms=None,
                call_id=pending.call_id,
                tool_name=pending.tool_name,
                status="unknown",
                failure_kind="missing_result",
                cwd=pending.cwd,
                command=pending.command,
                arguments_json=pending.arguments_json,
                exit_code=None,
                duration_ms=None,
                error=None,
                stdout_snippet=None,
                stderr_snippet=None,
                raw_json=pending.raw_json,
            )
        )

    return lines_scanned, results, warnings


# ---------------------------------------------------------------------------
# Internal data classes and helpers
# ---------------------------------------------------------------------------


@dataclass
class _PendingCall:
    line_number: int
    created_ms: int | None
    call_id: str
    tool_name: str
    cwd: str | None
    command: str | None
    arguments_json: str | None
    raw_json: str | None


def _pending_to_call(
    pending: _PendingCall,
    *,
    ordinal: int,
    source_session_id: str,
    file_path: Path,
    include_raw_json: bool,
) -> CodexToolCall:
    return CodexToolCall(
        ordinal=ordinal,
        source_session_id=source_session_id,
        source_path=file_path,
        line_number=pending.line_number,
        result_line_number=None,
        created_ms=pending.created_ms,
        completed_ms=None,
        call_id=pending.call_id,
        tool_name=pending.tool_name,
        status="unknown",
        failure_kind="missing_result",
        cwd=pending.cwd,
        command=pending.command,
        arguments_json=pending.arguments_json,
        exit_code=None,
        duration_ms=None,
        error=None,
        stdout_snippet=None,
        stderr_snippet=None,
        raw_json=pending.raw_json if include_raw_json else None,
    )


def _merge_call_and_result(
    *,
    prev: _PendingCall,
    result: dict[str, object],
    ordinal: int,
    result_line_number: int,
    source_session_id: str,
    file_path: Path,
    include_raw_json: bool,
    include_output: bool,
    max_snippet_chars: int,
) -> CodexToolCall:
    status, failure_kind = _classify_status(result)
    tool_name = _as_str(result.get("tool_name")) or prev.tool_name
    command = _as_str(result.get("command")) or prev.command
    cwd = _as_str(result.get("cwd")) or prev.cwd

    completed_ms = result.get("completed_ms")
    if completed_ms is not None:
        completed_ms = _as_int(completed_ms)

    return CodexToolCall(
        ordinal=ordinal,
        source_session_id=source_session_id,
        source_path=file_path,
        line_number=prev.line_number,
        result_line_number=result_line_number,
        created_ms=prev.created_ms,
        completed_ms=completed_ms,
        call_id=prev.call_id,
        tool_name=tool_name,
        status=status,
        failure_kind=failure_kind,
        cwd=cwd,
        command=command,
        arguments_json=prev.arguments_json,
        exit_code=result.get("exit_code"),
        duration_ms=result.get("duration_ms"),
        error=result.get("error"),
        stdout_snippet=(
            _snippet(result.get("stdout"), limit=max_snippet_chars)
            if include_output
            else None
        ),
        stderr_snippet=(
            _snippet(result.get("stderr"), limit=max_snippet_chars)
            if include_output
            else None
        ),
        raw_json=prev.raw_json if include_raw_json else None,
    )


# ---------------------------------------------------------------------------
# Event normalization
# ---------------------------------------------------------------------------


def _normalize_tool_event(
    entry: Mapping[str, object],
) -> dict[str, object] | None:
    """Try to extract a tool-call event from a Codex JSON line."""
    for path in CANDIDATE_PATHS:
        candidate = _dig(entry, path)
        if candidate is None:
            continue
        result = _try_normalize_from_mapping(candidate, entry)
        if result is not None:
            return result
    return _try_normalize_from_mapping(entry, entry)


def _try_normalize_from_mapping(
    mapping: Mapping[str, object],
    root: Mapping[str, object],
) -> dict[str, object] | None:
    """Attempt to extract a normalized tool event from a mapping."""
    raw_type = _as_str(mapping.get("type"))

    # Skip non-tool events
    if raw_type == "token_count" or raw_type == "turn_context":
        return None
    if raw_type in (
        "session_meta",
        "message",
        "response_start",
        "response_end",
        "summary",
    ):
        return None

    is_call = _is_call_type(raw_type, mapping)
    is_result = _is_result_type(raw_type, mapping)

    if not is_call and not is_result:
        return None

    # Extract common fields
    created_ms = _timestamp_ms_from_value(
        root.get("timestamp") or mapping.get("timestamp") or mapping.get("created_at")
    )
    call_id = _as_str(
        mapping.get("call_id") or mapping.get("tool_call_id") or mapping.get("id")
    )
    tool_name = _extract_tool_name(mapping)
    command = _extract_command(mapping)
    cwd = _as_str(mapping.get("cwd") or mapping.get("working_directory"))

    # Status/result fields
    exit_code = _as_optional_int(
        mapping.get("exit_code") or mapping.get("returncode") or mapping.get("code")
    )
    error = _as_str(mapping.get("error") or mapping.get("stderr"))
    timed_out = mapping.get("timed_out") or mapping.get("timeout")
    is_timed_out = (
        timed_out is True
        or (isinstance(timed_out, str) and timed_out.strip().lower() == "true")
        or (isinstance(timed_out, int) and timed_out != 0)
    )
    status_str = _as_str(mapping.get("status") or mapping.get("outcome"))
    duration_ms = _as_optional_int(
        mapping.get("duration_ms") or mapping.get("elapsed_ms")
    )
    stdout = mapping.get("stdout") or mapping.get("output")
    stderr = mapping.get("stderr")

    arguments_json = None
    arguments_raw = (
        mapping.get("arguments") or mapping.get("args") or mapping.get("input")
    )
    if arguments_raw is not None:
        if isinstance(arguments_raw, str):
            arguments_json = arguments_raw
        elif isinstance(arguments_raw, dict):
            arguments_json = json.dumps(arguments_raw, sort_keys=True)

    # An exec_command with result signals is standalone, not just a start
    has_result_signals = (
        exit_code is not None
        or status_str is not None
        or is_timed_out
        or error is not None
    )

    # Role assignment with tool_name default from type
    if is_result and not is_call:
        role = "result"
        # Result row: don't assign default tool name from type
        # (will come from the start row during merge)
    elif is_call and not is_result and not has_result_signals:
        role = "start"
        tool_name = tool_name or _default_tool_name_from_type(raw_type)
    else:
        role = "standalone"
        tool_name = tool_name or _default_tool_name_from_type(raw_type)

    result_dict: dict[str, object] = {
        "role": role,
        "created_ms": created_ms,
        "call_id": call_id,
        "tool_name": tool_name,
        "command": command,
        "cwd": cwd,
        "exit_code": exit_code,
        "error": error,
        "timed_out": is_timed_out,
        "status_str": status_str,
        "duration_ms": duration_ms,
        "stdout": stdout,
        "stderr": stderr,
        "arguments_json": arguments_json,
    }

    if created_ms is not None and duration_ms is not None:
        result_dict["completed_ms"] = created_ms + duration_ms
    else:
        result_dict["completed_ms"] = None

    return result_dict


def _is_call_type(raw_type: str | None, mapping: Mapping[str, object]) -> bool:
    """Check if this event represents a tool call start."""
    if raw_type is None:
        return False
    lowered = raw_type.lower()
    if lowered in ("exec_command", "tool_call", "function_call"):
        return True
    if lowered in ("tool_call_delta",):
        return True
    if lowered in ("response_item",):
        item = _as_mapping(mapping.get("item"))
        if item is not None:
            item_type = _as_str(item.get("type"))
            if item_type and item_type.lower() in (
                "function_call",
                "tool_call",
            ):
                return True
    return False


def _is_result_type(raw_type: str | None, mapping: Mapping[str, object]) -> bool:
    """Check if this event represents a tool result/output."""
    if raw_type is None:
        return False
    lowered = raw_type.lower()
    if lowered in (
        "command_result",
        "tool_result",
        "function_call_output",
    ):
        return True
    if lowered in ("response_item",):
        item = _as_mapping(mapping.get("item"))
        if item is not None:
            item_type = _as_str(item.get("type"))
            if item_type and item_type.lower() in (
                "function_call_output",
                "tool_result",
            ):
                return True
    # exec_command with result signals is standalone
    if lowered == "exec_command" and (
        mapping.get("exit_code") is not None
        or mapping.get("stderr") is not None
        or mapping.get("stdout") is not None
    ):
        return True
    return False


def _extract_tool_name(mapping: Mapping[str, object]) -> str | None:
    return _as_str(
        mapping.get("name") or mapping.get("tool") or mapping.get("tool_name")
    )


def _default_tool_name_from_type(raw_type: str | None) -> str | None:
    if raw_type is None:
        return None
    lowered = raw_type.lower()
    if lowered in ("exec_command", "command_result"):
        return "exec_command"
    if "tool" in lowered or "function" in lowered:
        return raw_type
    return None


def _extract_command(mapping: Mapping[str, object]) -> str | None:
    return _as_str(mapping.get("command") or mapping.get("cmd"))


# ---------------------------------------------------------------------------
# Status classification
# ---------------------------------------------------------------------------


def _classify_status(
    normalized: dict[str, object],
) -> tuple[str, str | None]:
    """Classify a tool call's status."""
    # 1. Timeout
    if normalized.get("timed_out") is True:
        return "timeout", "timeout"
    timed_out_str = _as_str(normalized.get("timed_out"))
    if timed_out_str == "true":
        return "timeout", "timeout"
    status_str = _as_str(normalized.get("status_str"))
    if status_str and status_str.lower() in ("timeout", "timed_out"):
        return "timeout", "timeout"
    exit_code_val = normalized.get("exit_code")
    if isinstance(exit_code_val, int) and exit_code_val == 124:
        return "timeout", "timeout"

    # 2. Cancelled
    if status_str and status_str.lower() in (
        "cancelled",
        "canceled",
        "aborted",
        "interrupted",
    ):
        return "cancelled", "cancelled"

    # 3. Failed
    if isinstance(exit_code_val, int) and exit_code_val != 0:
        return "failed", "exit_code"
    if status_str and status_str.lower() in ("failed", "failure", "error"):
        return "failed", "error"
    error_val = _as_str(normalized.get("error"))
    if error_val:
        return "failed", "error"

    # 4. Success
    if isinstance(exit_code_val, int) and exit_code_val == 0:
        return "success", None
    if status_str and status_str.lower() in (
        "success",
        "succeeded",
        "ok",
        "completed",
    ):
        return "success", None

    # 5. Unknown
    return "unknown", None


# ---------------------------------------------------------------------------
# JSON helpers (duplicated to avoid importing private helpers)
# ---------------------------------------------------------------------------


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value)
    return None


def _as_optional_int(value: object) -> int | None:
    return _as_int(value)


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _dig(
    root: Mapping[str, object], path: tuple[str, ...]
) -> Mapping[str, object] | None:
    """Walk nested dicts following *path*."""
    current: object = root
    for key in path:
        if isinstance(current, Mapping):
            current = current.get(key)
        else:
            return None
    return _as_mapping(current)


def _snippet(value: object, *, limit: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "\u2026"


def _session_id_from_path(path: Path) -> str:
    return path.stem or "unknown"


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
