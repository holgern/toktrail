# ruff: noqa: E501

from __future__ import annotations

from pathlib import Path

from toktrail.adapters.copilot import (
    list_copilot_sessions,
    parse_copilot_file,
    scan_copilot_file,
    scan_copilot_path,
)


def create_test_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_copilot_chat_span(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"metric","name":"gen_ai.client.token.usage"}\n'
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude-sonnet-4","startTime":[1775934260,133000000],"endTime":[1775934264,967317833],"attributes":{"gen_ai.operation.name":"chat","gen_ai.request.model":"claude-sonnet-4","gen_ai.response.model":"claude-sonnet-4","gen_ai.conversation.id":"conv-1","gen_ai.usage.input_tokens":19452,"gen_ai.usage.output_tokens":281,"gen_ai.usage.cache_read.input_tokens":123,"gen_ai.usage.reasoning.output_tokens":128,"github.copilot.interaction_id":"interaction-1"}}\n'
        ),
    )

    events = parse_copilot_file(copilot_file)

    assert len(events) == 1
    event = events[0]
    assert event.harness == "copilot"
    assert event.model_id == "claude-sonnet-4"
    assert event.provider_id == "anthropic"
    assert event.source_session_id == "conv-1"
    assert event.tokens.input == 19_329
    assert event.tokens.output == 281
    assert event.tokens.cache_read == 123
    assert event.tokens.reasoning == 128
    assert event.created_ms == 1_775_934_264_967
    assert event.source_dedup_key == "trace-1:span-1"
    assert event.global_dedup_key == "copilot:trace-1:span-1"


def test_parse_copilot_ignores_non_chat_spans(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-tool","spanId":"span-tool","name":"execute_tool search","attributes":{"gen_ai.operation.name":"execute_tool","gen_ai.usage.input_tokens":10,"gen_ai.usage.output_tokens":2}}\n'
            '{"type":"span","traceId":"trace-agent","spanId":"span-agent","name":"invoke_agent planner","attributes":{"gen_ai.operation.name":"invoke_agent","gen_ai.usage.input_tokens":20,"gen_ai.usage.output_tokens":4}}\n'
            '{"type":"span","traceId":"trace-chat","spanId":"span-chat","name":"chat gpt-5","endTime":[1775934264,967317833],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"gpt-5","gen_ai.usage.input_tokens":100,"gen_ai.usage.output_tokens":5}}\n'
        ),
    )

    events = parse_copilot_file(copilot_file)

    assert len(events) == 1
    assert events[0].source_dedup_key == "trace-chat:span-chat"


def test_parse_copilot_falls_back_to_trace_and_provider(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-fallback","spanId":"span-1","name":"chat custom-model","endTime":[1775934264,0],"attributes":{"gen_ai.request.model":"custom-model","gen_ai.usage.input_tokens":"7","gen_ai.usage.output_tokens":"9"}}\n'
        ),
    )

    events = parse_copilot_file(copilot_file)

    assert len(events) == 1
    event = events[0]
    assert event.provider_id == "github-copilot"
    assert event.source_session_id == "trace-fallback"
    assert event.tokens.input == 7
    assert event.tokens.output == 9


def test_parse_copilot_normalizes_only_cache_read_from_input(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.usage.input_tokens":1000,"gen_ai.usage.output_tokens":1,"gen_ai.usage.cache_read.input_tokens":200,"gen_ai.usage.cache_write.input_tokens":50}}\n'
        ),
    )

    events = parse_copilot_file(copilot_file)

    assert len(events) == 1
    assert events[0].tokens.input == 800
    assert events[0].tokens.cache_read == 200
    assert events[0].tokens.cache_write == 50


def test_parse_copilot_clamps_only_cache_read_to_input(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.usage.input_tokens":100,"gen_ai.usage.cache_read.input_tokens":90,"gen_ai.usage.cache_write.input_tokens":20}}\n'
        ),
    )

    events = parse_copilot_file(copilot_file)

    assert len(events) == 1
    assert events[0].tokens.input == 10
    assert events[0].tokens.cache_read == 90
    assert events[0].tokens.cache_write == 20


def test_parse_copilot_keeps_cache_only_message(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.usage.cache_read.input_tokens":50,"gen_ai.usage.cache_write.input_tokens":20}}\n'
        ),
    )

    events = parse_copilot_file(copilot_file)

    assert len(events) == 1
    assert events[0].tokens.input == 0
    assert events[0].tokens.cache_read == 50
    assert events[0].tokens.cache_write == 20


def test_parse_copilot_keeps_cache_read_when_input_is_missing(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.usage.cache_read.input_tokens":50}}\n'
        ),
    )

    events = parse_copilot_file(copilot_file)

    assert len(events) == 1
    assert events[0].tokens.input == 0
    assert events[0].tokens.cache_read == 50


def test_parse_copilot_extracts_thinking_level_from_attributes(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.request.reasoning_effort":" high ","gen_ai.usage.input_tokens":100}}\n'
        ),
    )

    events = parse_copilot_file(copilot_file)

    assert len(events) == 1
    assert events[0].thinking_level == "high"


def test_parse_copilot_returns_empty_for_missing_file(tmp_path) -> None:
    missing_file = tmp_path / "missing.jsonl"

    assert parse_copilot_file(missing_file) == []


def test_scan_copilot_file_supports_source_session_filter(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.conversation.id":"conv-1","gen_ai.usage.input_tokens":10}}\n'
            '{"type":"span","traceId":"trace-2","spanId":"span-2","name":"chat claude","endTime":[1775934265,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.conversation.id":"conv-2","gen_ai.usage.input_tokens":20}}\n'
        ),
    )

    scan = scan_copilot_file(copilot_file, source_session_id="conv-1")

    assert len(scan.events) == 1
    assert scan.events[0].source_session_id == "conv-1"
    assert scan.rows_skipped == 1


def test_parse_copilot_stores_no_raw_when_disabled(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.usage.input_tokens":100}}\n'
        ),
    )

    scan = scan_copilot_file(copilot_file, include_raw_json=False)

    assert len(scan.events) == 1
    assert scan.events[0].raw_json is None


def test_scan_copilot_path_reads_directory(tmp_path) -> None:
    create_test_file(
        tmp_path / "a.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.conversation.id":"conv-1","gen_ai.usage.input_tokens":10}}\n'
        ),
    )
    create_test_file(
        tmp_path / "nested" / "b.jsonl",
        (
            '{"type":"span","traceId":"trace-2","spanId":"span-2","name":"chat gpt-5","endTime":[1775934265,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"gpt-5","gen_ai.conversation.id":"conv-2","gen_ai.usage.input_tokens":20}}\n'
        ),
    )

    scan = scan_copilot_path(tmp_path)

    assert scan.files_seen == 2
    assert len(scan.events) == 2
    assert {event.source_session_id for event in scan.events} == {"conv-1", "conv-2"}


def test_list_copilot_sessions_aggregates_messages(tmp_path) -> None:
    copilot_file = create_test_file(
        tmp_path / "copilot.jsonl",
        (
            '{"type":"span","traceId":"trace-1","spanId":"span-1","name":"chat claude","endTime":[1775934264,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.conversation.id":"conv-1","gen_ai.usage.input_tokens":10}}\n'
            '{"type":"span","traceId":"trace-2","spanId":"span-2","name":"chat claude","endTime":[1775934265,0],"attributes":{"gen_ai.operation.name":"chat","gen_ai.response.model":"claude-sonnet-4","gen_ai.conversation.id":"conv-1","gen_ai.usage.input_tokens":20}}\n'
        ),
    )

    summaries = list_copilot_sessions(copilot_file)

    assert len(summaries) == 1
    assert summaries[0].source_session_id == "conv-1"
    assert summaries[0].assistant_message_count == 2
    assert summaries[0].tokens.total == 30
    assert summaries[0].source_paths == (str(copilot_file),)
