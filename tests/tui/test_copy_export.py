from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from toktrail.api.config import init_config
from toktrail.api.imports import import_usage
from toktrail.api.sessions import init_state
from toktrail.tui.app import ToktrailTuiApp

from ..helpers import VALID_ASSISTANT, create_opencode_db, insert_message

pytestmark = pytest.mark.asyncio


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


async def test_export_current_view_writes_copyable_file(tmp_path, monkeypatch) -> None:
    app = _make_seeded_app(tmp_path)
    export_dir = tmp_path / "exports"
    monkeypatch.setattr(app, "_export_dir", lambda: export_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("Y")
        await pilot.pause()
        exported = export_dir / "toktrail-sessions.txt"
        assert exported.exists()
        text = exported.read_text(encoding="utf-8")
        assert "opencode" in text
        assert "tokens" in text.lower()
