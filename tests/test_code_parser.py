# ruff: noqa: E501

from __future__ import annotations

from tests.helpers import create_codex_session_file
from toktrail.adapters.code import list_code_sessions, parse_code_file, scan_code_path
from toktrail.adapters.codex import parse_codex_file


def test_parse_code_file_reuses_codex_format_under_code_harness(tmp_path) -> None:
    session_file = tmp_path / "code" / "session-001.jsonl"
    create_codex_session_file(session_file)

    events = parse_code_file(session_file)

    assert len(events) == 1
    assert events[0].harness == "code"
    assert events[0].global_dedup_key.startswith("code:session-001:")
    assert events[0].provider_id == "openai"
    assert events[0].model_id == "gpt-5.2-codex"
    assert events[0].tokens.input == 100
    assert events[0].tokens.cache_read == 20
    assert events[0].tokens.output == 30
    assert events[0].tokens.reasoning == 5


def test_parse_code_file_uses_distinct_dedup_identity_from_codex(tmp_path) -> None:
    session_file = tmp_path / "shared" / "session-001.jsonl"
    create_codex_session_file(session_file)

    code_event = parse_code_file(session_file)[0]
    codex_event = parse_codex_file(session_file)[0]

    assert code_event.harness == "code"
    assert codex_event.harness == "codex"
    assert code_event.global_dedup_key != codex_event.global_dedup_key
    assert code_event.fingerprint_hash == codex_event.fingerprint_hash


def test_scan_code_path_supports_source_session_filter(tmp_path) -> None:
    first = tmp_path / "code" / "first.jsonl"
    second = tmp_path / "code" / "second.jsonl"
    create_codex_session_file(first)
    create_codex_session_file(second)

    scan = scan_code_path(tmp_path / "code", source_session_id="second")

    assert [event.source_session_id for event in scan.events] == ["second"]
    assert {event.harness for event in scan.events} == {"code"}


def test_list_code_sessions_aggregates_messages(tmp_path) -> None:
    session_file = tmp_path / "code" / "session-001.jsonl"
    create_codex_session_file(session_file)

    summaries = list_code_sessions(session_file)

    assert len(summaries) == 1
    assert summaries[0].harness == "code"
    assert summaries[0].source_session_id == "session-001"
    assert summaries[0].assistant_message_count == 1
