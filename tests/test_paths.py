from __future__ import annotations

import os
from datetime import datetime

from toktrail.paths import (
    default_codex_sessions_path,
    default_toktrail_config_path,
    new_copilot_otel_file_path,
    resolve_codex_sessions_path,
    resolve_copilot_file_path,
    resolve_copilot_source_path,
    resolve_toktrail_config_path,
)


def test_resolve_copilot_source_path_prefers_exporter_path(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "otel.jsonl"
    monkeypatch.delenv("TOKTRAIL_COPILOT_FILE", raising=False)
    monkeypatch.setenv("COPILOT_OTEL_FILE_EXPORTER_PATH", str(path))

    assert resolve_copilot_source_path(None) == path
    assert resolve_copilot_file_path(None) == path


def test_resolve_copilot_source_path_uses_latest_default_otel_jsonl(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOKTRAIL_COPILOT_FILE", raising=False)
    monkeypatch.delenv("COPILOT_OTEL_FILE_EXPORTER_PATH", raising=False)
    monkeypatch.delenv("TOKTRAIL_COPILOT_OTEL_DIR", raising=False)

    older = tmp_path / ".copilot" / "otel" / "copilot-otel-20260101-000000.jsonl"
    newer = tmp_path / ".copilot" / "otel" / "copilot-otel-20260101-000100.jsonl"
    older.parent.mkdir(parents=True, exist_ok=True)
    older.write_text("", encoding="utf-8")
    newer.write_text("", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    assert resolve_copilot_source_path(None) == newer


def test_new_copilot_otel_file_path_uses_default_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    path = new_copilot_otel_file_path(datetime(2026, 1, 1, 12, 0, 0))

    assert path == tmp_path / ".copilot" / "otel" / "copilot-otel-20260101-120000.jsonl"


def test_default_toktrail_config_path_uses_xdg_config_home(
    monkeypatch, tmp_path
) -> None:
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    assert default_toktrail_config_path() == config_home / "toktrail" / "config.toml"


def test_resolve_toktrail_config_path_prefers_cli_over_env(
    monkeypatch, tmp_path
) -> None:
    env_path = tmp_path / "env-config.toml"
    cli_path = tmp_path / "cli-config.toml"
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(env_path))

    assert resolve_toktrail_config_path(cli_path) == cli_path
    assert resolve_toktrail_config_path(None) == env_path


def test_default_codex_sessions_path_uses_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_codex_sessions_path() == tmp_path / ".codex" / "sessions"


def test_resolve_codex_sessions_path_prefers_env(monkeypatch, tmp_path) -> None:
    path = tmp_path / "codex-sessions"
    monkeypatch.setenv("TOKTRAIL_CODEX_SESSIONS", str(path))

    assert resolve_codex_sessions_path(None) == path


def test_resolve_codex_sessions_path_prefers_cli(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env"
    cli_path = tmp_path / "cli"
    monkeypatch.setenv("TOKTRAIL_CODEX_SESSIONS", str(env_path))

    assert resolve_codex_sessions_path(cli_path) == cli_path
