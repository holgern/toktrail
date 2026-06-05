"""Tests for Claude session event extraction and tool usage tracking."""

from __future__ import annotations

import json
from pathlib import Path

from toktrail.reporting import SessionTranscriptEvent
from toktrail.session_digests import (
    extract_claude_session_events,
    summarize_tool_health,
)


def _write_jsonl(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")


def test_extract_claude_session_events_basic(tmp_path: Path) -> None:
    """Claude JSONL with tool_use and tool_result produces correct events."""
    session_id = "ses-claude-001"
    source = tmp_path / f"{session_id}.jsonl"
    _write_jsonl(
        source,
        [
            {
                "sessionId": session_id,
                "type": "assistant",
                "timestamp": 1000,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-001",
                            "name": "Bash",
                            "input": {"command": "pytest -q"},
                        },
                        {
                            "type": "tool_use",
                            "id": "tu-002",
                            "name": "Read",
                            "input": {"file_path": "/tmp/test.py"},
                        },
                        {
                            "type": "tool_use",
                            "id": "tu-003",
                            "name": "Edit",
                            "input": {"file_path": "/tmp/test.py"},
                        },
                        {
                            "type": "tool_use",
                            "id": "tu-004",
                            "name": "TodoWrite",
                            "input": {"todos": []},
                        },
                    ],
                },
            },
            {
                "sessionId": session_id,
                "type": "user",
                "timestamp": 1100,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu-001",
                            "content": "ok",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu-002",
                            "is_error": True,
                            "error": "File not found",
                        },
                    ],
                },
            },
        ],
    )

    events = extract_claude_session_events(source, source_session_id=session_id)
    assert len(events) == 6

    # tool_call events
    tool_calls = [e for e in events if e.kind == "tool_call"]
    assert len(tool_calls) == 4
    assert tool_calls[0].name == "Bash"
    assert tool_calls[0].text == "pytest -q"
    assert tool_calls[1].name == "Read"
    assert tool_calls[2].name == "Edit"
    assert tool_calls[3].name == "TodoWrite"

    # tool_result events
    tool_results = [e for e in events if e.kind in ("tool_result", "error")]
    assert len(tool_results) == 2
    assert tool_results[0].kind == "tool_result"
    assert tool_results[0].success is None  # not explicitly success/fail
    assert tool_results[1].kind == "error"
    assert tool_results[1].success is False
    assert tool_results[1].error_text == "File not found"
    # name resolved via tool_use_id
    assert tool_results[1].name == "Read"


def test_extract_claude_session_events_filters_by_session(tmp_path: Path) -> None:
    """Only events matching source_session_id are returned."""
    source = tmp_path / "session.jsonl"
    _write_jsonl(
        source,
        [
            {
                "sessionId": "ses-001",
                "type": "assistant",
                "timestamp": 1000,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-1",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        },
                    ],
                },
            },
            {
                "sessionId": "ses-002",
                "type": "assistant",
                "timestamp": 2000,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-2",
                            "name": "Read",
                            "input": {"path": "/tmp"},
                        },
                    ],
                },
            },
        ],
    )

    events = extract_claude_session_events(source, source_session_id="ses-001")
    assert len(events) == 1
    assert events[0].name == "Bash"


def test_summarize_tool_health_normalizes_claude_names() -> None:
    """Claude tool names are normalized correctly."""
    events = [
        SessionTranscriptEvent(
            harness="claude",
            source_session_id="ses-1",
            created_ms=1000,
            role="assistant",
            kind="tool_call",
            name="Bash",
            raw_kind="tool_use",
        ),
        SessionTranscriptEvent(
            harness="claude",
            source_session_id="ses-1",
            created_ms=1100,
            role="assistant",
            kind="tool_call",
            name="Read",
            raw_kind="tool_use",
        ),
        SessionTranscriptEvent(
            harness="claude",
            source_session_id="ses-1",
            created_ms=1200,
            role="assistant",
            kind="tool_call",
            name="Edit",
            raw_kind="tool_use",
        ),
        SessionTranscriptEvent(
            harness="claude",
            source_session_id="ses-1",
            created_ms=1300,
            role="assistant",
            kind="tool_call",
            name="TodoWrite",
            raw_kind="tool_use",
        ),
        SessionTranscriptEvent(
            harness="claude",
            source_session_id="ses-1",
            created_ms=1400,
            role="user",
            kind="tool_result",
            name="Read",
            success=True,
            raw_kind="tool_result",
        ),
        SessionTranscriptEvent(
            harness="claude",
            source_session_id="ses-1",
            created_ms=1500,
            role="user",
            kind="error",
            name="Read",
            success=False,
            error_text="File not found",
            raw_kind="tool_result",
        ),
    ]

    health = summarize_tool_health(events)
    assert health.tools == {"bash": 1, "read": 1, "edit": 1, "todowrite": 1}
    assert health.tool_call_count == 6  # all tool_call, tool_result, error
    assert health.tool_failure_count == 1
    assert health.failed_tools == {"read": 1}
