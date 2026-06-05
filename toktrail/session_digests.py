from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from time import time

from toktrail.reporting import (
    SessionDigest,
    SessionDigestSummary,
    SessionToolHealth,
    SessionTotals,
    SessionTranscriptEvent,
    UsageSessionRow,
)
from toktrail.session_health import build_session_health

DIGEST_SCHEMA_VERSION = 2
DIGEST_GENERATOR = "heuristic:v1"
_PATH_RE = re.compile(r"(?:^|\s)([A-Za-z0-9_./-]+\.[A-Za-z0-9_/-]+)")
_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)=([^\s]+)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def build_session_digest(
    *,
    usage_session: UsageSessionRow,
    transcript_events: Iterable[SessionTranscriptEvent] = (),
    generated_at_ms: int | None = None,
    source_fingerprint: str | None = None,
) -> SessionDigest:
    events = tuple(transcript_events)
    effective_generated_at_ms = generated_at_ms or int(time() * 1000)
    tool_health = summarize_tool_health(events)
    health = build_session_health(
        events,
        usage_session=usage_session,
        tool_health=tool_health,
        generated_at_ms=effective_generated_at_ms,
    )
    files = _top_values(
        _redact(value) for event in events for value in _event_file_candidates(event)
    )
    commands = _top_values(
        _redact(event.text or event.name or "")
        for event in events
        if event.kind == "command" and (event.text or event.name)
    )
    summary = build_digest_summary(
        usage_session=usage_session,
        tool_health=tool_health,
        files_mentioned=files,
        commands_mentioned=commands,
        rich_event_count=len(events),
    )
    return SessionDigest(
        schema_version=DIGEST_SCHEMA_VERSION,
        origin_machine_id=usage_session.origin_machine_id,
        machine_label=usage_session.machine_label,
        harness=usage_session.harness,
        source_session_id=usage_session.source_session_id,
        area_path=usage_session.area_path,
        cwd=_redact_optional(usage_session.cwd),
        source_dir=_redact_optional(usage_session.source_dir),
        git_root=_redact_optional(usage_session.git_root),
        git_remote=_redact_optional(usage_session.git_remote),
        session_title=_redact_optional(usage_session.session_title),
        started_ms=usage_session.first_ms,
        last_seen_ms=usage_session.last_ms,
        usage=SessionTotals(tokens=usage_session.tokens, costs=usage_session.costs),
        message_count=usage_session.message_count,
        summary=summary,
        tool_health=tool_health,
        health=health,
        files_mentioned=files,
        commands_mentioned=commands,
        contains_raw_transcript=False,
        contains_snippets=False,
        models=usage_session.models,
        providers=usage_session.providers,
        generated_at_ms=effective_generated_at_ms,
        source_fingerprint=source_fingerprint,
    )


def build_digest_summary(
    *,
    usage_session: UsageSessionRow,
    tool_health: SessionToolHealth,
    files_mentioned: tuple[str, ...],
    commands_mentioned: tuple[str, ...],
    rich_event_count: int,
) -> SessionDigestSummary:
    title = usage_session.session_title or usage_session.area_path
    if title is None:
        cwd = usage_session.cwd or usage_session.source_dir
        title = Path(cwd).name if cwd else usage_session.source_session_id
    title = _redact(title)
    one_line = f"{usage_session.harness} session: {title}"
    bullets: list[str] = []
    if files_mentioned:
        bullets.append("Mentioned files: " + ", ".join(files_mentioned[:3]))
    if commands_mentioned:
        bullets.append("Ran commands: " + ", ".join(commands_mentioned[:3]))
    if tool_health.tool_call_count:
        bullets.append(
            "Tool calls: "
            f"{tool_health.tool_call_count}, failures: {tool_health.tool_failure_count}"
        )
    if not bullets:
        bullets.append(
            "Usage-only digest from imported session metadata and token totals."
        )
    if tool_health.tool_call_count:
        confidence = "high"
    elif rich_event_count:
        confidence = "medium"
    else:
        confidence = "low"
    return SessionDigestSummary(
        one_line=one_line,
        bullets=tuple(bullets[:5]),
        confidence=confidence,
        generator=DIGEST_GENERATOR,
    )


_TOOL_NAME_ALIASES: dict[str, str] = {
    "bash": "bash",
    "shell": "bash",
    "exec": "bash",
    "exec_command": "bash",
    "command": "bash",
    "run_command": "bash",
    "read": "read",
    "read_file": "read",
    "readfile": "read",
    "view": "read",
    "edit": "edit",
    "edit_file": "edit",
    "multiedit": "edit",
    "multi_edit": "edit",
    "replace": "edit",
    "write": "write",
    "write_file": "write",
    "create_file": "write",
    "grep": "grep",
    "search": "grep",
    "ripgrep": "grep",
    "glob": "glob",
    "ls": "ls",
    "todowrite": "todowrite",
    "todo_write": "todowrite",
    "todo": "todowrite",
    "task": "task",
    "agent": "task",
    "question": "question",
    "ask_question": "question",
    "apply_patch": "apply_patch",
    "patch": "apply_patch",
}


def normalize_tool_name(name: str | None, raw_kind: str | None = None) -> str:
    raw = (name or raw_kind or "unknown").strip()
    raw = raw.replace("-", "_").replace(" ", "_")
    raw = raw.lower()
    return _TOOL_NAME_ALIASES.get(raw, raw or "unknown")


def summarize_tool_health(
    events: Iterable[SessionTranscriptEvent],
) -> SessionToolHealth:
    call_count = 0
    failure_count = 0
    timeout_count = 0
    tools: dict[str, int] = {}
    failed_tools: dict[str, int] = {}
    warnings: set[str] = set()
    for event in events:
        if event.kind in {"tool_call", "tool_result", "command", "error"}:
            call_count += 1
        if event.kind in {"tool_call", "command"}:
            name = normalize_tool_name(event.name, event.raw_kind)
            tools[name] = tools.get(name, 0) + 1
        failed = (
            event.success is False or event.kind == "error" or bool(event.error_text)
        )
        if failed:
            failure_count += 1
            name = normalize_tool_name(event.name, event.raw_kind)
            failed_tools[name] = failed_tools.get(name, 0) + 1
        text = " ".join(part for part in (event.error_text, event.text) if part)
        if "timeout" in text.lower():
            timeout_count += 1
    if not call_count:
        warnings.add("usage-only-or-no-tool-events")
    return SessionToolHealth(
        tool_call_count=call_count,
        tool_failure_count=failure_count,
        tool_timeout_count=timeout_count,
        tools=tools,
        failed_tools=failed_tools,
        warnings=tuple(sorted(warnings)),
    )


def digest_source_fingerprint(events: Iterable[SessionTranscriptEvent]) -> str | None:
    payload = [
        {
            "created_ms": event.created_ms,
            "kind": event.kind,
            "name": event.name,
            "path": event.path,
            "success": event.success,
            "raw_kind": event.raw_kind,
        }
        for event in events
    ]
    if not payload:
        return None
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def extract_codex_session_events(
    source_path: Path,
    *,
    source_session_id: str,
) -> list[SessionTranscriptEvent]:
    paths = _jsonl_paths(source_path)
    events: list[SessionTranscriptEvent] = []
    for path in paths:
        session_id = path.stem
        if session_id != source_session_id:
            continue
        events.extend(_extract_codex_file_events(path, source_session_id=session_id))
    return events


def extract_pi_session_events(
    source_path: Path,
    *,
    source_session_id: str,
) -> list[SessionTranscriptEvent]:
    events: list[SessionTranscriptEvent] = []
    for path in _jsonl_paths(source_path):
        events.extend(
            _extract_pi_file_events(path, source_session_id=source_session_id)
        )
    return events


def extract_claude_session_events(
    source_path: Path,
    *,
    source_session_id: str,
) -> list[SessionTranscriptEvent]:
    events: list[SessionTranscriptEvent] = []
    for path in _jsonl_paths(source_path):
        events.extend(
            _extract_claude_file_events(path, source_session_id=source_session_id)
        )
    return events


def _extract_claude_file_events(
    path: Path,
    *,
    source_session_id: str,
) -> list[SessionTranscriptEvent]:
    events: list[SessionTranscriptEvent] = []
    tool_names_by_id: dict[str, str] = {}
    for entry in _iter_jsonl(path):
        entry_session_id = _as_str(entry.get("sessionId")) or path.stem
        if entry_session_id != source_session_id:
            continue
        entry_type = _as_str(entry.get("type"))
        created_ms = _timestamp_ms(entry.get("timestamp"))
        message = _as_mapping(entry.get("message")) or entry

        if entry_type == "assistant":
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = _as_str(block.get("type"))
                if block_type == "tool_use":
                    tool_id = _as_str(block.get("id"))
                    tool_name = _as_str(block.get("name"))
                    if tool_id and tool_name:
                        tool_names_by_id[tool_id] = tool_name
                    name = tool_name or tool_id
                    # Extract command for bash-like tools
                    inp = _as_mapping(block.get("input"))
                    text = None
                    bash_names = {"bash", "shell", "exec", "command"}
                    if inp and tool_name and tool_name.lower() in bash_names:
                        text = _as_str(inp.get("command"))
                    elif inp:
                        text = _first_str(inp, "file_path", "filePath", "path")
                    events.append(
                        SessionTranscriptEvent(
                            harness="claude",
                            source_session_id=source_session_id,
                            created_ms=created_ms,
                            role="assistant",
                            kind="tool_call",
                            name=name,
                            text=text,
                            raw_kind=block_type,
                        )
                    )

        elif entry_type == "user":
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = _as_str(block.get("type"))
                if block_type == "tool_result":
                    tool_use_id = _as_str(block.get("tool_use_id"))
                    name = tool_names_by_id.get(tool_use_id) if tool_use_id else None
                    is_error = block.get("is_error", block.get("isError"))
                    error_text = _as_str(block.get("error")) or _as_str(
                        block.get("errorText")
                    )
                    kind = "error" if is_error is True else "tool_result"
                    success = not is_error if isinstance(is_error, bool) else None
                    events.append(
                        SessionTranscriptEvent(
                            harness="claude",
                            source_session_id=source_session_id,
                            created_ms=created_ms,
                            role="user",
                            kind=kind,
                            name=name,
                            success=success,
                            error_text=error_text,
                            raw_kind=block_type,
                        )
                    )
    return events


def _extract_codex_file_events(
    path: Path,
    *,
    source_session_id: str,
) -> list[SessionTranscriptEvent]:
    events: list[SessionTranscriptEvent] = []
    for entry in _iter_jsonl(path):
        raw_type = _as_str(entry.get("type"))
        payload = _as_mapping(entry.get("payload")) or entry
        payload_type = _as_str(payload.get("type"))
        raw_kind = payload_type or raw_type
        created_ms = _timestamp_ms(entry.get("timestamp") or payload.get("timestamp"))
        text = _first_str(payload, "cmd", "command", "text", "message")
        name = _first_str(payload, "name", "tool", "tool_name", "call_id")
        success = _success_from_mapping(payload)
        error_text = _first_str(payload, "error", "error_text", "stderr")
        kind = _event_kind(raw_kind, payload, text=text, error_text=error_text)
        if kind is None:
            continue
        events.append(
            SessionTranscriptEvent(
                harness="codex",
                source_session_id=source_session_id,
                created_ms=created_ms,
                role=_as_str(payload.get("role")),
                kind=kind,
                name=name or _default_tool_name(raw_kind),
                text=text if kind == "command" else None,
                path=_first_str(payload, "path", "file_path"),
                success=success,
                error_text=error_text,
                raw_kind=raw_kind,
            )
        )
    return events


def _extract_pi_file_events(
    path: Path,
    *,
    source_session_id: str,
) -> list[SessionTranscriptEvent]:
    events: list[SessionTranscriptEvent] = []
    current_session_id: str | None = None
    for entry in _iter_jsonl(path):
        entry_type = _as_str(entry.get("type"))
        if entry_type == "session":
            current_session_id = _as_str(entry.get("id"))
            continue
        if current_session_id != source_session_id:
            continue
        if entry_type != "message":
            continue
        message = _as_mapping(entry.get("message"))
        if message is None:
            continue
        role = _as_str(message.get("role"))
        created_ms = _timestamp_ms(entry.get("timestamp") or message.get("timestamp"))

        if role == "toolResult":
            tool_name = _as_str(message.get("toolName"))
            is_error = message.get("isError")
            kind = "error" if is_error is True else "tool_result"
            name = tool_name or _as_str(message.get("toolCallId"))
            error_text = (
                _as_str(message.get("error"))
                or _as_str(message.get("errorText"))
                or _as_str(message.get("stderr"))
            )
            events.append(
                SessionTranscriptEvent(
                    harness="pi",
                    source_session_id=source_session_id,
                    created_ms=created_ms,
                    role=role,
                    kind=kind,
                    name=name,
                    success=not is_error if isinstance(is_error, bool) else None,
                    error_text=error_text,
                    raw_kind="toolResult",
                )
            )
            continue

        if role == "assistant":
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = _as_str(part.get("type"))
                if part_type not in ("toolCall", "tool-call"):
                    continue
                tool_name = _as_str(part.get("name"))
                tool_call_id = _as_str(part.get("id"))
                name = tool_name or tool_call_id
                events.append(
                    SessionTranscriptEvent(
                        harness="pi",
                        source_session_id=source_session_id,
                        created_ms=created_ms,
                        role=role,
                        kind="tool_call",
                        name=name,
                        raw_kind=part_type,
                    )
                )

    return events


def _event_kind(
    raw_kind: str | None,
    mapping: Mapping[str, object],
    *,
    text: str | None,
    error_text: str | None,
) -> str | None:
    raw = (raw_kind or "").lower()
    if error_text or any(key in mapping for key in ("error", "errorText", "stderr")):
        return "error"
    if "tool" in raw or "function" in raw:
        return "tool_result" if "result" in raw or "output" in raw else "tool_call"
    if "exec" in raw or "command" in raw or "shell" in raw:
        return "command"
    if text and _looks_like_command(text):
        return "command"
    if _as_str(mapping.get("tool")) or _as_str(mapping.get("tool_name")):
        return "tool_call"
    return None


def _success_from_mapping(mapping: Mapping[str, object]) -> bool | None:
    for key in ("success", "ok", "is_success"):
        value = mapping.get(key)
        if isinstance(value, bool):
            return value
    status = _as_str(mapping.get("status")) or _as_str(mapping.get("outcome"))
    if status is not None:
        lowered = status.lower()
        if lowered in {"success", "succeeded", "ok", "completed"}:
            return True
        if lowered in {"failed", "error", "timeout", "timed_out", "cancelled"}:
            return False
    exit_code = mapping.get("exit_code", mapping.get("exitCode"))
    if isinstance(exit_code, int):
        return exit_code == 0
    return None


def _jsonl_paths(source_path: Path) -> list[Path]:
    resolved = source_path.expanduser()
    if not resolved.exists():
        return []
    if resolved.is_file():
        return [resolved]
    return sorted(path for path in resolved.rglob("*.jsonl") if path.is_file())


def _iter_jsonl(path: Path) -> Iterable[Mapping[str, object]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _event_file_candidates(event: SessionTranscriptEvent) -> tuple[str, ...]:
    values: list[str] = []
    if event.path:
        values.append(event.path)
    for source in (event.text, event.name):
        if not source:
            continue
        values.extend(match.group(1) for match in _PATH_RE.finditer(source))
    return tuple(values)


def _top_values(values: Iterable[str], *, limit: int = 8) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = value.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= limit:
            break
    return tuple(result)


def _redact_optional(value: str | None) -> str | None:
    return _redact(value) if value is not None else None


def _redact(value: str) -> str:
    value = str(Path(value).expanduser()) if value.startswith("~") else value
    home = str(Path.home())
    if home and value.startswith(home):
        value = "~" + value[len(home) :]
    value = _EMAIL_RE.sub("<email>", value)
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", value)


def _looks_like_command(value: str) -> bool:
    stripped = value.strip()
    if not stripped or "\n" in stripped:
        return False
    return stripped.split(" ", 1)[0] in {
        "python",
        "pytest",
        "ruff",
        "mypy",
        "git",
        "uv",
        "npm",
        "pnpm",
        "cargo",
        "go",
        "make",
        "taskledger",
        "toktrail",
    }


def _timestamp_ms(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    from datetime import datetime, timezone

    text = value.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _first_str(mapping: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = _as_str(mapping.get(key))
        if value is not None:
            return value
    return None


def _as_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, dict) else None


def _as_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _default_tool_name(raw_kind: str | None) -> str | None:
    if raw_kind is None:
        return None
    raw = raw_kind.lower()
    if "exec" in raw or "command" in raw or "shell" in raw:
        return "shell"
    if "tool" in raw or "function" in raw:
        return raw_kind
    return None


def public_digest_from_internal(digest: SessionDigest) -> object:
    from toktrail.api.models import (
        CostTotals as PublicCostTotals,
    )
    from toktrail.api.models import (
        SessionDigest as PublicSessionDigest,
    )
    from toktrail.api.models import (
        SessionDigestSummary as PublicSessionDigestSummary,
    )
    from toktrail.api.models import (
        SessionHealth as PublicSessionHealth,
    )
    from toktrail.api.models import (
        SessionHealthPenalty as PublicSessionHealthPenalty,
    )
    from toktrail.api.models import (
        SessionToolHealth as PublicSessionToolHealth,
    )
    from toktrail.api.models import (
        SessionTotals as PublicSessionTotals,
    )
    from toktrail.api.models import (
        TokenBreakdown as PublicTokenBreakdown,
    )

    return PublicSessionDigest(
        schema_version=digest.schema_version,
        origin_machine_id=digest.origin_machine_id,
        machine_label=digest.machine_label,
        harness=digest.harness,
        source_session_id=digest.source_session_id,
        area_path=digest.area_path,
        cwd=digest.cwd,
        source_dir=digest.source_dir,
        git_root=digest.git_root,
        git_remote=digest.git_remote,
        session_title=digest.session_title,
        started_ms=digest.started_ms,
        last_seen_ms=digest.last_seen_ms,
        usage=PublicSessionTotals(
            tokens=PublicTokenBreakdown(
                input=digest.usage.tokens.input,
                output=digest.usage.tokens.output,
                reasoning=digest.usage.tokens.reasoning,
                cache_read=digest.usage.tokens.cache_read,
                cache_write=digest.usage.tokens.cache_write,
                cache_output=digest.usage.tokens.cache_output,
            ),
            costs=PublicCostTotals(
                source_cost_usd=digest.usage.costs.source_cost_usd,
                actual_cost_usd=digest.usage.costs.actual_cost_usd,
                virtual_cost_usd=digest.usage.costs.virtual_cost_usd,
                unpriced_count=digest.usage.costs.unpriced_count,
            ),
            message_count=digest.message_count,
        ),
        message_count=digest.message_count,
        summary=PublicSessionDigestSummary(
            one_line=digest.summary.one_line,
            bullets=digest.summary.bullets,
            confidence=digest.summary.confidence,
            generator=digest.summary.generator,
        ),
        tool_health=PublicSessionToolHealth(
            tool_call_count=digest.tool_health.tool_call_count,
            tool_failure_count=digest.tool_health.tool_failure_count,
            tool_timeout_count=digest.tool_health.tool_timeout_count,
            tools=dict(digest.tool_health.tools),
            failed_tools=dict(digest.tool_health.failed_tools),
            warnings=digest.tool_health.warnings,
        ),
        health=(
            None
            if digest.health is None
            else PublicSessionHealth(
                score=digest.health.score,
                grade=digest.health.grade,
                outcome=digest.health.outcome,
                outcome_confidence=digest.health.outcome_confidence,
                basis=digest.health.basis,
                penalties=tuple(
                    PublicSessionHealthPenalty(
                        kind=penalty.kind,
                        points=penalty.points,
                        detail=penalty.detail,
                    )
                    for penalty in digest.health.penalties
                ),
                retry_count=digest.health.retry_count,
                edit_churn_count=digest.health.edit_churn_count,
                consecutive_failure_max=digest.health.consecutive_failure_max,
                context_pressure_max=digest.health.context_pressure_max,
                compaction_count=digest.health.compaction_count,
                mid_task_compaction_count=digest.health.mid_task_compaction_count,
            )
        ),
        files_mentioned=digest.files_mentioned,
        commands_mentioned=digest.commands_mentioned,
        models=digest.models,
        providers=digest.providers,
        contains_raw_transcript=digest.contains_raw_transcript,
        contains_snippets=digest.contains_snippets,
        generated_at_ms=digest.generated_at_ms,
        source_fingerprint=digest.source_fingerprint,
    )
