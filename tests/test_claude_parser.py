from __future__ import annotations

import json
from pathlib import Path

from toktrail.adapters.claude import (
    list_claude_sessions,
    parse_claude_file,
    scan_claude_file,
    scan_claude_path,
)
from toktrail.models import TokenBreakdown


def write_jsonl(path: Path, *line_groups: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(line) for group in line_groups for line in group) + "\n",
        encoding="utf-8",
    )
    return path


def write_json(path: Path, data: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_assistant(
    *,
    model: str = "claude-3-5-sonnet",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 20,
    cache_write: int = 10,
    message_id: str = "msg-1",
    request_id: str = "req-1",
    timestamp: str = "2026-01-01T00:00:00Z",
) -> dict[str, object]:
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "requestId": request_id,
        "message": {
            "id": message_id,
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        },
    }


def test_parse_claude_assistant_usage(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [{"type": "user", "message": "hello"}],
        [make_assistant()],
    )

    events = parse_claude_file(path)

    assert len(events) == 1
    event = events[0]
    assert event.harness == "claude"
    assert event.provider_id == "anthropic"
    assert event.model_id == "claude-3-5-sonnet"
    assert event.source_session_id == "session-001"
    assert event.tokens == TokenBreakdown(
        input=100, output=50, reasoning=0, cache_read=20, cache_write=10
    )


def test_parse_claude_deduplicates_streaming_with_per_field_max(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [make_assistant(output_tokens=50)],
        [make_assistant(output_tokens=80)],
    )

    events = parse_claude_file(path)

    assert len(events) == 1
    assert events[0].tokens.output == 80
    assert events[0].tokens.input == 100


def test_parse_claude_dedup_per_field_max(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [
            make_assistant(
                input_tokens=50, output_tokens=30, cache_read=10, cache_write=5
            )
        ],
        [
            make_assistant(
                input_tokens=100, output_tokens=20, cache_read=30, cache_write=15
            )
        ],
    )

    events = parse_claude_file(path)

    assert len(events) == 1
    assert events[0].tokens.input == 100
    assert events[0].tokens.output == 30
    assert events[0].tokens.cache_read == 30
    assert events[0].tokens.cache_write == 15


def test_parse_claude_dedup_skips_model_none_without_stale_index(
    tmp_path: Path,
) -> None:
    """First row has usage/id/requestId but no model.
    Second row is a true assistant with model.
    Should produce one event, not crash."""
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "requestId": "req-1",
                "message": {
                    "id": "msg-1",
                    "model": "",
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 10,
                    },
                },
            },
        ],
        [make_assistant()],
    )

    events = parse_claude_file(path)

    assert len(events) == 1
    assert events[0].model_id == "claude-3-5-sonnet"


def test_parse_claude_allows_same_message_different_request(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [make_assistant(message_id="msg-1", request_id="req-1")],
        [make_assistant(message_id="msg-1", request_id="req-2")],
    )

    events = parse_claude_file(path)

    assert len(events) == 2


def test_parse_claude_entries_without_dedup_fields_still_processed(
    tmp_path: Path,
) -> None:
    entry = {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {
            "model": "claude-3-5-sonnet",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
            },
        },
    }
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [dict(entry), dict(entry)],
    )

    events = parse_claude_file(path)

    assert len(events) == 2


def test_parse_claude_ignores_user_and_tool_rows(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [{"type": "user", "message": {"content": "hello"}}],
        [{"type": "tool_result", "content": "result"}],
        [make_assistant()],
    )

    events = parse_claude_file(path)

    assert len(events) == 1


def test_parse_claude_headless_json_output(tmp_path: Path) -> None:
    path = write_json(
        tmp_path / "session-001.json",
        {
            "message": {
                "model": "claude-3-5-sonnet",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "cache_read_input_tokens": 50,
                    "cache_creation_input_tokens": 25,
                },
            },
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )

    events = parse_claude_file(path)

    assert len(events) == 1
    assert events[0].model_id == "claude-3-5-sonnet"
    assert events[0].tokens.input == 200
    assert events[0].tokens.output == 100
    assert events[0].tokens.cache_read == 50
    assert events[0].tokens.cache_write == 25


def test_parse_claude_headless_stream_output(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [
            {
                "type": "message_start",
                "message": {
                    "model": "claude-3-5-sonnet",
                    "usage": {"input_tokens": 100, "output_tokens": 0},
                },
            },
            {
                "type": "message_delta",
                "delta": {
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
            {
                "type": "message_delta",
                "delta": {
                    "usage": {"output_tokens": 80},
                },
            },
            {"type": "message_stop"},
        ],
    )

    events = parse_claude_file(path)

    assert len(events) == 1
    assert events[0].tokens.input == 100
    assert events[0].tokens.output == 80


def test_sidechain_nested_with_meta_sidecar(tmp_path: Path) -> None:
    workspace = tmp_path / "projects" / "ws-1" / "parent-session"
    sidechain_path = write_jsonl(
        workspace / "subagents" / "agent-abc123.jsonl",
        [
            {
                "isSidechain": True,
                "sessionId": "parent-session",
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "id": "msg-1",
                    "model": "claude-3-5-sonnet",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ],
    )
    meta_path = workspace / "subagents" / "agent-abc123.meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({"agentType": "explore"}), encoding="utf-8")

    events = parse_claude_file(sidechain_path)

    assert len(events) == 1
    assert events[0].source_session_id == "parent-session"
    assert events[0].agent == "Explore"


def test_sidechain_nested_without_meta_falls_back(tmp_path: Path) -> None:
    workspace = tmp_path / "projects" / "ws-1" / "parent-session"
    sidechain_path = write_jsonl(
        workspace / "subagents" / "agent-xyz.jsonl",
        [
            {
                "isSidechain": True,
                "sessionId": "parent-session",
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "id": "msg-1",
                    "model": "claude-3-5-sonnet",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ],
    )

    events = parse_claude_file(sidechain_path)

    assert len(events) == 1
    assert events[0].agent == "Claude Code Subagent"


def test_sidechain_flat_legacy_layout(tmp_path: Path) -> None:
    workspace = tmp_path / "projects" / "ws-1"
    sidechain_path = write_jsonl(
        workspace / "agent-abc123.jsonl",
        [
            {
                "isSidechain": True,
                "sessionId": "parent-123",
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "id": "msg-1",
                    "model": "claude-3-5-sonnet",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ],
    )

    events = parse_claude_file(sidechain_path)

    assert len(events) == 1
    assert events[0].source_session_id == "parent-123"


def test_sidechain_tier2_recovers_agent_from_parent_tool_use(tmp_path: Path) -> None:
    workspace = tmp_path / "projects" / "ws-1"
    _parent_path = write_jsonl(
        workspace / "parent-123.jsonl",
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Agent",
                            "id": "tool-001",
                            "input": {"subagent_type": "document-specialist"},
                        }
                    ],
                    "model": "claude-3-5-sonnet",
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-001",
                            "content": "agentId: abc123",
                        }
                    ]
                },
            },
        ],
    )
    _sidechain_path = write_jsonl(
        workspace / "agent-abc123.jsonl",
        [
            {
                "isSidechain": True,
                "sessionId": "parent-123",
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "id": "msg-1",
                    "model": "claude-3-5-sonnet",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ],
    )

    # Scan the directory so parent cache is populated
    scan = scan_claude_path(workspace)
    sidechain_events = [
        e
        for e in scan.events
        if e.source_session_id == "parent-123" and e.agent is not None
    ]

    assert len(sidechain_events) >= 1
    assert sidechain_events[0].agent == "Document Specialist"


def test_sidechain_meta_takes_precedence_over_parent(tmp_path: Path) -> None:
    workspace = tmp_path / "projects" / "ws-1"
    _parent_path = write_jsonl(
        workspace / "parent-123.jsonl",
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Agent",
                            "id": "tool-001",
                            "input": {"subagent_type": "wrong-type"},
                        }
                    ],
                    "model": "claude-3-5-sonnet",
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-001",
                            "content": "agentId: abc123",
                        }
                    ]
                },
            },
        ],
    )
    sidechain_path = write_jsonl(
        workspace / "agent-abc123.jsonl",
        [
            {
                "isSidechain": True,
                "sessionId": "parent-123",
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {
                    "id": "msg-1",
                    "model": "claude-3-5-sonnet",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            },
        ],
    )
    meta_path = workspace / "agent-abc123.meta.json"
    meta_path.write_text(json.dumps({"agentType": "code-reviewer"}), encoding="utf-8")

    events = parse_claude_file(sidechain_path)

    assert len(events) == 1
    assert events[0].agent == "Code Reviewer"


def test_scan_claude_path_reads_nested_jsonl_and_filters(tmp_path: Path) -> None:
    workspace = tmp_path / "projects" / "ws-1"
    write_jsonl(
        workspace / "session-001.jsonl",
        [make_assistant(message_id="msg-1", request_id="req-1")],
    )
    write_jsonl(
        workspace / "session-002.jsonl",
        [
            make_assistant(
                message_id="msg-2",
                request_id="req-2",
                input_tokens=200,
            )
        ],
    )

    scan = scan_claude_path(workspace, source_session_id="session-001")

    assert scan.files_seen == 2
    assert len(scan.events) == 1
    assert scan.events[0].source_session_id == "session-001"


def test_scan_claude_raw_json_is_optional(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "session-001.jsonl",
        [make_assistant()],
    )

    scan_with_raw = scan_claude_file(path, include_raw_json=True)
    scan_no_raw = scan_claude_file(path, include_raw_json=False)

    assert scan_with_raw.events[0].raw_json is not None
    assert scan_no_raw.events[0].raw_json is None


def test_list_claude_sessions_aggregates_messages(tmp_path: Path) -> None:
    workspace = tmp_path / "projects" / "ws-1"
    write_jsonl(
        workspace / "session-001.jsonl",
        [
            make_assistant(
                message_id="msg-1",
                request_id="req-1",
                input_tokens=100,
            ),
            make_assistant(
                message_id="msg-2",
                request_id="req-2",
                input_tokens=200,
            ),
        ],
    )

    sessions = list_claude_sessions(workspace)

    assert len(sessions) >= 1
    session = [s for s in sessions if s.source_session_id == "session-001"][0]
    assert session.assistant_message_count == 2
    assert session.tokens.input == 300
    assert any("session-001.jsonl" in p for p in session.source_paths)
