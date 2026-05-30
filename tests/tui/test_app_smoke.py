from __future__ import annotations

from collections.abc import Callable

import pytest

pytest.importorskip("textual")
from toktrail.tui.app import ToktrailTuiApp

pytestmark = pytest.mark.asyncio


async def test_tui_opens_dashboard(
    make_tui_app: Callable[..., ToktrailTuiApp],
) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.query_one("#dashboard") is not None
        assert app.query_one("#content").current == "dashboard"
        assert "Dashboard: Today" in app._dashboard.get_export_text()
        assert "Top providers" in app._dashboard.get_export_text()
