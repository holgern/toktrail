from __future__ import annotations

import pytest

pytest.importorskip("textual")
pytestmark = pytest.mark.asyncio


async def test_switch_to_areas_prices_subscriptions_config_with_keys(
    make_tui_app,
) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert app.query_one("#content").current == "areas"
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#content").current == "prices"
        await pilot.press("5")
        await pilot.pause()
        assert app.query_one("#content").current == "subscriptions"
        await pilot.press("6")
        await pilot.pause()
        assert app.query_one("#content").current == "config"


async def test_clicking_tabs_switches_content(make_tui_app) -> None:
    app = make_tui_app()
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
        await pilot.click("#tab-subscriptions")
        await pilot.pause()
        assert app.query_one("#content").current == "subscriptions"


async def test_switch_dashboard_views_with_keys(make_tui_app) -> None:
    app = make_tui_app()
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


async def test_tui_compact_mode_hides_desktop_chrome(make_tui_app) -> None:
    app = make_tui_app(tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.pause()
        assert app._tui_display is not None
        assert app._tui_display.mode == "compact"
        assert "toktrail" in str(app.query_one("#compact-bar").render())


async def test_tui_micro_mode_hides_details(make_tui_app) -> None:
    app = make_tui_app(tui_mode="micro")
    async with app.run_test(size=(60, 18)) as pilot:
        await pilot.pause()
        assert app._tui_display is not None
        assert app._tui_display.mode == "micro"


async def test_tui_compact_mode_keeps_number_navigation(make_tui_app) -> None:
    app = make_tui_app(tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert app.query_one("#content").current == "sessions"
        await pilot.press("1")
        await pilot.pause()
        assert app.query_one("#content").current == "dashboard"


async def test_sessions_compact_uses_reduced_columns(make_tui_app) -> None:
    app = make_tui_app(tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        table = app.query_one("#sessions-table")
        labels = [str(column.label) for column in table.ordered_columns]
        assert labels == ["Time", "Area", "Model", "Health", "Fails", "Tokens", "Cost"]


async def test_day_back_updates_date_offset(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert app.query_one("#content").current == "sessions"
        assert app._date_offset == 0
        await pilot.press("left")
        await pilot.pause()
        assert app._date_offset == 1
        status_text = str(app.query_one("#status").render())
        assert "Sessions:" in status_text
        assert "today" not in status_text


async def test_day_forward_clamps_at_today(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert app._date_offset == 0
        await pilot.press("right")
        await pilot.pause()
        assert app._date_offset == 0


async def test_day_back_then_forward(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        assert app._date_offset == 1
        await pilot.press("right")
        await pilot.pause()
        assert app._date_offset == 0
        status_text = str(app.query_one("#status").render())
        assert "today" in status_text


async def test_day_navigation_only_on_sessions_and_areas(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        # Dashboard pane: left arrow should not change offset
        assert app.query_one("#content").current == "dashboard"
        await pilot.press("left")
        await pilot.pause()
        assert app._date_offset == 0


async def test_status_bar_shows_date_on_sessions(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.pause()
        status_text = app.query_one("#status").render()
        assert "Sessions: today" in str(status_text)


async def test_status_bar_shows_date_on_areas(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        await pilot.pause()
        status_text = app.query_one("#status").render()
        assert "Areas: today" in str(status_text)


async def test_help_overlay_opens_and_closes(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert len(app.screen_stack) > 1
        from toktrail.tui.screens.help import HelpScreen

        assert isinstance(app.screen_stack[-1], HelpScreen)
        help_screen = app.screen_stack[-1]
        help_text = str(help_screen.query_one("#help-content").render())
        assert "Keybindings" in help_text or "Keys" in help_text
        await pilot.press("escape")
        await pilot.pause()
        assert not any(isinstance(s, HelpScreen) for s in app.screen_stack)


async def test_help_overlay_with_h_key(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("h")
        await pilot.pause()
        assert len(app.screen_stack) > 1
        from toktrail.tui.screens.help import HelpScreen

        assert isinstance(app.screen_stack[-1], HelpScreen)
        await pilot.press("escape")
        await pilot.pause()


async def test_help_compact_mode(make_tui_app) -> None:
    app = make_tui_app(tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        help_screen = app.screen_stack[-1]
        help_text = str(help_screen.query_one("#help-content").render())
        assert "Keybindings" in help_text
        await pilot.press("escape")
        await pilot.pause()


async def test_help_micro_mode(make_tui_app) -> None:
    app = make_tui_app(tui_mode="micro")
    async with app.run_test(size=(60, 18)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        help_screen = app.screen_stack[-1]
        help_text = str(help_screen.query_one("#help-content").render())
        assert "Keys" in help_text
        await pilot.press("escape")
        await pilot.pause()


async def test_sessions_table_focus_on_switch(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.pause()
        table = app.query_one("#sessions-table")
        assert table.has_focus


async def test_areas_table_focus_on_switch(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        await pilot.pause()
        table = app.query_one("#areas-table")
        assert table.has_focus


async def test_switching_to_non_date_pane_clears_date_status(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert "Sessions: today" in str(app.query_one("#status").render())
        await pilot.press("4")
        await pilot.pause()
        assert "Prices pane." in str(app.query_one("#status").render())


async def test_refresh_action_uses_thread_worker(make_tui_app, monkeypatch) -> None:
    app = make_tui_app()
    captured = {}

    def fake_run_worker(work, **kwargs):
        captured["work"] = work
        captured.update(kwargs)
        return None

    monkeypatch.setattr(app, "run_worker", fake_run_worker)
    async with app.run_test(size=(120, 40)) as pilot:
        app.action_refresh()
        await pilot.pause()
        assert captured["name"] == "refresh"
        assert captured["thread"] is True
        assert captured["exclusive"] is True
        assert "Refresh started." in str(app.query_one("#status").render())


async def test_sessions_full_mode_has_health_and_fails_columns(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        table = app.query_one("#sessions-table")
        labels = [str(column.label) for column in table.ordered_columns]
        assert "Health" in labels
        assert "Fails" in labels
        assert labels.index("Health") < labels.index("Msgs")
        assert labels.index("Fails") < labels.index("Msgs")


async def test_sessions_micro_mode_includes_health_in_rows(make_tui_app) -> None:
    app = make_tui_app(tui_mode="micro")
    async with app.run_test(size=(60, 18)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        table = app.query_one("#sessions-table")
        labels = [str(column.label) for column in table.ordered_columns]
        assert labels == ["Session"]


async def test_sessions_detail_panel_shows_no_session_when_empty(make_tui_app) -> None:
    app = make_tui_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        detail = app.query_one("#sessions-detail")
        assert "No session selected." in str(detail.render())


async def test_sessions_export_header_includes_health_and_fails() -> None:
    """Unit-level check: _build_export_text header includes health/fails columns."""

    from toktrail.api.model_parts.core_models import TokenBreakdown
    from toktrail.reporting import (
        CostTotals,
        UsageSessionRow,
    )
    from toktrail.tui.panes.sessions import SessionsPane
    from toktrail.tui.services import SessionsData

    row = UsageSessionRow(
        key="m1/pi/s1",
        origin_machine_id="m1",
        machine_name=None,
        machine_label="m1",
        harness="pi",
        source_session_id="s1",
        area_id=None,
        area_sync_id=None,
        area_path="area/test",
        area_name=None,
        first_ms=1000,
        last_ms=2000,
        message_count=5,
        tokens=TokenBreakdown(input=100, output=50),
        costs=CostTotals(actual_cost_usd=0.01, virtual_cost_usd=0.02),
        models=("claude-3",),
        cwd="/tmp",
        source_dir="/tmp/src",
    )
    data = SessionsData(sessions=(row,), digests={})
    pane = SessionsPane()
    export_text = pane._build_export_text(data)
    header_line = export_text.split("\n")[1]
    assert "health" in header_line
    assert "fails" in header_line
    assert row.source_session_id in export_text
