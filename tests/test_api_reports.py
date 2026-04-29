from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from tests.helpers import (
    VALID_ASSISTANT,
    create_copilot_file,
    create_opencode_db,
    insert_message,
)
from toktrail.api.config import init_config
from toktrail.api.imports import import_usage
from toktrail.api.reports import session_report, usage_report
from toktrail.api.sessions import init_state, start_session
from toktrail.errors import InvalidAPIUsageError, NoActiveSessionError


def test_session_report_uses_active_session_by_default(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    insert_message(conn, row_id="row-1", session_id="ses-1", data=VALID_ASSISTANT)
    conn.commit()
    conn.close()
    init_state(state_db)
    start_session(state_db, name="report")
    import_usage(state_db, "opencode", source_path=source_db)

    report = session_report(state_db, config_path=tmp_path / "missing-config.toml")
    payload = report.as_dict()

    assert report.session is not None
    assert payload["totals"]["input"] == 1000
    assert payload["totals"]["source_cost_usd"] == 0.05
    assert payload["totals"]["message_count"] == 1
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
            "total": 1850,
        }
    ]


def test_session_report_without_active_session_raises(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    init_state(state_db)

    with pytest.raises(NoActiveSessionError, match="active tracking session"):
        session_report(state_db)


def test_session_report_applies_config_and_uses_state_db_only(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)
    init_config(config_path, template="copilot")
    init_state(state_db)
    session = start_session(state_db, name="copilot")
    import_usage(state_db, "copilot", session_id=session.id, source_path=copilot_file)
    os.remove(copilot_file)

    report = session_report(state_db, session.id, config_path=config_path)

    assert report.totals.costs.virtual_cost_usd > 0.0
    assert report.totals.costs.savings_usd == report.totals.costs.virtual_cost_usd


def test_usage_report_supports_periods_without_tracking_session(
    tmp_path, monkeypatch
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


def test_usage_report_rejects_period_and_since_until_together(tmp_path) -> None:
    with pytest.raises(InvalidAPIUsageError, match="either period or since/until"):
        usage_report(
            tmp_path / "toktrail.db",
            period="today",
            since_ms=int(
                datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000
            ),
        )


def test_session_report_supports_thinking_filter_and_collapse(tmp_path) -> None:
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    conn = create_opencode_db(source_db)
    high = deepcopy(VALID_ASSISTANT)
    high["thinkingLevel"] = "high"
    insert_message(conn, row_id="row-1", session_id="ses-1", data=high)
    low = deepcopy(VALID_ASSISTANT)
    low["id"] = "msg-low"
    low["thinkingLevel"] = "low"
    insert_message(conn, row_id="row-2", session_id="ses-1", data=low)
    conn.commit()
    conn.close()

    init_state(state_db)
    session = start_session(state_db, name="thinking")
    import_usage(state_db, "opencode", session_id=session.id, source_path=source_db)

    filtered = session_report(state_db, session.id, thinking_level="high")
    collapsed = session_report(state_db, session.id, split_thinking=False)

    assert [(row.model_id, row.thinking_level) for row in filtered.by_model] == [
        ("claude-sonnet-4", "high")
    ]
    assert [
        (row.model_id, row.thinking_level, row.message_count)
        for row in collapsed.by_model
    ] == [("claude-sonnet-4", None, 2)]
