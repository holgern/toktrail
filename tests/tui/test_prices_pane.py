from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from toktrail.api.config import init_config
from toktrail.api.imports import import_usage
from toktrail.api.prices import list_unconfigured_models
from toktrail.api.sessions import init_state
from toktrail.tui.app import ToktrailTuiApp

from ..helpers import VALID_ASSISTANT, create_opencode_db, insert_message

pytestmark = pytest.mark.asyncio


def _future_assistant(*, provider: str, model: str) -> dict[str, object]:
    assistant = deepcopy(VALID_ASSISTANT)
    created_ms = float(int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000)
    time_block = assistant["time"]
    assert isinstance(time_block, dict)
    time_block["created"] = created_ms
    assistant["providerID"] = provider
    assistant["modelID"] = model
    return assistant


def _make_empty_app(tmp_path) -> ToktrailTuiApp:
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


def _make_seeded_app(tmp_path) -> ToktrailTuiApp:
    db_path = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    init_state(db_path)
    init_config(config_path, template="copilot")
    conn = create_opencode_db(source_db)
    insert_message(
        conn,
        row_id="row-1",
        session_id="ses-1",
        data=_future_assistant(provider="unknown-a", model="unknown-model-a"),
    )
    insert_message(
        conn,
        row_id="row-2",
        session_id="ses-2",
        data=_future_assistant(provider="unknown-b", model="unknown-model-b"),
    )
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
    )


async def test_upsert_price_from_unconfigured_seed(tmp_path) -> None:
    app = _make_seeded_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app._refresh_views()
        table = app.query_one("#prices-unconfigured-table")
        assert table.row_count >= 1
        selected_index = 1 if table.row_count > 1 else 0
        second = table.ordered_rows[selected_index]
        app._prices.on_data_table_row_highlighted(
            table.RowHighlighted(table, selected_index, second.key)
        )
        assert app._prices.selected_unconfigured is not None
        seed = app._prices.seed_manual_price_row()
        assert seed is not None
        saved_path = app.service.upsert_manual_price(
            seed.__class__(
                table=seed.table,
                provider=seed.provider,
                model=seed.model,
                input_usd_per_1m=1.25,
                output_usd_per_1m=10.0,
            )
        )
        app._refresh_views()
        await pilot.pause()
        assert saved_path.exists()
        remaining = list_unconfigured_models(
            app.state.db_path,
            period="today",
            config_path=app.state.config_path,
        )
        assert not any(
            row.provider_id == seed.provider and row.model_id == seed.model
            for row in remaining
        )


async def test_price_form_keeps_invalid_numbers_visible(tmp_path) -> None:
    from toktrail.api.models import PriceRow
    from toktrail.tui.screens.price_form import PriceFormScreen

    app = _make_seeded_app(tmp_path)
    seed = PriceRow(
        table="virtual",
        provider="unknown-a",
        model="unknown-model-a",
        input_usd_per_1m=0.0,
        output_usd_per_1m=0.0,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(PriceFormScreen(seed))
        await pilot.pause()
        app.screen.query_one("#input-price").value = "not-a-number"
        await pilot.click("#save")
        await pilot.pause()
        assert isinstance(app.screen, PriceFormScreen)
        assert "must be numbers" in str(app.screen.query_one("#price-error").render())


async def test_enter_on_prices_opens_price_form(tmp_path) -> None:
    from toktrail.tui.screens.price_form import PriceFormScreen

    app = _make_seeded_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#content").current == "prices"

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen_stack[-1], PriceFormScreen)


async def test_enter_on_prices_without_unconfigured_model_shows_status(
    tmp_path,
) -> None:
    app = _make_empty_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert "No unconfigured model selected." in str(
            app.query_one("#status").render()
        )
