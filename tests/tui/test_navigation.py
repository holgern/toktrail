from __future__ import annotations

import pytest

pytest.importorskip("textual")
from toktrail.api.config import init_config
from toktrail.api.sessions import init_state
from toktrail.tui.app import ToktrailTuiApp

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


async def test_switch_dashboard_views_with_keys(tmp_path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("d")
        await pilot.pause()
        assert app.query_one("#content").current == "dashboard"
        assert "Dashboard: Daily" in app._dashboard.get_export_text()
        assert "Daily usage" in app._dashboard.get_export_text()

        await pilot.press("w")
        await pilot.pause()
        assert app.query_one("#content").current == "dashboard"
        assert "Dashboard: Weekly" in app._dashboard.get_export_text()
        assert "Weekly usage" in app._dashboard.get_export_text()

        await pilot.press("t")
        await pilot.pause()
        assert app.query_one("#content").current == "dashboard"
        assert "Dashboard: Today" in app._dashboard.get_export_text()
        assert "Top providers" in app._dashboard.get_export_text()


async def test_tui_compact_mode_hides_desktop_chrome(tmp_path) -> None:
    app = _make_app(tmp_path, tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.pause()
        assert app._tui_display is not None
        assert app._tui_display.mode == "compact"
        assert "toktrail" in str(app.query_one("#compact-bar").render())


async def test_tui_micro_mode_hides_details(tmp_path) -> None:
    app = _make_app(tmp_path, tui_mode="micro")
    async with app.run_test(size=(60, 18)) as pilot:
        await pilot.pause()
        assert app._tui_display is not None
        assert app._tui_display.mode == "micro"


async def test_tui_compact_mode_keeps_number_navigation(tmp_path) -> None:
    app = _make_app(tmp_path, tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert app.query_one("#content").current == "sessions"
        await pilot.press("1")
        await pilot.pause()
        assert app.query_one("#content").current == "dashboard"


async def test_sessions_compact_uses_reduced_columns(tmp_path) -> None:
    app = _make_app(tmp_path, tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        table = app.query_one("#sessions-table")
        labels = [str(column.label) for column in table.ordered_columns]
        assert labels == ["Time", "Area", "Model", "Tokens", "Cost"]
