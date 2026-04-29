from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest

from tests.helpers import VALID_ASSISTANT, create_opencode_db, insert_message
from toktrail.api.imports import import_configured_usage, import_usage
from toktrail.api.reports import session_report
from toktrail.api.sessions import init_state, start_session
from toktrail.errors import (
    InvalidAPIUsageError,
    SessionNotFoundError,
)


def test_import_usage_defaults_to_active_session_and_is_idempotent(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)
    session = start_session(state_db, name="import-test")

    first = import_usage(state_db, "opencode", source_path=source_db)
    second = import_usage(state_db, "opencode", source_path=source_db)
    report = session_report(state_db, session.id)

    assert first.tracking_session_id == session.id
    assert first.rows_imported == 2
    assert first.events_imported == 2
    assert second.rows_imported == 0
    assert report.totals.tokens.total == 3900


def test_import_usage_without_active_session_imports_unscoped_events(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)

    result = import_usage(state_db, "opencode", source_path=source_db)

    assert result.tracking_session_id is None
    assert result.rows_imported == 2
    with pytest.raises(SessionNotFoundError, match="Tracking session not found"):
        import_usage(state_db, "opencode", session_id=999, source_path=source_db)


def test_import_usage_since_start_and_source_session_filters(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    early = deepcopy(VALID_ASSISTANT)
    early["time"] = {"created": 10.0, "completed": 11.0}
    insert_message(conn, row_id="row-1", session_id="ses-1", data=early)
    later = deepcopy(VALID_ASSISTANT)
    later["id"] = "msg_999"
    later["time"] = {"created": 200.0, "completed": 201.0}
    insert_message(conn, row_id="row-2", session_id="ses-2", data=later)
    conn.commit()
    conn.close()

    init_state(state_db)
    session = start_session(state_db, name="since", started_at_ms=100)
    result = import_usage(
        state_db,
        "opencode",
        session_id=session.id,
        source_path=source_db,
        since_start=True,
        source_session_id="ses-2",
    )
    report = session_report(state_db, session.id)

    assert result.since_ms == 100
    assert result.rows_imported == 1
    assert report.by_harness[0].message_count == 1


def test_import_usage_rejects_conflicting_since_options(tmp_path) -> None:
    with pytest.raises(InvalidAPIUsageError, match="cannot be used together"):
        import_usage(
            tmp_path / "toktrail.db",
            "opencode",
            source_path=tmp_path / "source.db",
            since_start=True,
            since_ms=123,
        )


def test_import_usage_include_raw_json_false_stores_no_raw_json(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)
    start_session(state_db, name="raw")

    result = import_usage(
        state_db,
        "opencode",
        source_path=source_db,
        include_raw_json=False,
    )

    conn = sqlite3.connect(state_db)
    row = conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE raw_json IS NULL"
    ).fetchone()
    conn.close()

    assert result.events_skipped == 0
    assert row is not None
    assert row[0] == 2


def test_import_usage_can_ignore_active_session_when_requested(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)
    start_session(state_db, name="active")

    result = import_usage(
        state_db,
        "opencode",
        source_path=source_db,
        use_active_session=False,
    )

    assert result.tracking_session_id is None
    assert result.rows_imported == 2


def test_import_configured_usage_imports_all_configured_harnesses(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode", "pi"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{source_db}"
pi = "{tmp_path / 'missing-pi'}"
""".strip(),
        encoding="utf-8",
    )
    init_state(state_db)

    results = import_configured_usage(state_db, config_path=config_path)

    assert [
        (result.harness, result.status, result.rows_imported) for result in results
    ] == [
        ("opencode", "ok", 2),
        ("pi", "skipped", 0),
    ]


def test_later_session_import_links_existing_unscoped_events_without_duplicates(
    tmp_path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)

    first = import_usage(state_db, "opencode", source_path=source_db)
    session = start_session(state_db, name="linked")
    second = import_usage(
        state_db,
        "opencode",
        session_id=session.id,
        source_path=source_db,
    )
    report = session_report(state_db, session.id)

    assert first.tracking_session_id is None
    assert first.rows_imported == 2
    assert second.rows_imported == 0
    assert second.rows_linked == 2
    assert report.totals.tokens.total == 3900


def _create_opencode_messages(path) -> None:
    conn = create_opencode_db(path)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=deepcopy(VALID_ASSISTANT),
    )
    second = deepcopy(VALID_ASSISTANT)
    second["id"] = "msg_456"
    second["tokens"] = {
        "input": 1500,
        "output": 400,
        "reasoning": 50,
        "cache": {"read": 100, "write": 0},
    }
    insert_message(conn, row_id="row-2", session_id="ses-1", data=second)
    conn.commit()
    conn.close()
