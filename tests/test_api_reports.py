from __future__ import annotations

import os

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

    report = session_report(state_db)
    payload = report.as_dict()

    assert report.session is not None
    assert payload["totals"]["input"] == 1000
    assert payload["totals"]["source_cost_usd"] == 0.05
    assert payload["totals"]["message_count"] == 1


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


def test_usage_report_requires_session_id(tmp_path) -> None:
    with pytest.raises(InvalidAPIUsageError, match="requires session_id"):
        usage_report(tmp_path / "toktrail.db")
