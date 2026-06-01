from __future__ import annotations

from pathlib import Path

from toktrail.session_digests import extract_pi_session_events, summarize_tool_health


def test_pi_session_digest_extracts_failed_tool_result(tmp_path: Path) -> None:
    source = tmp_path / "sessions" / "session.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        '{"type":"session","id":"pi_ses_1","cwd":"/tmp/project"}\n'
        '{"type":"message","timestamp":"2026-01-01T00:00:01Z",'
        '"message":{"role":"user",'
        '"content":[{"type":"text","text":"hello"}]}}\n'
        '{"type":"message","timestamp":"2026-01-01T00:00:02Z",'
        '"message":{"role":"assistant","model":"m","provider":"p",'
        '"usage":{"input":1,"output":1},'
        '"content":['
        '{"type":"toolCall","name":"bash","id":"call_00","arguments":{"command":"ls"}},'
        '{"type":"toolCall","name":"read","id":"call_01","arguments":{"filePath":"x.py"}}'
        "]}}\n"
        '{"type":"message","timestamp":"2026-01-01T00:00:03Z",'
        '"message":{"role":"toolResult","toolName":"bash",'
        '"toolCallId":"call_00","isError":false,'
        '"content":[{"type":"text","text":"file1.txt"}]}}\n'
        '{"type":"message","timestamp":"2026-01-01T00:00:04Z",'
        '"message":{"role":"toolResult","toolName":"read",'
        '"toolCallId":"call_01","isError":true,'
        '"error":"file not found",'
        '"content":[{"type":"text","text":""}]}}\n',
        encoding="utf-8",
    )

    events = extract_pi_session_events(
        tmp_path / "sessions",
        source_session_id="pi_ses_1",
    )
    health = summarize_tool_health(events)

    assert len(events) == 4
    # Two tool_call events from the assistant message
    assert events[0].kind == "tool_call"
    assert events[0].name == "bash"
    assert events[1].kind == "tool_call"
    assert events[1].name == "read"
    # One successful tool_result
    assert events[2].kind == "tool_result"
    assert events[2].name == "bash"
    assert events[2].success is True
    # One failed tool_result (isError=true) → kind="error"
    assert events[3].kind == "error"
    assert events[3].name == "read"
    assert events[3].success is False
    assert events[3].error_text == "file not found"
    # Health summary
    assert health.tool_call_count == 4
    assert health.tool_failure_count == 1
    assert health.failed_tools == {"read": 1}
