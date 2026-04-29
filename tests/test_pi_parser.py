from __future__ import annotations

from pathlib import Path

from toktrail.adapters.pi import list_pi_sessions, parse_pi_file, scan_pi_path


def write_pi_session(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_parse_pi_jsonl_valid_assistant_message(tmp_path) -> None:
    session_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    write_pi_session(
        session_file,
        """{"type":"session","id":"pi_ses_001","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/tmp"}
{"type":"message","id":"msg_001","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"assistant","model":"claude-3-5-sonnet","provider":"anthropic","usage":{"input":100,"output":50,"cacheRead":10,"cacheWrite":5,"totalTokens":165}}}
""",
    )

    events = parse_pi_file(session_file)

    assert len(events) == 1
    event = events[0]
    assert event.harness == "pi"
    assert event.source_session_id == "pi_ses_001"
    assert event.source_message_id == "msg_001"
    assert event.provider_id == "anthropic"
    assert event.model_id == "claude-3-5-sonnet"
    assert event.tokens.input == 100
    assert event.tokens.output == 50
    assert event.tokens.cache_read == 10
    assert event.tokens.cache_write == 5
    assert event.tokens.reasoning == 0
    assert event.tokens.total == 165
    assert event.cost_usd == 0.0
    assert event.created_ms == 1_767_225_601_000


def test_parse_pi_skips_non_assistant_messages(tmp_path) -> None:
    session_file = tmp_path / "session.jsonl"
    write_pi_session(
        session_file,
        """{"type":"session","id":"pi_ses_002","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/tmp"}
{"type":"message","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","model":"claude-3-5-sonnet","provider":"anthropic","usage":{"input":100,"output":50,"cacheRead":0,"cacheWrite":0,"totalTokens":150}}}
""",
    )

    assert parse_pi_file(session_file) == []


def test_parse_pi_skips_missing_usage(tmp_path) -> None:
    session_file = tmp_path / "session.jsonl"
    write_pi_session(
        session_file,
        """{"type":"session","id":"pi_ses_003","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/tmp"}
{"type":"message","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"assistant","model":"claude-3-5-sonnet","provider":"anthropic"}}
""",
    )

    assert parse_pi_file(session_file) == []


def test_parse_pi_skips_malformed_json_lines(tmp_path) -> None:
    session_file = tmp_path / "session.jsonl"
    write_pi_session(
        session_file,
        """{"type":"session","id":"pi_ses_004","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/tmp"}
not valid json
{"type":"message","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"assistant","model":"gpt-4o-mini","provider":"openai","usage":{"input":10,"output":5,"cacheRead":0,"cacheWrite":0,"totalTokens":15}}}
""",
    )

    events = parse_pi_file(session_file)

    assert len(events) == 1
    assert events[0].model_id == "gpt-4o-mini"
    assert events[0].provider_id == "openai"


def test_parse_pi_clamps_negative_usage(tmp_path) -> None:
    session_file = tmp_path / "session.jsonl"
    write_pi_session(
        session_file,
        """{"type":"session","id":"pi_ses_005","cwd":"/tmp"}
{"type":"message","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"assistant","model":"m","provider":"p","usage":{"input":-10,"output":2,"cacheRead":-3,"cacheWrite":4}}}
""",
    )

    event = parse_pi_file(session_file)[0]

    assert event.tokens.input == 0
    assert event.tokens.output == 2
    assert event.tokens.cache_read == 0
    assert event.tokens.cache_write == 4


def test_parse_pi_returns_empty_for_missing_file(tmp_path) -> None:
    assert parse_pi_file(tmp_path / "missing.jsonl") == []


def test_scan_pi_path_reads_nested_jsonl_files(tmp_path) -> None:
    first = tmp_path / "encoded-a" / "a.jsonl"
    second = tmp_path / "encoded-b" / "b.jsonl"
    write_pi_session(
        first,
        """{"type":"session","id":"pi_a","cwd":"/a"}
{"type":"message","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"assistant","model":"m","provider":"p","usage":{"input":1,"output":2,"cacheRead":3,"cacheWrite":4}}}
""",
    )
    write_pi_session(
        second,
        """{"type":"session","id":"pi_b","cwd":"/b"}
{"type":"message","timestamp":"2026-01-01T00:00:02.000Z","message":{"role":"assistant","model":"m","provider":"p","usage":{"input":5,"output":6,"cacheRead":7,"cacheWrite":8}}}
""",
    )

    scan = scan_pi_path(tmp_path)

    assert scan.files_seen == 2
    assert len(scan.events) == 2
    assert {event.source_session_id for event in scan.events} == {"pi_a", "pi_b"}


def test_scan_pi_path_supports_source_session_filter(tmp_path) -> None:
    first = tmp_path / "encoded-a" / "a.jsonl"
    second = tmp_path / "encoded-b" / "b.jsonl"
    write_pi_session(
        first,
        """{"type":"session","id":"pi_a","cwd":"/a"}
{"type":"message","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"assistant","model":"m","provider":"p","usage":{"input":1,"output":2,"cacheRead":3,"cacheWrite":4}}}
""",
    )
    write_pi_session(
        second,
        """{"type":"session","id":"pi_b","cwd":"/b"}
{"type":"message","timestamp":"2026-01-01T00:00:02.000Z","message":{"role":"assistant","model":"m","provider":"p","usage":{"input":5,"output":6,"cacheRead":7,"cacheWrite":8}}}
""",
    )

    scan = scan_pi_path(tmp_path, source_session_id="pi_b")

    assert len(scan.events) == 1
    assert scan.events[0].source_session_id == "pi_b"
    assert scan.rows_skipped == 1


def test_parse_pi_falls_back_to_file_mtime_for_invalid_timestamp(tmp_path) -> None:
    session_file = tmp_path / "session.jsonl"
    write_pi_session(
        session_file,
        """{"type":"session","id":"pi_ses_006","cwd":"/tmp"}
{"type":"message","timestamp":"not-a-timestamp","message":{"role":"assistant","model":"m","provider":"p","usage":{"input":1,"output":2,"cacheRead":3,"cacheWrite":4}}}
""",
    )
    expected_created_ms = int(session_file.stat().st_mtime * 1000)

    event = parse_pi_file(session_file)[0]

    assert event.created_ms == expected_created_ms


def test_list_pi_sessions_aggregates_messages(tmp_path) -> None:
    session_file = tmp_path / "session.jsonl"
    write_pi_session(
        session_file,
        """{"type":"session","id":"pi_ses_007","cwd":"/tmp"}
{"type":"message","timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"assistant","model":"m","provider":"p","usage":{"input":1,"output":2,"cacheRead":3,"cacheWrite":4}}}
{"type":"message","timestamp":"2026-01-01T00:00:02.000Z","message":{"role":"assistant","model":"m","provider":"p","usage":{"input":5,"output":6,"cacheRead":7,"cacheWrite":8}}}
""",
    )

    summaries = list_pi_sessions(session_file)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.source_session_id == "pi_ses_007"
    assert summary.assistant_message_count == 2
    assert summary.tokens.total == 36
