from __future__ import annotations

from pathlib import Path

from toktrail.session_digests import extract_pi_session_events, summarize_tool_health


def test_pi_session_digest_extracts_failed_tool_result(tmp_path: Path) -> None:
    source = tmp_path / "sessions" / "session.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        '{"type":"session","id":"pi_ses_1","cwd":"/tmp/project"}\n'
        '{"type":"message","timestamp":"2026-01-01T00:00:01Z",'
        '"message":{"role":"assistant","model":"m","provider":"p",'
        '"usage":{"input":1,"output":1}}}\n'
        '{"type":"message","timestamp":"2026-01-01T00:00:02Z",'
        '"message":{"role":"tool","type":"tool_result","toolName":"shell",'
        '"status":"failed","error":"failed",'
        '"content":"pytest tests/test_pi_session_digest.py"}}\n',
        encoding="utf-8",
    )

    events = extract_pi_session_events(
        tmp_path / "sessions",
        source_session_id="pi_ses_1",
    )
    health = summarize_tool_health(events)

    assert len(events) == 1
    assert events[0].kind == "error"
    assert events[0].name == "shell"
    assert health.tool_call_count == 1
    assert health.tool_failure_count == 1
    assert health.failed_tools == {"shell": 1}
