from __future__ import annotations

import sqlite3
from decimal import Decimal

from toktrail.api.events import record_usage_event, record_usage_events
from toktrail.api.models import TokenBreakdown, UsageEvent
from toktrail.api.reports import session_report, usage_report
from toktrail.api.sessions import init_state, start_run


def test_record_usage_event_without_active_run_imports_unscoped(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)

    result = record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-1",
        source_message_id="req-1",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=100, output=20),
        created_ms=1000,
    )
    report = usage_report(state_db)

    assert result.run_id is None
    assert result.rows_imported == 1
    assert result.rows_skipped == 0
    assert report.totals.tokens.total == 120


def test_record_usage_event_links_active_run_by_default(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)
    run = start_run(state_db, name="api-ingest", started_at_ms=0)

    result = record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-1",
        source_message_id="req-2",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=50, output=10),
        created_ms=1000,
    )
    report = session_report(state_db, run.id)

    assert result.run_id == run.id
    assert result.rows_imported == 1
    assert result.rows_linked == 1
    assert report.totals.tokens.total == 60


def test_record_usage_event_explicit_session_id(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)
    run = start_run(state_db, name="explicit-run", started_at_ms=0)

    result = record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-2",
        source_message_id="req-3",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=12, output=8),
        created_ms=2000,
        session_id=run.id,
        use_active_session=False,
    )

    assert result.run_id == run.id
    assert result.rows_imported == 1
    assert result.rows_linked == 1


def test_record_usage_event_is_idempotent_by_dedup_key(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)

    first = record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-3",
        source_message_id="req-4",
        source_dedup_key="dedup-1",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=20, output=4),
        created_ms=3000,
    )
    second = record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-3",
        source_message_id="req-4",
        source_dedup_key="dedup-1",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=20, output=4),
        created_ms=3000,
    )

    assert first.rows_imported == 1
    assert second.rows_imported == 0
    assert second.rows_skipped == 1


def test_record_usage_event_different_fingerprint_conflicts_or_skips_consistently(
    tmp_path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)

    first = record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-4",
        source_dedup_key="dedup-2",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=20, output=4),
        created_ms=4000,
    )
    second = record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-4",
        source_dedup_key="dedup-2",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=21, output=4),
        created_ms=4000,
    )
    report = usage_report(state_db, harness="my-app")

    assert first.rows_imported == 1
    assert second.rows_imported == 0
    assert second.rows_skipped == 1
    assert report.totals.tokens.total == 24


def test_record_usage_event_raw_json_is_opt_in(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)

    record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-5",
        source_message_id="req-5",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=10, output=1),
        raw_json={"secret": "value"},
        include_raw_json=False,
        created_ms=5000,
    )
    record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-5",
        source_message_id="req-6",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(input=10, output=1),
        raw_json={"secret": "value"},
        include_raw_json=True,
        created_ms=5001,
    )

    conn = sqlite3.connect(state_db)
    without_raw = conn.execute(
        "SELECT COUNT(*) FROM usage_events "
        "WHERE source_message_id = 'req-5' AND raw_json IS NULL"
    ).fetchone()
    with_raw = conn.execute(
        "SELECT COUNT(*) FROM usage_events "
        "WHERE source_message_id = 'req-6' AND raw_json IS NOT NULL"
    ).fetchone()
    conn.close()

    assert without_raw is not None and without_raw[0] == 1
    assert with_raw is not None and with_raw[0] == 1


def test_record_usage_event_shows_in_usage_report_by_provider_model_and_harness(
    tmp_path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)

    result = record_usage_event(
        state_db,
        harness="my-app",
        source_session_id="job-6",
        source_message_id="req-7",
        provider_id="openai",
        model_id="gpt-5.5",
        tokens=TokenBreakdown(
            input=12000,
            output=800,
            reasoning=120,
            cache_read=50000,
        ),
        source_cost_usd=Decimal("0.0123"),
        created_ms=6000,
    )
    report = usage_report(state_db, harness="my-app")

    assert result.rows_imported == 1
    assert report.by_harness[0].harness == "my-app"
    assert report.by_provider[0].provider_id == "openai"
    assert report.by_model[0].model_id == "gpt-5.5"
    assert report.totals.tokens.input == 12000
    assert report.totals.tokens.output == 800
    assert report.totals.tokens.cache_read == 50000


def test_record_usage_events_imports_normalized_rows(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)
    run = start_run(state_db, name="record-many", started_at_ms=0)
    event = UsageEvent(
        harness="my-app",
        source_session_id="job-7",
        source_row_id="row-1",
        source_message_id="req-8",
        source_dedup_key="dedup-8",
        global_dedup_key="my-app:job-7:dedup-8",
        fingerprint_hash="fp-8",
        provider_id="openai",
        model_id="gpt-5.5",
        thinking_level=None,
        agent=None,
        created_ms=7000,
        completed_ms=7001,
        tokens=TokenBreakdown(input=9, output=1),
        source_cost_usd=Decimal("0"),
        raw_json=None,
    )

    result = record_usage_events(
        state_db,
        [event],
        session_id=run.id,
        use_active_session=False,
    )
    report = session_report(state_db, run.id)

    assert result.run_id == run.id
    assert result.rows_imported == 1
    assert result.rows_linked == 1
    assert report.totals.tokens.total == 10
