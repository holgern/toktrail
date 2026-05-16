from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from tests.helpers import (
    VALID_ASSISTANT,
    create_copilot_file,
    create_opencode_db,
    insert_message,
)
from toktrail.api.config import init_config
from toktrail.api.imports import import_usage
from toktrail.api.reports import (
    session_report,
    subscription_usage_report,
    usage_areas_report,
    usage_report,
    usage_runs_report,
    usage_series_report,
    usage_sessions_report,
)
from toktrail.api.sessions import init_state, start_run
from toktrail.db import (
    assign_area_to_source_session,
    connect,
    create_tracking_session,
    end_tracking_session,
    ensure_area,
    get_local_machine_id,
    insert_usage_events,
    migrate,
)
from toktrail.errors import InvalidAPIUsageError, NoActiveRunError
from toktrail.models import TokenBreakdown, UsageEvent


@pytest.fixture(autouse=True)
def _isolate_toktrail_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(config_path))


def make_api_usage_event(
    dedup_suffix: str,
    *,
    created_ms: int,
    tokens: TokenBreakdown,
    source_session_id: str = "ses-1",
) -> UsageEvent:
    return UsageEvent(
        harness="opencode",
        source_session_id=source_session_id,
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"dedup-{dedup_suffix}",
        global_dedup_key=f"global-{dedup_suffix}",
        fingerprint_hash=f"fp-{dedup_suffix}",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
        thinking_level=None,
        agent="build",
        created_ms=created_ms,
        completed_ms=created_ms + 1,
        tokens=tokens,
        source_cost_usd=Decimal("0"),
        raw_json=None,
    )


def _future_opencode_assistant() -> dict[str, object]:
    assistant = deepcopy(VALID_ASSISTANT)
    created_ms = float(int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000)
    time_block = cast(dict[str, object], assistant["time"])
    time_block["created"] = created_ms
    return assistant


def test_session_report_uses_active_session_by_default(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    assistant = _future_opencode_assistant()
    insert_message(conn, row_id="row-1", session_id="ses-1", data=assistant)
    conn.commit()
    conn.close()
    init_state(state_db)
    start_run(state_db, name="report")
    import_usage(state_db, "opencode", source_path=source_db)

    report = session_report(state_db, config_path=tmp_path / "missing-config.toml")
    payload = report.as_dict()

    assert report.session is not None
    totals_payload = cast(dict[str, object], payload["totals"])
    assert totals_payload["input"] == 1000
    assert totals_payload["source_cost_usd"] == "0.05"
    assert totals_payload["message_count"] == 1
    assert payload["unconfigured_models"] == [
        {
            "required": ["virtual"],
            "harness": "opencode",
            "provider_id": "anthropic",
            "model_id": "claude-sonnet-4",
            "thinking_level": None,
            "message_count": 1,
            "input": 1000,
            "output": 500,
            "reasoning": 100,
            "cache_read": 200,
            "cache_write": 50,
            "cache_output": 0,
            "total": 1500,
            "prompt_total": 1250,
            "output_total": 500,
            "accounting_total": 1850,
        }
    ]


def test_session_report_without_active_session_raises(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)

    with pytest.raises(NoActiveRunError, match="active run"):
        session_report(state_db)


def test_session_report_applies_config_and_uses_state_db_only(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)
    init_config(config_path, template="copilot")
    init_state(state_db)
    db_conn = connect(state_db)
    try:
        migrate(db_conn)
        session_id = create_tracking_session(db_conn, "copilot", started_at_ms=0)
    finally:
        db_conn.close()
    import_usage(state_db, "copilot", session_id=session_id, source_path=copilot_file)
    os.remove(copilot_file)

    report = session_report(state_db, session_id, config_path=config_path)

    assert report.totals.costs.virtual_cost_usd > 0.0
    assert report.totals.costs.savings_usd == report.totals.costs.virtual_cost_usd


def test_usage_report_supports_periods_without_tracking_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(conn, row_id="row-1", session_id="ses-1", data=VALID_ASSISTANT)
    conn.commit()
    conn.close()
    init_state(state_db)
    import_usage(state_db, "opencode", source_path=source_db)
    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2023, 11, 14, 23, 0, tzinfo=tz),
    )

    report = usage_report(state_db, period="today", timezone="UTC")

    assert report.session is None
    assert report.filters["period"] == "today"
    assert report.filters["timezone"] == "UTC"
    assert report.totals.tokens.total == 1500


def test_usage_report_preserves_cache_output_in_public_tokens(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_api_usage_event(
                    "cache-output",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=10, output=5, cache_output=7),
                )
            ],
        )
    finally:
        conn.close()

    report = usage_report(state_db)

    assert report.totals.tokens.cache_output == 7
    assert report.by_harness[0].tokens.cache_output == 7


def test_usage_series_report_daily_json_shape(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(conn, row_id="row-1", session_id="ses-1", data=VALID_ASSISTANT)
    conn.commit()
    conn.close()
    init_state(state_db)
    import_usage(state_db, "opencode", source_path=source_db)

    report = usage_series_report(
        state_db,
        granularity="daily",
        timezone="UTC",
        breakdown=True,
        config_path=tmp_path / "missing-config.toml",
    )
    payload = report.as_dict()

    assert payload["type"] == "usage_series"
    assert payload["granularity"] == "daily"
    assert payload["timezone"] == "UTC"
    buckets = cast(list[dict[str, object]], payload["buckets"])
    by_model = cast(list[dict[str, object]], buckets[0]["by_model"])
    assert by_model[0]["model_id"] == "claude-sonnet-4"


def test_usage_series_report_rejects_invalid_granularity(tmp_path: Path) -> None:
    with pytest.raises(InvalidAPIUsageError, match="Invalid granularity"):
        usage_series_report(tmp_path / "toktrail.db", granularity="hourly")


def test_usage_report_rejects_period_and_since_until_together(tmp_path: Path) -> None:
    with pytest.raises(InvalidAPIUsageError, match="either period or since/until"):
        usage_report(
            tmp_path / "toktrail.db",
            period="today",
            since_ms=int(
                datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000
            ),
        )


def test_session_report_supports_thinking_filter_and_collapse(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    high = _future_opencode_assistant()
    high["thinkingLevel"] = "high"
    insert_message(conn, row_id="row-1", session_id="ses-1", data=high)
    low = _future_opencode_assistant()
    low["id"] = "msg-low"
    low["thinkingLevel"] = "low"
    insert_message(conn, row_id="row-2", session_id="ses-1", data=low)
    conn.commit()
    conn.close()

    init_state(state_db)
    session = start_run(state_db, name="thinking")
    import_usage(state_db, "opencode", session_id=session.id, source_path=source_db)

    filtered_split = session_report(
        state_db, session.id, thinking_level="high", split_thinking=True
    )
    split_all = session_report(state_db, session.id, split_thinking=True)
    collapsed = session_report(state_db, session.id)

    assert [(row.model_id, row.thinking_level) for row in filtered_split.by_model] == [
        ("claude-sonnet-4", "high")
    ]
    assert sorted(
        [
            (row.model_id, row.thinking_level, row.message_count)
            for row in split_all.by_model
        ],
        key=lambda x: x[1] or "",
    ) == [
        ("claude-sonnet-4", "high", 1),
        ("claude-sonnet-4", "low", 1),
    ]
    assert [
        (row.model_id, row.thinking_level, row.message_count)
        for row in collapsed.by_model
    ] == [("claude-sonnet-4", None, 2)]


def test_usage_report_exposes_provider_summary(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(conn, row_id="row-1", session_id="ses-1", data=VALID_ASSISTANT)
    conn.commit()
    conn.close()
    init_state(state_db)
    import_usage(state_db, "opencode", source_path=source_db)

    report = usage_report(state_db)

    assert report.by_provider
    assert report.by_provider[0].provider_id == "anthropic"


def test_usage_report_exposes_machine_summary(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_api_usage_event(
                    "machine-summary",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=10, output=4),
                )
            ],
        )
    finally:
        conn.close()

    report = usage_report(state_db)

    assert len(report.by_machine) == 1
    assert report.by_machine[0].machine_id is not None
    assert report.by_machine[0].message_count == 1


def test_usage_report_accepts_area_filter(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        area = ensure_area(conn, "privat/toktrail")
        local_machine_id = get_local_machine_id(conn)
        event = make_api_usage_event(
            "area-filter",
            created_ms=1_000,
            tokens=TokenBreakdown(input=10, output=4),
            source_session_id="area-session",
        )
        insert_usage_events(conn, None, [event])
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=local_machine_id,
            harness="opencode",
            source_session_id="area-session",
        )
        conn.commit()
    finally:
        conn.close()

    report = usage_report(state_db, area="privat")

    assert report.totals.tokens.total == event.tokens.total
    assert report.filters["area"] == "privat"


def test_subscription_usage_report_returns_public_dataclasses(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "config.toml"
    conn = create_opencode_db(source_db)
    insert_message(conn, row_id="row-1", session_id="ses-1", data=VALID_ASSISTANT)
    conn.commit()
    conn.close()
    init_state(state_db)
    import_usage(state_db, "opencode", source_path=source_db)
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
timezone = "UTC"
quota_cost_basis = "source"
fixed_cost_usd = 10
fixed_cost_period = "monthly"
fixed_cost_reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "5h"
limit_usd = 1
reset_mode = "first_use"
reset_at = "2023-11-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    report = subscription_usage_report(
        state_db,
        config_path=config_path,
        now_ms=1700000000000,
    )

    payload = report.as_dict()
    assert "generated_at_ms" in payload
    subscriptions = cast(list[dict[str, object]], payload["subscriptions"])
    periods = cast(list[dict[str, object]], subscriptions[0]["periods"])
    assert subscriptions[0]["subscription_id"] == "anthropic-pro-plan"
    assert subscriptions[0]["usage_provider_ids"] == ["anthropic"]
    assert subscriptions[0]["quota_cost_basis"] == "source"
    assert periods[0]["period"] == "5h"
    assert periods[0]["status"] in {"waiting_for_first_use", "active"}
    assert periods[0]["reset_mode"] == "first_use"
    assert periods[0]["reset_at"] == "2023-11-01T00:00:00+00:00"
    assert "since_ms" in periods[0]
    assert "until_ms" in periods[0]
    assert "billing" in subscriptions[0]


def test_session_report_bounds_to_run_lifetime(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        session_id = create_tracking_session(conn, "bounded", started_at_ms=1_000)
        insert_usage_events(
            conn,
            session_id,
            [
                make_api_usage_event(
                    "before",
                    created_ms=999,
                    tokens=TokenBreakdown(input=100),
                ),
                make_api_usage_event(
                    "during",
                    created_ms=1_100,
                    tokens=TokenBreakdown(input=7, output=3),
                ),
                make_api_usage_event(
                    "after",
                    created_ms=1_600,
                    tokens=TokenBreakdown(output=10),
                ),
            ],
        )
        end_tracking_session(conn, session_id, ended_at_ms=1_500)
    finally:
        conn.close()

    report = session_report(state_db, session_id)

    assert report.totals.tokens.input == 7
    assert report.totals.tokens.output == 3
    assert report.filters["since_ms"] == 1_000
    assert report.filters["until_ms"] == 1_500


def test_usage_report_session_id_bounds_to_run_lifetime(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        session_id = create_tracking_session(conn, "bounded", started_at_ms=1_000)
        insert_usage_events(
            conn,
            session_id,
            [
                make_api_usage_event(
                    "before",
                    created_ms=999,
                    tokens=TokenBreakdown(input=100),
                ),
                make_api_usage_event(
                    "during",
                    created_ms=1_100,
                    tokens=TokenBreakdown(output=4),
                ),
            ],
        )
        end_tracking_session(conn, session_id, ended_at_ms=1_500)
    finally:
        conn.close()

    report = usage_report(state_db, session_id=session_id, since_ms=0, until_ms=9_999)

    assert report.totals.tokens.total == 4
    assert report.filters["since_ms"] == 1_000
    assert report.filters["until_ms"] == 1_500


def test_usage_series_report_session_id_bounds_to_run_lifetime(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        session_id = create_tracking_session(conn, "bounded", started_at_ms=1_000)
        insert_usage_events(
            conn,
            session_id,
            [
                make_api_usage_event(
                    "before",
                    created_ms=999,
                    tokens=TokenBreakdown(input=100),
                ),
                make_api_usage_event(
                    "during",
                    created_ms=1_100,
                    tokens=TokenBreakdown(input=5, output=2),
                ),
            ],
        )
        end_tracking_session(conn, session_id, ended_at_ms=1_500)
    finally:
        conn.close()

    report = usage_series_report(
        state_db,
        granularity="daily",
        session_id=session_id,
    )

    assert report.totals.tokens.total == 7
    assert report.filters["since_ms"] == 1_000
    assert report.filters["until_ms"] == 1_500


def test_usage_series_report_accepts_area_filter(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        area = ensure_area(conn, "privat/toktrail")
        local_machine_id = get_local_machine_id(conn)
        event = make_api_usage_event(
            "series-area",
            created_ms=1_000,
            tokens=TokenBreakdown(input=12, output=3),
            source_session_id="series-session",
        )
        insert_usage_events(conn, None, [event])
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=local_machine_id,
            harness="opencode",
            source_session_id="series-session",
        )
        conn.commit()
    finally:
        conn.close()

    report = usage_series_report(state_db, granularity="daily", area="privat")

    assert report.totals.tokens.total == event.tokens.total
    assert report.filters["area"] == "privat"


def test_usage_sessions_report_json_shape(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(conn, row_id="row-1", session_id="ses-1", data=VALID_ASSISTANT)
    conn.commit()
    conn.close()
    init_state(state_db)
    import_usage(state_db, "opencode", source_path=source_db)

    report = usage_sessions_report(
        state_db,
        limit=10,
        breakdown=True,
        config_path=tmp_path / "missing-config.toml",
    )
    payload = report.as_dict()

    assert payload["type"] == "usage_sessions"
    assert payload["order"] == "desc"
    sessions = cast(list[dict[str, object]], payload["sessions"])
    assert len(sessions) >= 1
    session = sessions[0]
    assert "key" in session
    assert "origin_machine_id" in session
    assert "machine_name" in session
    assert "machine_label" in session
    assert "harness" in session
    assert "source_session_id" in session
    assert "area_id" in session
    assert "area_sync_id" in session
    assert "area_path" in session
    assert "area_name" in session
    assert "source_paths" in session
    assert "cwd" in session
    assert "source_dir" in session
    assert "git_root" in session
    assert "git_remote" in session
    assert "session_title" in session
    assert "tokens" in session
    assert "costs" in session
    # costs serialized as strings
    costs = cast(dict[str, object], session["costs"])
    assert isinstance(costs["actual_cost_usd"], str)
    assert isinstance(costs["source_cost_usd"], str)
    # no raw JSON leakage
    assert "raw_json" not in session
    # by_model present when breakdown=True
    by_model = cast(list[dict[str, object]], session["by_model"])
    assert len(by_model) >= 1


def test_usage_sessions_report_session_id_bounds_to_run_lifetime(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        session_id = create_tracking_session(
            conn, "bounded-sessions", started_at_ms=1_000
        )
        insert_usage_events(
            conn,
            session_id,
            [
                make_api_usage_event(
                    "before",
                    created_ms=999,
                    tokens=TokenBreakdown(input=100),
                ),
                make_api_usage_event(
                    "during",
                    created_ms=1_100,
                    tokens=TokenBreakdown(input=5, output=2),
                ),
            ],
        )
        end_tracking_session(conn, session_id, ended_at_ms=1_500)
    finally:
        conn.close()

    report = usage_sessions_report(
        state_db,
        session_id=session_id,
        limit=None,
    )

    assert report.totals.tokens.total == 7
    assert report.filters["since_ms"] == 1_000
    assert report.filters["until_ms"] == 1_500


def test_usage_sessions_report_supports_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_api_usage_event(
                    "today",
                    created_ms=int(
                        datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
                make_api_usage_event(
                    "yesterday",
                    created_ms=int(
                        datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
            ],
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2026, 5, 11, 12, 0, tzinfo=tz),
    )
    report = usage_sessions_report(
        state_db,
        period="today",
        timezone="UTC",
        limit=None,
    )

    assert len(report.sessions) == 1
    assert report.filters["period"] == "today"
    assert report.filters["timezone"] == "UTC"
    assert report.filters["since_ms"] is not None
    assert report.filters["until_ms"] is not None


def test_usage_sessions_report_accepts_area_filter(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        area = ensure_area(conn, "privat/toktrail")
        local_machine_id = get_local_machine_id(conn)
        event = make_api_usage_event(
            "sessions-area",
            created_ms=1_000,
            tokens=TokenBreakdown(input=8, output=2),
            source_session_id="sessions-area",
        )
        insert_usage_events(conn, None, [event])
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=local_machine_id,
            harness="opencode",
            source_session_id="sessions-area",
        )
        conn.commit()
    finally:
        conn.close()

    report = usage_sessions_report(state_db, area="privat", limit=None)

    assert len(report.sessions) == 1
    assert report.sessions[0].area_path == "privat/toktrail"
    assert report.filters["area"] == "privat"


def test_usage_sessions_report_rejects_period_with_since_until(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)
    with pytest.raises(InvalidAPIUsageError):
        usage_sessions_report(state_db, period="today", since_ms=1)


def test_usage_runs_report_filters_machine_id(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    machine_a = "aa11bb22cc33dd44"
    machine_b = "ee55ff6677889900"
    conn = connect(state_db)
    try:
        migrate(conn)
        run_a = create_tracking_session(conn, "run-a", started_at_ms=1_000)
        insert_usage_events(
            conn,
            run_a,
            [
                make_api_usage_event(
                    "run-a-event",
                    created_ms=1_100,
                    tokens=TokenBreakdown(input=5, output=1),
                )
            ],
            origin_machine_id=machine_a,
        )
        end_tracking_session(conn, run_a, ended_at_ms=1_500)
        run_b = create_tracking_session(conn, "run-b", started_at_ms=2_000)
        insert_usage_events(
            conn,
            run_b,
            [
                make_api_usage_event(
                    "run-b-event",
                    created_ms=2_100,
                    tokens=TokenBreakdown(input=9, output=2),
                )
            ],
            origin_machine_id=machine_b,
        )
        conn.execute(
            "UPDATE runs SET origin_machine_id = ? WHERE id = ?",
            (machine_a, run_a),
        )
        conn.execute(
            "UPDATE runs SET origin_machine_id = ? WHERE id = ?",
            (machine_b, run_b),
        )
        conn.commit()
    finally:
        conn.close()

    report = usage_runs_report(
        state_db,
        machine_id=machine_a,
        limit=None,
    )

    assert len(report.runs) == 1
    assert report.runs[0].name == "run-a"
    assert report.runs[0].origin_machine_id == machine_a


def test_usage_areas_report_json_shape(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        area = ensure_area(conn, "privat/toktrail")
        local_machine_id = get_local_machine_id(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_api_usage_event(
                    "usage-areas",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=14, output=5),
                    source_session_id="usage-areas",
                )
            ],
        )
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=local_machine_id,
            harness="opencode",
            source_session_id="usage-areas",
        )
        conn.commit()
    finally:
        conn.close()

    report = usage_areas_report(state_db)
    payload = report.as_dict()

    assert payload["type"] == "usage_areas"
    areas = cast(list[dict[str, object]], payload["areas"])
    assert all("area_sync_id" in area_row for area_row in areas)
    assert any(area_row["path"] == "privat" for area_row in areas)
    assert any(area_row["path"] == "privat/toktrail" for area_row in areas)
