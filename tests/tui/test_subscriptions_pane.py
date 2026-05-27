from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from toktrail.api.config import init_config
from toktrail.api.imports import import_usage
from toktrail.api.sessions import init_state
from toktrail.tui.app import ToktrailTuiApp

from ..cli.helpers import write_subscriptions_config
from ..helpers import VALID_ASSISTANT, create_opencode_db, insert_message

pytestmark = pytest.mark.asyncio


def _make_app(tmp_path, *, tui_mode: str = "auto") -> ToktrailTuiApp:
    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    init_state(db_path)
    init_config(config_path, template="copilot")
    return ToktrailTuiApp(
        db_path=db_path,
        config_path=config_path,
        prices_path=config_path.with_name("prices.toml"),
        prices_dir=config_path.with_name("prices"),
        subscriptions_path=config_path.with_name("subscriptions.toml"),
        refresh_on_start=False,
        tui_mode=tui_mode,
    )


def _make_seeded_app(tmp_path, *, tui_mode: str = "auto") -> ToktrailTuiApp:
    db_path = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    init_state(db_path)
    init_config(config_path, template="copilot")
    write_subscriptions_config(config_path.with_name("subscriptions.toml"))
    conn = create_opencode_db(source_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    assistant = deepcopy(VALID_ASSISTANT)
    time_block = assistant["time"]
    assert isinstance(time_block, dict)
    time_block["created"] = float(now_ms)
    assistant["providerID"] = "opencode-go"
    assistant["modelID"] = "opencode-go/deepseek-v4-pro"
    assistant["cost"] = 3.2
    assistant["tokens"] = {
        "input": 120,
        "output": 30,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }
    insert_message(conn, row_id="row-1", session_id="ses-1", data=assistant)
    conn.commit()
    conn.close()
    import_usage(db_path, "opencode", source_path=source_db, use_active_session=False)
    return ToktrailTuiApp(
        db_path=db_path,
        config_path=config_path,
        prices_path=config_path.with_name("prices.toml"),
        prices_dir=config_path.with_name("prices"),
        subscriptions_path=config_path.with_name("subscriptions.toml"),
        refresh_on_start=False,
        tui_mode=tui_mode,
    )


async def test_tui_subscriptions_empty_state(tmp_path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        assert app.query_one("#content").current == "subscriptions"
        assert (
            "No provider subscriptions configured."
            in app._subscriptions.get_export_text()
        )


async def test_tui_subscriptions_seeded_shows_plan_data(tmp_path) -> None:
    app = _make_seeded_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        export = app._subscriptions.get_export_text()
        assert "Subscriptions" in export
        assert "plan\tid\tproviders\tscope\tbasis" in export
        assert "OpenCode Go" in export
        assert "opencode-go" in export
        assert "all areas" in export
        assert "Billing" in export


async def test_subscriptions_full_mode_includes_scope_column(tmp_path) -> None:
    app = _make_seeded_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        table = app.query_one("#subscriptions-table")
        labels = [str(column.label) for column in table.ordered_columns]
    assert "Scope" in labels


async def test_subscriptions_compact_uses_reduced_columns(tmp_path) -> None:
    app = _make_seeded_app(tmp_path, tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        table = app.query_one("#subscriptions-table")
        labels = [str(column.label) for column in table.ordered_columns]
    assert labels == ["Plan", "Period", "Status", "Used", "Left", "Reset"]


async def test_subscriptions_micro_uses_reduced_columns(tmp_path) -> None:
    app = _make_seeded_app(tmp_path, tui_mode="micro")
    async with app.run_test(size=(60, 18)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        table = app.query_one("#subscriptions-table")
        labels = [str(column.label) for column in table.ordered_columns]
        assert labels == ["Plan", "Period", "Used", "Left"]


async def test_subscriptions_detail_shows_subscription_info(tmp_path) -> None:
    app = _make_seeded_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        detail = app.query_one("#subscriptions-detail")
        text = str(detail.render())
        assert "OpenCode Go" in text
        assert "opencode-go" in text
        assert "Quota basis" in text
        assert "Resets in:" in text


async def test_subscriptions_tab_click(tmp_path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#tab-subscriptions")
        await pilot.pause()
        assert app.query_one("#content").current == "subscriptions"
