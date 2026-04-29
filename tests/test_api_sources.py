from __future__ import annotations

from copy import deepcopy

import pytest

from tests.helpers import (
    VALID_ASSISTANT,
    create_codex_session_file,
    create_copilot_file,
    create_opencode_db,
    create_pi_session_file,
    insert_message,
    write_jsonl_rows,
)
from tests.test_amp_parser import create_amp_source
from tests.test_droid_parser import write_droid_settings
from tests.test_goose_parser import create_goose_db, insert_session
from toktrail.api.config import init_config
from toktrail.api.sources import (
    capture_source_snapshot,
    diff_source_snapshots,
    list_source_sessions,
    scan_usage,
)
from toktrail.errors import (
    AmbiguousSourceSessionError,
    InvalidAPIUsageError,
    SourcePathError,
)


def _build_opencode_source(tmp_path):
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()
    return source_db


def _build_pi_source(tmp_path):
    session_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    create_pi_session_file(session_file)
    return session_file


def _build_copilot_source(tmp_path):
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)
    return copilot_file


def _build_codex_source(tmp_path):
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)
    return codex_file


def _build_goose_source(tmp_path):
    goose_db = tmp_path / "goose" / "sessions.db"
    create_goose_db(goose_db)
    insert_session(goose_db, session_id="goose-1")
    return goose_db


def _build_droid_source(tmp_path):
    source = tmp_path / "factory" / "sessions"
    write_droid_settings(source / "droid-1.settings.json")
    return source


def _build_amp_source(tmp_path):
    source = tmp_path / "amp" / "threads"
    create_amp_source(source / "thread-1.json")
    return source


@pytest.mark.parametrize(
    ("harness", "builder", "source_session_id"),
    (
        ("opencode", _build_opencode_source, "ses-1"),
        ("pi", _build_pi_source, "pi_ses_001"),
        ("copilot", _build_copilot_source, "conv-1"),
        ("codex", _build_codex_source, "session-001"),
        ("goose", _build_goose_source, "goose-1"),
        ("droid", _build_droid_source, "droid-1"),
        ("amp", _build_amp_source, "thread-1"),
    ),
)
def test_capture_source_snapshot_supports_all_harnesses(
    tmp_path,
    harness: str,
    builder,
    source_session_id: str,
) -> None:
    source_path = builder(tmp_path)

    snapshot = capture_source_snapshot(harness, source_path=source_path)

    assert snapshot.harness == harness
    assert snapshot.source_path == source_path
    assert snapshot.sessions[0].source_session_id == source_session_id
    assert snapshot.sessions[0].assistant_message_count == 1


def test_list_source_sessions_supports_last_limit_source_session_and_sort(
    tmp_path,
) -> None:
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-a" / "a.jsonl")
    write_jsonl_rows(
        session_dir / "encoded-b" / "b.jsonl",
        [
            {
                "type": "session",
                "id": "pi_ses_999",
                "timestamp": "2026-01-01T00:00:00.000Z",
                "cwd": "/tmp",
            },
            {
                "type": "message",
                "id": "msg_999",
                "parentId": None,
                "timestamp": "2026-01-01T00:00:02.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-3-5-sonnet",
                    "provider": "anthropic",
                    "usage": {
                        "input": 200,
                        "output": 100,
                        "cacheRead": 20,
                        "cacheWrite": 10,
                        "totalTokens": 330,
                    },
                },
            },
        ],
    )

    sessions = list_source_sessions(
        "pi", source_path=session_dir, sort="tokens", limit=1
    )
    latest = list_source_sessions("pi", source_path=session_dir, last=True)
    selected = list_source_sessions(
        "pi",
        source_path=session_dir,
        source_session_id="pi_ses_001",
    )

    assert sessions[0].source_session_id == "pi_ses_999"
    assert latest[0].source_session_id == "pi_ses_999"
    assert selected[0].source_session_id == "pi_ses_001"


def test_list_source_sessions_supports_cost_sort_values(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    init_config(config_path, template="copilot")
    copilot_dir = tmp_path / "copilot"
    create_copilot_file(copilot_dir / "first.jsonl")
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

    for sort_value in ("actual", "virtual", "savings"):
        rows = list_source_sessions(
            "copilot",
            source_path=copilot_dir,
            sort=sort_value,
            limit=1,
            config_path=config_path,
        )
        assert rows[0].source_session_id == "conv-2"


def test_diff_source_snapshots_identifies_new_updated_and_unchanged(tmp_path) -> None:
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()

    before = capture_source_snapshot("opencode", source_path=source_db)

    updated = deepcopy(VALID_ASSISTANT)
    updated["id"] = "msg_456"
    updated["tokens"] = {
        "input": 2000,
        "output": 100,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }
    insert_message(conn, row_id="row-2", session_id="ses-1", data=updated)
    second_session = deepcopy(VALID_ASSISTANT)
    second_session["id"] = "msg_222"
    second_session["time"] = {"created": 1700000001000.0, "completed": 1700000001500.0}
    insert_message(conn, row_id="row-3", session_id="ses-2", data=second_session)
    conn.commit()
    conn.close()

    after = capture_source_snapshot("opencode", source_path=source_db)
    diff = diff_source_snapshots(before, after)

    assert [summary.source_session_id for summary in diff.updated_sessions] == ["ses-1"]
    assert [summary.source_session_id for summary in diff.new_sessions] == ["ses-2"]
    assert diff.unchanged_sessions == ()


def test_diff_require_single_candidate_raises_for_zero_and_multiple(tmp_path) -> None:
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    conn.commit()
    conn.close()

    snapshot = capture_source_snapshot("opencode", source_path=source_db)
    no_change = diff_source_snapshots(snapshot, snapshot)
    with pytest.raises(SourcePathError, match="No new or updated"):
        no_change.require_single_candidate()

    conn = create_opencode_db(tmp_path / "opencode-2.db")
    conn.close()
    multiple = diff_source_snapshots(
        snapshot,
        type(snapshot)(
            harness=snapshot.harness,
            source_path=snapshot.source_path,
            captured_ms=snapshot.captured_ms + 1,
            sessions=(
                snapshot.sessions[0].__class__(
                    harness="opencode",
                    source_session_id="ses-1",
                    first_created_ms=snapshot.sessions[0].first_created_ms,
                    last_created_ms=snapshot.sessions[0].last_created_ms + 1,
                    assistant_message_count=2,
                    tokens=snapshot.sessions[0].tokens,
                    costs=snapshot.sessions[0].costs,
                    models=snapshot.sessions[0].models,
                    providers=snapshot.sessions[0].providers,
                    source_paths=snapshot.sessions[0].source_paths,
                ),
                snapshot.sessions[0].__class__(
                    harness="opencode",
                    source_session_id="ses-2",
                    first_created_ms=1,
                    last_created_ms=2,
                    assistant_message_count=1,
                    tokens=snapshot.sessions[0].tokens,
                    costs=snapshot.sessions[0].costs,
                    models=snapshot.sessions[0].models,
                    providers=snapshot.sessions[0].providers,
                    source_paths=snapshot.sessions[0].source_paths,
                ),
            ),
        ),
    )
    with pytest.raises(AmbiguousSourceSessionError, match="ses-1, ses-2"):
        multiple.require_single_candidate()


def test_api_sources_invalid_usage_and_missing_paths_raise(tmp_path) -> None:
    with pytest.raises(InvalidAPIUsageError, match="cannot be used together"):
        list_source_sessions(
            "pi", source_path=tmp_path, source_session_id="x", last=True
        )
    with pytest.raises(SourcePathError, match="Pi sessions path not found"):
        scan_usage("pi", source_path=tmp_path / "missing")


def test_scan_usage_defaults_to_no_raw_json(tmp_path) -> None:
    source_path = _build_copilot_source(tmp_path)

    result = scan_usage("copilot", source_path=source_path)

    assert result.events[0].raw_json is None
