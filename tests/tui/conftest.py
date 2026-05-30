from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("textual")

from toktrail.api.config import init_config
from toktrail.api.sessions import init_state
from toktrail.tui.app import ToktrailTuiApp


@pytest.fixture
def make_tui_app(tmp_path: Path) -> Callable[..., ToktrailTuiApp]:
    def _make_app(
        *,
        tui_mode: str = "auto",
        refresh_on_start: bool = False,
    ) -> ToktrailTuiApp:
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
            refresh_on_start=refresh_on_start,
            tui_mode=tui_mode,
        )

    return _make_app
