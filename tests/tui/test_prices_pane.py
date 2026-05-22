from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from tests.helpers import VALID_ASSISTANT, create_opencode_db, insert_message
from toktrail.api.config import init_config
from toktrail.api.imports import import_usage
from toktrail.api.prices import list_unconfigured_models
from toktrail.api.sessions import init_state
from toktrail.tui.app import ToktrailTuiApp


def _future_assistant() -> dict[str, object]:
    assistant = deepcopy(VALID_ASSISTANT)
    created_ms = float(int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000)
    time_block = assistant["time"]
    assert isinstance(time_block, dict)
    time_block["created"] = created_ms
    assistant["providerID"] = "openai-codex"
    assistant["modelID"] = "gpt-5.3-codex"
    return assistant


def _make_seeded_app(tmp_path) -> ToktrailTuiApp:
    db_path = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    init_state(db_path)
    init_config(config_path, template="copilot")
    conn = create_opencode_db(source_db)
    insert_message(conn, row_id="row-1", session_id="ses-1", data=_future_assistant())
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
