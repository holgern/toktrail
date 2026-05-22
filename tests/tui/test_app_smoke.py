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


async def test_tui_opens_dashboard(tmp_path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.query_one("#dashboard") is not None
        assert app.query_one("#content").current == "dashboard"
        assert "Dashboard: Today" in app._dashboard.get_export_text()
        assert "Top providers" in app._dashboard.get_export_text()
