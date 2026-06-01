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


def _write_two_subscriptions_config(path) -> None:
    path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "UTC"
quota_cost_basis = "source"
fixed_cost_usd = 10
fixed_cost_period = "monthly"
fixed_cost_reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "monthly"
limit_usd = 200
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions]]
id = "opencode-pro"
usage_providers = ["opencode-pro"]
display_name = "OpenCode Pro"
timezone = "UTC"
quota_cost_basis = "source"
fixed_cost_usd = 25
fixed_cost_period = "monthly"
fixed_cost_reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "monthly"
limit_usd = 500
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )


def _make_seeded_app_with_two_subscriptions(
    tmp_path,
    *,
    tui_mode: str = "auto",
) -> ToktrailTuiApp:
    db_path = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    init_state(db_path)
    init_config(config_path, template="copilot")
    _write_two_subscriptions_config(config_path.with_name("subscriptions.toml"))

    conn = create_opencode_db(source_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    first = deepcopy(VALID_ASSISTANT)
    first_time = first["time"]
    assert isinstance(first_time, dict)
    first_time["created"] = float(now_ms)
    first["providerID"] = "opencode-go"
    first["modelID"] = "opencode-go/deepseek-v4-pro"
    first["cost"] = 3.2
    first["tokens"] = {
        "input": 120,
        "output": 30,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }

    second = deepcopy(VALID_ASSISTANT)
    second_time = second["time"]
    assert isinstance(second_time, dict)
    second_time["created"] = float(now_ms)
    second["providerID"] = "opencode-pro"
    second["modelID"] = "opencode-pro/claude-sonnet"
    second["cost"] = 1.1
    second["tokens"] = {
        "input": 90,
        "output": 20,
        "reasoning": 0,
        "cache": {"read": 0, "write": 0},
    }

    insert_message(conn, row_id="row-1", session_id="ses-1", data=first)
    insert_message(conn, row_id="row-2", session_id="ses-2", data=second)
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
        assert "No provider subscriptions configured." in (
            app._subscriptions.get_export_text()
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


async def test_subscriptions_full_mode_is_subscription_list(tmp_path) -> None:
    app = _make_seeded_app(tmp_path, tui_mode="full")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()

        table = app.query_one("#subscriptions-table")
        labels = [str(column.label) for column in table.ordered_columns]

        assert labels == [
            "Plan",
            "Providers",
            "Scope",
            "Basis",
            "Used",
            "Limit",
            "Left",
            "Reset",
            "Windows",
        ]
        assert table.row_count == 1


async def test_subscriptions_compact_uses_reduced_columns(tmp_path) -> None:
    app = _make_seeded_app(tmp_path, tui_mode="compact")
    async with app.run_test(size=(72, 22)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        table = app.query_one("#subscriptions-table")
        labels = [str(column.label) for column in table.ordered_columns]
    assert labels == ["Plan", "Used", "Left", "Reset"]


async def test_subscriptions_micro_uses_reduced_columns(tmp_path) -> None:
    app = _make_seeded_app(tmp_path, tui_mode="micro")
    async with app.run_test(size=(60, 18)) as pilot:
        await pilot.press("5")
        await pilot.pause()
        table = app.query_one("#subscriptions-table")
        labels = [str(column.label) for column in table.ordered_columns]
        assert labels == ["Plan", "Used", "Left"]


async def test_enter_opens_subscription_detail_and_escape_returns(tmp_path) -> None:
    from toktrail.tui.screens.subscription_detail import SubscriptionDetailScreen

    app = _make_seeded_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen_stack[-1], SubscriptionDetailScreen)
        detail_text = str(
            app.screen_stack[-1].query_one("#subscription-detail-content").render()
        )
        assert "Subscription: OpenCode Go" in detail_text
        assert "Quota windows" in detail_text
        assert "Billing" in detail_text

        await pilot.press("escape")
        await pilot.pause()

        assert not any(
            isinstance(screen, SubscriptionDetailScreen) for screen in app.screen_stack
        )
        assert app.query_one("#content").current == "subscriptions"


async def test_subscriptions_supports_j_k_navigation(tmp_path) -> None:
    app = _make_seeded_app_with_two_subscriptions(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        await pilot.pause()

        assert app._subscriptions.selected_subscription is not None
        first = app._subscriptions.selected_subscription.subscription_id

        await pilot.press("j")
        await pilot.pause()
        assert app._subscriptions.selected_subscription is not None
        second = app._subscriptions.selected_subscription.subscription_id
        assert second != first

        await pilot.press("k")
        await pilot.pause()
        assert app._subscriptions.selected_subscription is not None
        assert app._subscriptions.selected_subscription.subscription_id == first


async def test_subscriptions_tab_click(tmp_path) -> None:
    app = _make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#tab-subscriptions")
        await pilot.pause()
        assert app.query_one("#content").current == "subscriptions"
