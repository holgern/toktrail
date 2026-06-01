from __future__ import annotations

from decimal import Decimal

from toktrail.models import TokenBreakdown
from toktrail.reporting import (
    CostTotals,
    SessionToolHealth,
    SessionTranscriptEvent,
    UsageSessionRow,
)
from toktrail.session_health import (
    build_session_health,
    classify_outcome,
    count_edit_churn,
    count_retries,
    max_consecutive_failures,
)


def _usage_session(*, message_count: int = 3, last_ms: int = 3_000) -> UsageSessionRow:
    return UsageSessionRow(
        key="machine/codex/ses-1",
        origin_machine_id="machine-1",
        machine_name="workstation",
        machine_label="workstation",
        harness="codex",
        source_session_id="ses-1",
        area_id=None,
        area_sync_id=None,
        area_path="private/toktrail",
        area_name=None,
        first_ms=1_000,
        last_ms=last_ms,
        message_count=message_count,
        tokens=TokenBreakdown(input=10, output=5),
        costs=CostTotals(actual_cost_usd=Decimal("0.01")),
        cwd="/home/test/src/toktrail",
        source_dir="/home/test/src/toktrail",
        git_root="/home/test/src/toktrail",
        git_remote="git@example.com:org/toktrail.git",
        session_title="Health test",
    )


def _event(
    *,
    created_ms: int,
    role: str | None = None,
    kind: str = "command",
    name: str | None = "shell",
    text: str | None = None,
    path: str | None = None,
    success: bool | None = None,
    error_text: str | None = None,
    raw_kind: str | None = None,
) -> SessionTranscriptEvent:
    return SessionTranscriptEvent(
        harness="codex",
        source_session_id="ses-1",
        created_ms=created_ms,
        role=role,
        kind=kind,
        name=name,
        text=text,
        path=path,
        success=success,
        error_text=error_text,
        raw_kind=raw_kind,
    )


def test_build_session_health_keeps_unknown_sessions_unscored() -> None:
    health = build_session_health(
        (),
        usage_session=_usage_session(message_count=1),
        tool_health=SessionToolHealth(),
        generated_at_ms=5_000,
    )

    assert health.score is None
    assert health.grade is None
    assert health.outcome == "unknown"
    assert health.penalties == ()


def test_build_session_health_penalizes_tool_failures_and_caps_score() -> None:
    health = build_session_health(
        (
            _event(
                created_ms=1_000,
                kind="command",
                text="pytest tests/test_session_health.py",
                success=False,
                error_text="failed",
            ),
        ),
        usage_session=_usage_session(),
        tool_health=SessionToolHealth(tool_call_count=10, tool_failure_count=20),
        generated_at_ms=4_000,
    )

    assert health.outcome == "unknown"
    assert health.score == 70
    assert health.grade == "C"
    assert any(
        penalty.kind == "tool_failures" and penalty.points == 30
        for penalty in health.penalties
    )


def test_retry_and_edit_churn_helpers_detect_repeated_work() -> None:
    events = (
        _event(
            created_ms=1_000,
            text="python tool.py",
            success=False,
            error_text="failed",
        ),
        _event(
            created_ms=1_100,
            text="python tool.py",
            success=False,
            error_text="failed",
        ),
        _event(
            created_ms=1_200,
            text="python tool.py",
            success=False,
            error_text="failed",
        ),
        _event(
            created_ms=1_300,
            text="apply_patch foo.py",
            path="foo.py",
            name="edit",
        ),
        _event(
            created_ms=1_400,
            text="apply_patch foo.py",
            path="foo.py",
            name="edit",
        ),
        _event(
            created_ms=1_500,
            text="apply_patch foo.py",
            path="foo.py",
            name="edit",
        ),
    )

    assert count_retries(events) == 2
    assert count_edit_churn(events) >= 1
    assert max_consecutive_failures(events) == 3


def test_classify_outcome_marks_abandoned_when_last_user_turn_goes_quiet() -> None:
    outcome, confidence = classify_outcome(
        (
            _event(created_ms=1_000, role="assistant", kind="tool_result", success=True),
            _event(created_ms=2_000, role="user", kind="command", text="try again"),
        ),
        _usage_session(last_ms=2_000),
    )

    assert outcome == "abandoned"
    assert confidence == "medium"


def test_classify_outcome_marks_completed_on_successful_last_event() -> None:
    outcome, confidence = classify_outcome(
        (
            _event(created_ms=1_000, role="user", kind="command", text="run tests"),
            _event(created_ms=2_000, role="assistant", kind="tool_result", success=True),
        ),
        _usage_session(last_ms=2_000),
    )

    assert outcome == "completed"
    assert confidence == "medium"
