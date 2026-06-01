from __future__ import annotations

import re
from time import time

from toktrail.reporting import (
    SessionHealth,
    SessionHealthPenalty,
    SessionToolHealth,
    SessionTranscriptEvent,
    UsageSessionRow,
)

_COMPACTION_RE = re.compile(r"\b(compact|compaction|compress(?:ion)?)\b", re.IGNORECASE)
_CONTEXT_RATIO_RE = re.compile(
    r"context(?:\s+window)?[^0-9]{0,24}(?P<value>0(?:\.\d+)?|1(?:\.0+)?)",
    re.IGNORECASE,
)
_CONTEXT_PERCENT_RE = re.compile(
    r"context(?:\s+window)?[^0-9]{0,24}(?P<value>\d{1,3})%",
    re.IGNORECASE,
)
_EDIT_RE = re.compile(r"\b(edit|write|patch|apply_patch|replace)\b", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:^|\s)([A-Za-z0-9_./-]+\.[A-Za-z0-9_/-]+)")
_QUIET_THRESHOLD_MS = 6 * 60 * 60 * 1000


def build_session_health(
    events: tuple[SessionTranscriptEvent, ...],
    *,
    usage_session: UsageSessionRow,
    tool_health: SessionToolHealth,
    generated_at_ms: int | None = None,
) -> SessionHealth:
    outcome, confidence, outcome_basis = _classify_outcome_with_basis(
        events,
        usage_session=usage_session,
        generated_at_ms=generated_at_ms,
    )
    retry_count = count_retries(events)
    edit_churn_count = count_edit_churn(events)
    consecutive_failure_max = max_consecutive_failures(events)
    compaction_count, mid_task_compaction_count = count_compactions(events)
    context_pressure_max = max_context_pressure(events)

    penalties: list[SessionHealthPenalty] = []
    basis = list(outcome_basis)

    _add_penalty(
        penalties,
        kind="outcome",
        points={"errored": 30, "abandoned": 15}.get(outcome, 0),
        detail=outcome,
        basis=basis,
    )
    _add_penalty(
        penalties,
        kind="tool_failures",
        points=min(tool_health.tool_failure_count * 3, 30),
        detail=f"count={tool_health.tool_failure_count}",
        basis=basis,
    )
    _add_penalty(
        penalties,
        kind="retries",
        points=min(retry_count * 5, 25),
        detail=f"count={retry_count}",
        basis=basis,
    )
    _add_penalty(
        penalties,
        kind="edit_churn",
        points=min(edit_churn_count * 4, 20),
        detail=f"count={edit_churn_count}",
        basis=basis,
    )
    if consecutive_failure_max >= 3:
        _add_penalty(
            penalties,
            kind="failure_streak",
            points=10,
            detail=f"max={consecutive_failure_max}",
            basis=basis,
        )
    if compaction_count > 1:
        _add_penalty(
            penalties,
            kind="compactions",
            points=min((compaction_count - 1) * 5, 15),
            detail=f"count={compaction_count}",
            basis=basis,
        )
    if mid_task_compaction_count > 0:
        _add_penalty(
            penalties,
            kind="mid_task_compactions",
            points=min(mid_task_compaction_count * 8, 18),
            detail=f"count={mid_task_compaction_count}",
            basis=basis,
        )
    if context_pressure_max is not None and context_pressure_max > 0.90:
        _add_penalty(
            penalties,
            kind="context_pressure",
            points=10,
            detail=f"max={context_pressure_max:.2f}",
            basis=basis,
        )

    has_signal = (
        outcome != "unknown"
        or tool_health.tool_failure_count > 0
        or retry_count > 0
        or edit_churn_count > 0
        or consecutive_failure_max > 0
        or compaction_count > 0
        or mid_task_compaction_count > 0
        or context_pressure_max is not None
    )
    score: int | None = None
    grade: str | None = None
    if has_signal:
        score = max(0, 100 - sum(penalty.points for penalty in penalties))
        grade = _grade_for_score(score)

    return SessionHealth(
        score=score,
        grade=grade,
        outcome=outcome,
        outcome_confidence=confidence,
        basis=tuple(basis),
        penalties=tuple(penalties),
        retry_count=retry_count,
        edit_churn_count=edit_churn_count,
        consecutive_failure_max=consecutive_failure_max,
        context_pressure_max=context_pressure_max,
        compaction_count=compaction_count,
        mid_task_compaction_count=mid_task_compaction_count,
    )


def classify_outcome(
    events: tuple[SessionTranscriptEvent, ...],
    usage_session: UsageSessionRow,
) -> tuple[str, str]:
    outcome, confidence, _ = _classify_outcome_with_basis(
        events,
        usage_session=usage_session,
        generated_at_ms=None,
    )
    return outcome, confidence


def count_retries(events: tuple[SessionTranscriptEvent, ...]) -> int:
    retries = 0
    current_signature: tuple[str, str, str, str] | None = None
    current_length = 0
    for event in events:
        signature = _retry_signature(event)
        if signature is None:
            current_signature = None
            current_length = 0
            continue
        if signature == current_signature:
            current_length += 1
        else:
            current_signature = signature
            current_length = 1
        if current_length >= 3:
            retries += 1
    return retries


def count_edit_churn(events: tuple[SessionTranscriptEvent, ...]) -> int:
    churn = 0
    for index, event in enumerate(events):
        target = _edit_target(event)
        if target is None:
            continue
        window = events[index : index + 5]
        seen = sum(1 for candidate in window if _edit_target(candidate) == target)
        if seen >= 3:
            churn += 1
    return churn


def max_consecutive_failures(events: tuple[SessionTranscriptEvent, ...]) -> int:
    streak = 0
    max_streak = 0
    for event in events:
        if _event_failed(event):
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def count_compactions(events: tuple[SessionTranscriptEvent, ...]) -> tuple[int, int]:
    compaction_indexes = [
        index
        for index, event in enumerate(events)
        if _COMPACTION_RE.search(_event_text(event))
    ]
    if not compaction_indexes:
        return 0, 0
    mid_task = sum(1 for index in compaction_indexes if index < len(events) - 1)
    return len(compaction_indexes), mid_task


def max_context_pressure(events: tuple[SessionTranscriptEvent, ...]) -> float | None:
    best: float | None = None
    for event in events:
        text = _event_text(event)
        for match in _CONTEXT_RATIO_RE.finditer(text):
            value = float(match.group("value"))
            best = value if best is None else max(best, value)
        for match in _CONTEXT_PERCENT_RE.finditer(text):
            value = min(int(match.group("value")), 100) / 100
            best = value if best is None else max(best, value)
    return best


def _classify_outcome_with_basis(
    events: tuple[SessionTranscriptEvent, ...],
    *,
    usage_session: UsageSessionRow,
    generated_at_ms: int | None,
) -> tuple[str, str, tuple[str, ...]]:
    basis: list[str] = []
    if not events and usage_session.message_count <= 1:
        basis.append("no-rich-events")
        return "unknown", "low", tuple(basis)

    failure_streak = max_consecutive_failures(events)
    if failure_streak >= 3:
        basis.append(f"failure-streak={failure_streak}")
        return "errored", "high", tuple(basis)

    if events:
        last_event = events[-1]
        if last_event.kind == "error":
            basis.append("last-event-error")
            return "errored", "high", tuple(basis)

        evaluation_ms = generated_at_ms or int(time() * 1000)
        last_event_ms = (
            last_event.created_ms or usage_session.last_ms or usage_session.first_ms
        )
        if (
            last_event.role == "user"
            and last_event_ms is not None
            and evaluation_ms - last_event_ms >= _QUIET_THRESHOLD_MS
        ):
            basis.append("stale-last-user-event")
            return "abandoned", "medium", tuple(basis)

        if last_event.role == "assistant" or (
            last_event.success is True and last_event.kind in {"tool_result", "command"}
        ):
            basis.append("successful-last-event")
            return "completed", "medium", tuple(basis)

    return "unknown", "low", tuple(basis)


def _add_penalty(
    penalties: list[SessionHealthPenalty],
    *,
    kind: str,
    points: int,
    detail: str,
    basis: list[str],
) -> None:
    if points <= 0:
        return
    penalties.append(SessionHealthPenalty(kind=kind, points=points, detail=detail))
    basis.append(f"{kind}={detail}")


def _grade_for_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _retry_signature(event: SessionTranscriptEvent) -> tuple[str, str, str, str] | None:
    if event.kind not in {"tool_call", "tool_result", "command", "error"}:
        return None
    return (
        event.kind,
        event.name or "",
        event.path or "",
        _event_text(event),
    )


def _edit_target(event: SessionTranscriptEvent) -> str | None:
    if not _EDIT_RE.search(_event_text(event)):
        return None
    if event.path:
        return event.path
    match = _PATH_RE.search(_event_text(event))
    if match is None:
        return None
    return match.group(1)


def _event_failed(event: SessionTranscriptEvent) -> bool:
    return event.success is False or event.kind == "error" or bool(event.error_text)


def _event_text(event: SessionTranscriptEvent) -> str:
    return " ".join(
        part.strip()
        for part in (event.raw_kind, event.name, event.text, event.error_text)
        if part and part.strip()
    )
