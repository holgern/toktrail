from __future__ import annotations

import pytest

pytest.importorskip("textual")
from toktrail.api.config import init_config
from toktrail.api.sessions import init_state
from toktrail.tui.app import ToktrailTuiApp

pytestmark = pytest.mark.asyncio


def _make_app(tmp_path) -> ToktrailTuiApp:
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
    )


async def test_switch_to_areas_and_prices_with_keys(tmp_path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert app.query_one("#content").current == "areas"
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#content").current == "prices"


async def test_clicking_tabs_switches_content(tmp_path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#tab-sessions")
        await pilot.pause()
        assert app.query_one("#content").current == "sessions"
        await pilot.click("#tab-areas")
        await pilot.pause()
        assert app.query_one("#content").current == "areas"
        await pilot.click("#tab-prices")
        await pilot.pause()
        assert app.query_one("#content").current == "prices"
