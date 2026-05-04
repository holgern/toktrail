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
    usage_report,
    usage_series_report,
)
from toktrail.api.sessions import init_state, start_run
from toktrail.db import (
    connect,
    create_tracking_session,
    end_tracking_session,
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
) -> UsageEvent:
    return UsageEvent(
        harness="opencode",
        source_session_id="ses-1",
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
            "total": 1850,
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
    assert report.totals.tokens.total == 1850


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
provider = "anthropic"
timezone = "UTC"
cost_basis = "source"
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
    assert subscriptions[0]["provider_id"] == "anthropic"
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
