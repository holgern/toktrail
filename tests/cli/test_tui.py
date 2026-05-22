from __future__ import annotations

import builtins

import pytest
from typer.testing import CliRunner

from toktrail.api.config import init_config
from toktrail.api.sessions import init_state
from toktrail.cli import app


def test_tui_help_does_not_require_textual_runtime() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["tui", "--help"])
    assert result.exit_code == 0, result.output
    assert "Open interactive terminal UI." in result.output


def test_tui_missing_dependency_shows_install_extra_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "toktrail.tui.app":
            raise ImportError("textual not installed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(app, ["tui"])
    assert result.exit_code == 1
    assert "installing toktrail[tui]" in result.output


def test_tui_command_constructs_app_when_textual_is_available(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("textual")
    from toktrail.tui.app import ToktrailTuiApp

    runner = CliRunner()
    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    init_state(db_path)
    init_config(config_path, template="copilot")
    called: dict[str, bool] = {}

    def fake_run(self) -> None:  # type: ignore[no-untyped-def]
        called["run"] = True

    monkeypatch.setattr(ToktrailTuiApp, "run", fake_run)
    result = runner.invoke(
        app,
        ["--db", str(db_path), "--config", str(config_path), "tui", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert called["run"] is True


def test_tui_mode_option_overrides_env(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("textual")
    from toktrail.tui.app import ToktrailTuiApp

    runner = CliRunner()
    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    init_state(db_path)
    init_config(config_path, template="copilot")
    captured: dict[str, object] = {}

    real_init = ToktrailTuiApp.__init__

    def fake_init(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        captured["tui_mode"] = kwargs.get("tui_mode")
        real_init(self, *args, **kwargs)

    def fake_run(self) -> None:  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setenv("TOKTRAIL_TUI_MODE", "micro")
    monkeypatch.setattr(ToktrailTuiApp, "__init__", fake_init)
    monkeypatch.setattr(ToktrailTuiApp, "run", fake_run)
    result = runner.invoke(
        app,
        [
            "--db",
            str(db_path),
            "--config",
            str(config_path),
            "tui",
            "--no-refresh",
            "--tui-mode",
            "compact",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["tui_mode"] == "compact"


def test_tui_mode_validation_error() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["tui", "--tui-mode", "invalid"])
    assert result.exit_code == 1
    assert "TUI mode must be one of: auto, full, compact, micro" in result.output
