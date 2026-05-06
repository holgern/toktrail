from __future__ import annotations

import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from tests.helpers import (
    VALID_ASSISTANT,
    create_codex_session_file,
    create_opencode_db,
    insert_message,
)
from tests.test_amp_parser import create_amp_source
from tests.test_droid_parser import write_droid_settings
from tests.test_goose_parser import create_goose_db, insert_session
from toktrail.api.imports import import_configured_usage, import_usage
from toktrail.api.models import RunScope
from toktrail.api.reports import session_report
from toktrail.api.sessions import init_state, start_run
from toktrail.errors import (
    InvalidAPIUsageError,
    RunNotFoundError,
)


def _toml_path_value(path: Path) -> str:
    return str(path).replace("\\", "/")


@pytest.fixture(autouse=True)
def _isolate_toktrail_config(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(config_path))


def test_import_usage_defaults_to_active_session_and_is_idempotent(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)
    session = start_run(state_db, name="import-test", started_at_ms=0)

    first = import_usage(state_db, "opencode", source_path=source_db)
    second = import_usage(state_db, "opencode", source_path=source_db)
    report = session_report(state_db, session.id)

    assert first.run_id == session.id
    assert first.rows_imported == 2
    assert first.events_imported == 2
    assert second.rows_imported == 0
    assert report.totals.tokens.total == 3400


def test_import_usage_without_active_session_imports_unscoped_events(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)

    result = import_usage(state_db, "opencode", source_path=source_db)

    assert result.run_id is None
    assert result.rows_imported == 2
    with pytest.raises(RunNotFoundError, match="Run not found"):
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
    session = start_run(state_db, name="since", started_at_ms=100)
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


def test_import_configured_usage_since_start_filters_events(tmp_path) -> None:
    """import_configured_usage with since_start=True filters events before run start."""
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"

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

    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "error"

[imports.sources]
opencode = "{_toml_path_value(source_db)}"
""".strip(),
        encoding="utf-8",
    )

    init_state(state_db)
    session = start_run(state_db, name="cfg-since", started_at_ms=100)
    results = import_configured_usage(
        state_db,
        session_id=session.id,
        use_active_session=False,
        config_path=config_path,
        since_start=True,
    )
    report = session_report(state_db, session.id)

    assert results[0].since_ms == 100
    assert results[0].rows_imported == 1
    assert report.by_harness[0].message_count == 1


def test_import_configured_usage_rejects_since_start_and_since_ms(
    tmp_path,
) -> None:
    with pytest.raises(InvalidAPIUsageError, match="cannot be used together"):
        import_configured_usage(
            tmp_path / "toktrail.db",
            config_path=tmp_path / "toktrail.toml",
            since_start=True,
            since_ms=123,
        )


def test_import_usage_include_raw_json_false_stores_no_raw_json(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)
    start_run(state_db, name="raw", started_at_ms=0)

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
    start_run(state_db, name="active")

    result = import_usage(
        state_db,
        "opencode",
        source_path=source_db,
        use_active_session=False,
    )

    assert result.run_id is None
    assert result.rows_imported == 2


def test_import_usage_active_run_filters_historical_rows_by_default(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)
    session = start_run(state_db, name="bounded-default")

    result = import_usage(state_db, "opencode", source_path=source_db)
    report = session_report(state_db, session.id)

    assert result.run_id == session.id
    assert result.rows_seen == 2
    assert result.rows_imported == 0
    assert result.rows_skipped == 2
    assert result.since_ms == session.started_at_ms
    assert report.totals.tokens.total == 0


def test_import_usage_since_ms_cannot_widen_before_run_start(tmp_path) -> None:
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
    session = start_run(state_db, name="since-cap", started_at_ms=100)

    result = import_usage(
        state_db,
        "opencode",
        session_id=session.id,
        source_path=source_db,
        since_ms=1,
    )
    report = session_report(state_db, session.id)

    assert result.since_ms == 100
    assert result.rows_imported == 1
    assert report.by_harness[0].message_count == 1


def test_import_usage_supports_codex_source(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)
    init_state(state_db)
    session = start_run(state_db, name="codex", started_at_ms=0)

    first = import_usage(
        state_db,
        "codex",
        source_path=codex_file,
        session_id=session.id,
    )
    second = import_usage(
        state_db,
        "codex",
        source_path=codex_file,
        session_id=session.id,
    )
    report = session_report(state_db, session.id)

    assert first.rows_imported == 1
    assert second.rows_imported == 0
    assert report.totals.tokens.input == 100
    assert report.totals.tokens.cache_read == 20
    assert report.totals.tokens.output == 30
    assert report.totals.tokens.reasoning == 5
    assert report.totals.tokens.total == 130


def test_import_usage_supports_droid_source(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    droid_source = tmp_path / "factory" / "sessions"
    write_droid_settings(droid_source / "droid-1.settings.json")
    init_state(state_db)
    session = start_run(state_db, name="droid", started_at_ms=0)

    first = import_usage(
        state_db,
        "droid",
        source_path=droid_source,
        session_id=session.id,
    )
    second = import_usage(
        state_db,
        "droid",
        source_path=droid_source,
        session_id=session.id,
    )
    report = session_report(state_db, session.id)

    assert first.rows_imported == 1
    assert second.rows_imported == 0
    assert report.by_harness[0].harness == "droid"
    assert report.totals.tokens.input == 1234
    assert report.totals.tokens.output == 567
    assert report.totals.tokens.reasoning == 34
    assert report.totals.tokens.cache_read == 12
    assert report.totals.tokens.cache_write == 89
    assert report.totals.tokens.total == 1801


def test_import_usage_supports_amp_source(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    amp_source = tmp_path / "amp" / "threads"
    create_amp_source(amp_source / "thread-1.json")
    init_state(state_db)
    session = start_run(state_db, name="amp", started_at_ms=0)

    first = import_usage(
        state_db,
        "amp",
        source_path=amp_source,
        session_id=session.id,
    )
    second = import_usage(
        state_db,
        "amp",
        source_path=amp_source,
        session_id=session.id,
    )
    report = session_report(state_db, session.id)

    assert first.rows_imported == 1
    assert second.rows_imported == 0
    assert report.by_harness[0].harness == "amp"
    assert report.totals.tokens.input == 100
    assert report.totals.tokens.output == 20
    assert report.totals.tokens.cache_read == 30
    assert report.totals.tokens.cache_write == 40
    assert report.totals.tokens.total == 120
    assert report.totals.costs.source_cost_usd == 0.75


def test_import_configured_usage_imports_all_configured_harnesses(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    goose_db = tmp_path / "goose" / "sessions.db"
    droid_source = tmp_path / "factory" / "sessions"
    amp_source = tmp_path / "amp" / "threads"
    _create_opencode_messages(source_db)
    create_codex_session_file(codex_file)
    create_goose_db(goose_db)
    insert_session(goose_db)
    write_droid_settings(droid_source / "droid-1.settings.json")
    create_amp_source(amp_source / "thread-1.json")
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode", "pi", "codex", "goose", "droid", "amp"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"
pi = "{_toml_path_value(tmp_path / "missing-pi")}"
codex = "{_toml_path_value(codex_file)}"
goose = "{_toml_path_value(goose_db)}"
droid = "{_toml_path_value(droid_source)}"
amp = "{_toml_path_value(amp_source)}"
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
        ("codex", "ok", 1),
        ("goose", "ok", 1),
        ("droid", "ok", 1),
        ("amp", "ok", 1),
    ]


def test_later_session_import_links_existing_unscoped_events_without_duplicates(
    tmp_path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    _create_opencode_messages(source_db)
    init_state(state_db)

    first = import_usage(state_db, "opencode", source_path=source_db)
    session = start_run(state_db, name="linked", started_at_ms=0)
    second = import_usage(
        state_db,
        "opencode",
        session_id=session.id,
        source_path=source_db,
    )
    report = session_report(state_db, session.id)

    assert first.run_id is None
    assert first.rows_imported == 2
    assert second.rows_imported == 0
    assert second.rows_linked == 2
    assert report.totals.tokens.total == 3400


def test_import_configured_usage_applies_active_run_harness_scope(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    config_path = tmp_path / "toktrail.toml"
    _create_opencode_messages(source_db)
    create_codex_session_file(codex_file)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode", "codex"]
missing_source = "error"

[imports.sources]
opencode = "{_toml_path_value(source_db)}"
codex = "{_toml_path_value(codex_file)}"
""".strip(),
        encoding="utf-8",
    )
    init_state(state_db)
    start_run(
        state_db,
        name="codex-only",
        scope=RunScope(harnesses=("codex",)),
    )

    results = import_configured_usage(state_db, config_path=config_path)

    assert [result.harness for result in results] == ["codex"]


def test_import_usage_links_only_provider_model_scope(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    first = deepcopy(VALID_ASSISTANT)
    first["providerID"] = "openai"
    first["modelID"] = "gpt-5.5"
    insert_message(conn, row_id="row-1", session_id="ses-1", data=first)
    second = deepcopy(VALID_ASSISTANT)
    second["id"] = "msg-2"
    second["providerID"] = "anthropic"
    second["modelID"] = "claude-sonnet-4"
    insert_message(conn, row_id="row-2", session_id="ses-1", data=second)
    conn.commit()
    conn.close()

    init_state(state_db)
    session = start_run(
        state_db,
        name="scoped-model",
        scope=RunScope(provider_ids=("openai",), model_ids=("gpt-5.5",)),
        started_at_ms=0,
    )
    result = import_usage(
        state_db,
        "opencode",
        session_id=session.id,
        source_path=source_db,
    )
    report = session_report(state_db, session.id)

    assert result.rows_imported == 2
    assert result.rows_linked == 1
    assert result.rows_scope_excluded == 1
    assert report.totals.tokens.total == (
        first["tokens"]["input"] + first["tokens"]["output"]
    )


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
