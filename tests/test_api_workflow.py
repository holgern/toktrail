from __future__ import annotations

from copy import deepcopy

import pytest

from tests.helpers import (
    VALID_ASSISTANT,
    create_codex_session_file,
    create_opencode_db,
    insert_message,
    write_jsonl_rows,
)
from toktrail.api.workflow import finalize_manual_run, prepare_manual_run
from toktrail.errors import AmbiguousSourceSessionError, SourcePathError


def test_prepare_manual_run_returns_tracking_session_snapshot_and_environment(
    tmp_path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(
        conn, row_id="row-1", session_id="ses-1", data=deepcopy(VALID_ASSISTANT)
    )
    conn.commit()
    conn.close()

    prepared = prepare_manual_run(
        state_db,
        "opencode",
        name="workflow-opencode",
        source_path=source_db,
    )

    assert prepared.run.active is True
    assert prepared.before_snapshot.sessions[0].source_session_id == "ses-1"
    assert prepared.environment.env == {}


def test_finalize_manual_run_detects_updated_source_session_for_opencode(
    tmp_path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(
        conn, row_id="row-1", session_id="ses-1", data=deepcopy(VALID_ASSISTANT)
    )
    conn.commit()

    prepared = prepare_manual_run(
        state_db,
        "opencode",
        name="workflow-opencode",
        source_path=source_db,
    )

    updated = deepcopy(VALID_ASSISTANT)
    updated["id"] = "msg_999"
    updated["tokens"] = {
        "input": 2000,
        "output": 100,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }
    insert_message(conn, row_id="row-2", session_id="ses-1", data=updated)
    conn.commit()
    conn.close()

    finalized = finalize_manual_run(state_db, prepared)

    assert finalized.source_session.source_session_id == "ses-1"
    assert finalized.import_result.rows_imported == 2
    assert finalized.run.active is False


def test_prepare_and_finalize_manual_run_for_codex(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)

    prepared = prepare_manual_run(
        state_db,
        "codex",
        name="workflow-codex",
        source_path=codex_file,
    )

    with codex_file.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"timestamp":"2026-01-01T00:00:02Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":200,"cached_input_tokens":20,"output_tokens":40,"reasoning_output_tokens":5},"last_token_usage":{"input_tokens":80,"output_tokens":10}}}}\n'
        )

    finalized = finalize_manual_run(state_db, prepared)

    assert finalized.source_session.source_session_id == "session-001"
    assert finalized.import_result.rows_imported == 2
    assert finalized.report.totals.tokens.input == 180
    assert finalized.report.totals.tokens.cache_read == 20
    assert finalized.report.totals.tokens.output == 40
    assert finalized.report.totals.tokens.reasoning == 5
    assert finalized.run.active is False


def test_finalize_manual_run_explicit_source_session_id_bypasses_ambiguity(
    tmp_path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    copilot_dir = tmp_path / "copilot"
    prepared = prepare_manual_run(
        state_db,
        "copilot",
        name="workflow-copilot",
        source_path=copilot_dir,
    )
    write_jsonl_rows(
        copilot_dir / "first.jsonl",
        [
            {
                "type": "span",
                "traceId": "trace-1",
                "spanId": "span-1",
                "name": "chat claude-sonnet-4",
                "endTime": [1775934264, 967317833],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.response.model": "claude-sonnet-4",
                    "gen_ai.conversation.id": "conv-1",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 5,
                },
            }
        ],
    )
    write_jsonl_rows(
        copilot_dir / "second.jsonl",
        [
            {
                "type": "span",
                "traceId": "trace-2",
                "spanId": "span-2",
                "name": "chat claude-sonnet-4",
                "endTime": [1775934265, 967317833],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.response.model": "claude-sonnet-4",
                    "gen_ai.conversation.id": "conv-2",
                    "gen_ai.usage.input_tokens": 300,
                    "gen_ai.usage.output_tokens": 10,
                },
            }
        ],
    )

    finalized = finalize_manual_run(
        state_db,
        prepared,
        source_session_id="conv-2",
    )

    assert finalized.source_session.source_session_id == "conv-2"
    assert finalized.import_result.rows_imported == 1


def test_finalize_manual_run_raises_for_ambiguous_or_missing_changes(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    copilot_dir = tmp_path / "copilot"
    prepared = prepare_manual_run(
        state_db,
        "copilot",
        name="workflow-copilot",
        source_path=copilot_dir,
    )
    write_jsonl_rows(
        copilot_dir / "first.jsonl",
        [
            {
                "type": "span",
                "traceId": "trace-1",
                "spanId": "span-1",
                "name": "chat claude-sonnet-4",
                "endTime": [1775934264, 967317833],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.response.model": "claude-sonnet-4",
                    "gen_ai.conversation.id": "conv-1",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 5,
                },
            }
        ],
    )
    write_jsonl_rows(
        copilot_dir / "second.jsonl",
        [
            {
                "type": "span",
                "traceId": "trace-2",
                "spanId": "span-2",
                "name": "chat claude-sonnet-4",
                "endTime": [1775934265, 967317833],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.response.model": "claude-sonnet-4",
                    "gen_ai.conversation.id": "conv-2",
                    "gen_ai.usage.input_tokens": 300,
                    "gen_ai.usage.output_tokens": 10,
                },
            }
        ],
    )

    with pytest.raises(AmbiguousSourceSessionError, match="conv-1, conv-2"):
        finalize_manual_run(state_db, prepared)

    empty_state_db = tmp_path / "empty.db"
    empty_prepared = prepare_manual_run(
        empty_state_db,
        "copilot",
        name="workflow-empty",
        source_path=tmp_path / "empty-copilot",
    )
    with pytest.raises(SourcePathError, match="No new or updated"):
        finalize_manual_run(empty_state_db, empty_prepared)
