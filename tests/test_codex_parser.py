# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path

from toktrail.adapters.codex import (
    _CodexTotals,
    list_codex_sessions,
    parse_codex_file,
    scan_codex_file,
    scan_codex_path,
)


def write_codex_session(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def write_codex_rows(path: Path, rows: list[dict[str, object]]) -> Path:
    return write_codex_session(
        path,
        "".join(f"{json.dumps(row)}\n" for row in rows),
    )


def test_parse_headless_usage_line(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "session.jsonl",
        [
            {
                "type": "turn.completed",
                "model": "gpt-4o-mini",
                "usage": {
                    "input_tokens": 120,
                    "cached_input_tokens": 20,
                    "output_tokens": 30,
                },
            }
        ],
    )

    events = parse_codex_file(session_file)

    assert len(events) == 1
    event = events[0]
    assert event.model_id == "gpt-4o-mini"
    assert event.provider_id == "openai"
    assert event.tokens.input == 100
    assert event.tokens.output == 30
    assert event.tokens.cache_read == 20


def test_parse_headless_usage_nested_data(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "nested.jsonl",
        [
            {
                "type": "result",
                "data": {
                    "model_name": "gpt-4o",
                    "usage": {
                        "input_tokens": 50,
                        "cached_input_tokens": 5,
                        "output_tokens": 12,
                    },
                },
            }
        ],
    )

    event = parse_codex_file(session_file)[0]

    assert event.model_id == "gpt-4o"
    assert event.tokens.input == 45
    assert event.tokens.output == 12
    assert event.tokens.cache_read == 5


def test_session_meta_exec_marks_headless(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "headless.jsonl",
        [
            {"type": "session_meta", "payload": {"source": "exec"}},
            _token_count_row(last={"input_tokens": 10}),
        ],
    )

    event = parse_codex_file(session_file)[0]

    assert event.agent == "headless"


def test_session_meta_provider_and_agent(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "meta.jsonl",
        [
            {
                "type": "session_meta",
                "payload": {
                    "model_provider": "azure",
                    "agent_nickname": "my-agent",
                },
            },
            _token_count_row(last={"input_tokens": 10}),
        ],
    )

    event = parse_codex_file(session_file)[0]

    assert event.provider_id == "azure"
    assert event.agent == "my-agent"


def test_model_info_slug_from_turn_context(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "turn-context.jsonl",
        [
            {"type": "turn_context", "payload": {"model_info": {"slug": "o3-pro"}}},
            _token_count_row(last={"input_tokens": 10}),
        ],
    )

    event = parse_codex_file(session_file)[0]

    assert event.model_id == "o3-pro"


def test_extract_model_skips_empty_slug_falls_through_to_model(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "turn-context-empty.jsonl",
        [
            {"type": "turn_context", "payload": {"model_info": {"slug": "   "}}},
            _token_count_row(
                last={"input_tokens": 10},
                payload={"model": "gpt-5.4-mini"},
            ),
        ],
    )

    event = parse_codex_file(session_file)[0]

    assert event.model_id == "gpt-5.4-mini"


def test_token_count_repeated_totals_are_deduped(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "repeated.jsonl",
        [
            _token_count_row(
                total={"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 30},
                last={"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 30},
            ),
            _token_count_row(
                total={"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 30},
                last={"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 30},
                timestamp="2026-01-01T00:00:02Z",
            ),
        ],
    )

    events = parse_codex_file(session_file)

    assert len(events) == 1


def test_token_count_falls_back_to_last_usage_when_totals_reset(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "reset.jsonl",
        [
            _token_count_row(
                total={"input_tokens": 100},
                last={"input_tokens": 100},
            ),
            _token_count_row(
                total={"input_tokens": 10},
                last={"input_tokens": 10},
                timestamp="2026-01-01T00:00:02Z",
            ),
        ],
    )

    events = parse_codex_file(session_file)

    assert len(events) == 2
    assert [event.tokens.input for event in events] == [100, 10]


def test_first_event_uses_last_not_total_for_resumed_sessions(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "resumed.jsonl",
        [
            _token_count_row(
                total={"input_tokens": 1000, "output_tokens": 500},
                last={"input_tokens": 10, "output_tokens": 2},
            )
        ],
    )

    event = parse_codex_file(session_file)[0]

    assert event.tokens.input == 10
    assert event.tokens.output == 2


def test_zero_token_snapshot_does_not_advance_baseline(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "zero-snapshot.jsonl",
        [
            _token_count_row(total={"input_tokens": 100}),
            _token_count_row(
                total={"input_tokens": 100},
                last={"input_tokens": 0},
                timestamp="2026-01-01T00:00:02Z",
            ),
            _token_count_row(
                total={"input_tokens": 130},
                timestamp="2026-01-01T00:00:03Z",
            ),
        ],
    )

    events = parse_codex_file(session_file)

    assert len(events) == 2
    assert [event.tokens.input for event in events] == [100, 30]


def test_cached_tokens_takes_max_of_both_fields(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "cached-max.jsonl",
        [
            _token_count_row(
                last={
                    "input_tokens": 50,
                    "cached_input_tokens": 10,
                    "cache_read_input_tokens": 20,
                }
            )
        ],
    )

    event = parse_codex_file(session_file)[0]

    assert event.tokens.input == 30
    assert event.tokens.cache_read == 20


def test_into_tokens_clamps_cached_to_input() -> None:
    tokens = _CodexTotals(input=50, cached=100).into_tokens()

    assert tokens.input == 0
    assert tokens.cache_read == 50


def test_token_count_avoids_double_counting_stale_cumulative_regressions(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "stale-regression.jsonl",
        [
            _token_count_row(total={"input_tokens": 100}, last={"input_tokens": 100}),
            _token_count_row(
                total={"input_tokens": 99},
                last={"input_tokens": 1},
                timestamp="2026-01-01T00:00:02Z",
            ),
        ],
    )

    events = parse_codex_file(session_file)

    assert len(events) == 1
    assert events[0].tokens.input == 100


def test_token_count_treats_large_regressions_as_real_resets(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "large-regression.jsonl",
        [
            _token_count_row(total={"input_tokens": 100}, last={"input_tokens": 100}),
            _token_count_row(
                total={"input_tokens": 10},
                last={"input_tokens": 10},
                timestamp="2026-01-01T00:00:02Z",
            ),
            _token_count_row(
                total={"input_tokens": 25},
                timestamp="2026-01-01T00:00:03Z",
            ),
        ],
    )

    events = parse_codex_file(session_file)

    assert len(events) == 3
    assert [event.tokens.input for event in events] == [100, 10, 15]


def test_scan_codex_path_reads_nested_jsonl_files(tmp_path) -> None:
    write_codex_rows(
        tmp_path / "2026-01-01" / "first.jsonl",
        [_token_count_row(last={"input_tokens": 10})],
    )
    write_codex_rows(
        tmp_path / "2026-01-02" / "second.jsonl",
        [_token_count_row(last={"input_tokens": 20})],
    )

    scan = scan_codex_path(tmp_path)

    assert scan.files_seen == 2
    assert len(scan.events) == 2
    assert {event.source_session_id for event in scan.events} == {"first", "second"}


def test_scan_codex_path_supports_source_session_filter(tmp_path) -> None:
    write_codex_rows(
        tmp_path / "2026-01-01" / "first.jsonl",
        [_token_count_row(last={"input_tokens": 10})],
    )
    write_codex_rows(
        tmp_path / "2026-01-02" / "second.jsonl",
        [_token_count_row(last={"input_tokens": 20})],
    )

    scan = scan_codex_path(tmp_path, source_session_id="second")

    assert len(scan.events) == 1
    assert scan.events[0].source_session_id == "second"
    assert scan.rows_skipped == 1


def test_parse_codex_stores_no_raw_when_disabled(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "no-raw.jsonl",
        [_token_count_row(last={"input_tokens": 10})],
    )

    scan = scan_codex_file(session_file, include_raw_json=False)

    assert len(scan.events) == 1
    assert scan.events[0].raw_json is None


def test_parse_codex_returns_empty_for_missing_file(tmp_path) -> None:
    assert parse_codex_file(tmp_path / "missing.jsonl") == []


def test_invalid_utf8_after_valid_row_preserves_valid_event_and_stops(tmp_path) -> None:
    session_file = write_codex_session(
        tmp_path / "invalid-utf8.jsonl",
        (
            b'{"type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":10}}}}\n'
            + b"\xff\n"
            + b'{"type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":20}}}}\n'
        ),
    )

    scan = scan_codex_file(session_file)

    assert len(scan.events) == 1
    assert scan.events[0].tokens.input == 10
    assert scan.rows_skipped == 1


def test_list_codex_sessions_aggregates_messages(tmp_path) -> None:
    session_file = write_codex_rows(
        tmp_path / "aggregate.jsonl",
        [
            _token_count_row(
                last={"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 30, "reasoning_output_tokens": 5},
                payload={"model": "gpt-5.2-codex"},
            ),
            _token_count_row(
                last={"input_tokens": 80, "output_tokens": 10},
                timestamp="2026-01-01T00:00:02Z",
                payload={"model": "gpt-5.2-codex"},
            ),
        ],
    )

    summaries = list_codex_sessions(session_file)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.source_session_id == "aggregate"
    assert summary.assistant_message_count == 2
    assert summary.tokens.input == 180
    assert summary.tokens.cache_read == 20
    assert summary.tokens.output == 40
    assert summary.tokens.reasoning == 5
    assert summary.source_paths == (str(session_file),)


def _token_count_row(
    *,
    total: dict[str, object] | None = None,
    last: dict[str, object] | None = None,
    timestamp: str = "2026-01-01T00:00:01Z",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    info: dict[str, object] = {}
    if total is not None:
        info["total_token_usage"] = total
    if last is not None:
        info["last_token_usage"] = last
    row_payload: dict[str, object] = {"type": "token_count", "info": info}
    if payload is not None:
        row_payload.update(payload)
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": row_payload,
    }
