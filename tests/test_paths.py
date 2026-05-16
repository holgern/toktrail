from __future__ import annotations

import os
from datetime import datetime

from toktrail.paths import (
    default_amp_threads_path,
    default_code_sessions_path,
    default_codex_sessions_path,
    default_droid_sessions_path,
    default_goose_sessions_db_path,
    default_harnessbridge_sessions_path,
    default_provider_prices_path,
    default_toktrail_config_path,
    default_toktrail_machine_path,
    default_toktrail_prices_dir,
    default_toktrail_prices_path,
    default_toktrail_subscriptions_path,
    new_copilot_otel_file_path,
    resolve_amp_threads_path,
    resolve_code_sessions_path,
    resolve_codex_sessions_path,
    resolve_copilot_file_path,
    resolve_copilot_source_path,
    resolve_droid_sessions_path,
    resolve_goose_sessions_path,
    resolve_harnessbridge_sessions_path,
    resolve_toktrail_config_path,
    resolve_toktrail_machine_path,
    resolve_toktrail_prices_dir,
    resolve_toktrail_prices_path,
    resolve_toktrail_subscriptions_path,
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


def test_default_toktrail_machine_path_uses_xdg_config_home(
    monkeypatch, tmp_path
) -> None:
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    assert default_toktrail_machine_path() == config_home / "toktrail" / "machine.toml"


def test_resolve_toktrail_machine_path_prefers_cli_over_env(
    monkeypatch, tmp_path
) -> None:
    env_path = tmp_path / "env-machine.toml"
    cli_path = tmp_path / "cli-machine.toml"
    monkeypatch.setenv("TOKTRAIL_MACHINE_CONFIG", str(env_path))

    assert resolve_toktrail_machine_path(cli_path) == cli_path
    assert resolve_toktrail_machine_path(None) == env_path


def test_default_toktrail_prices_path_uses_xdg_config_home(
    monkeypatch, tmp_path
) -> None:
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    assert default_toktrail_prices_path() == config_home / "toktrail" / "prices.toml"


def test_default_toktrail_prices_dir_uses_xdg_config_home(
    monkeypatch, tmp_path
) -> None:
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    assert default_toktrail_prices_dir() == config_home / "toktrail" / "prices"


def test_resolve_toktrail_prices_path_prefers_cli(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env-prices.toml"
    cli_path = tmp_path / "cli-prices.toml"
    monkeypatch.setenv("TOKTRAIL_PRICES", str(env_path))

    assert resolve_toktrail_prices_path(cli_path) == cli_path


def test_resolve_toktrail_prices_path_prefers_env(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env-prices.toml"
    monkeypatch.setenv("TOKTRAIL_PRICES", str(env_path))

    assert resolve_toktrail_prices_path(None) == env_path


def test_resolve_toktrail_prices_dir_prefers_cli(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env-prices"
    cli_path = tmp_path / "cli-prices"
    monkeypatch.setenv("TOKTRAIL_PRICES_DIR", str(env_path))

    assert resolve_toktrail_prices_dir(cli_path) == cli_path


def test_resolve_toktrail_prices_dir_prefers_env(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env-prices"
    monkeypatch.setenv("TOKTRAIL_PRICES_DIR", str(env_path))

    assert resolve_toktrail_prices_dir(None) == env_path


def test_default_provider_prices_path_normalizes_provider(
    monkeypatch,
    tmp_path,
) -> None:
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    assert default_provider_prices_path("OpenAI") == (
        config_home / "toktrail" / "prices" / "openai.toml"
    )
    assert default_provider_prices_path("opencode go") == (
        config_home / "toktrail" / "prices" / "opencode-go.toml"
    )


def test_default_toktrail_subscriptions_path_uses_xdg_config_home(
    monkeypatch, tmp_path
) -> None:
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    assert default_toktrail_subscriptions_path() == (
        config_home / "toktrail" / "subscriptions.toml"
    )


def test_resolve_toktrail_subscriptions_path_prefers_cli(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env-subscriptions.toml"
    cli_path = tmp_path / "cli-subscriptions.toml"
    monkeypatch.setenv("TOKTRAIL_SUBSCRIPTIONS", str(env_path))

    assert resolve_toktrail_subscriptions_path(cli_path) == cli_path


def test_resolve_toktrail_subscriptions_path_prefers_env(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env-subscriptions.toml"
    monkeypatch.setenv("TOKTRAIL_SUBSCRIPTIONS", str(env_path))

    assert resolve_toktrail_subscriptions_path(None) == env_path


def test_default_codex_sessions_path_uses_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_codex_sessions_path() == tmp_path / ".codex" / "sessions"


def test_default_code_sessions_path_uses_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODE_HOME", raising=False)

    assert default_code_sessions_path() == tmp_path / ".code" / "sessions"


def test_default_code_sessions_path_uses_code_home(monkeypatch, tmp_path) -> None:
    code_home = tmp_path / "custom-code-home"
    monkeypatch.setenv("CODE_HOME", str(code_home))

    assert default_code_sessions_path() == code_home / "sessions"


def test_default_amp_threads_path_uses_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_amp_threads_path() == (
        tmp_path / ".local" / "share" / "amp" / "threads"
    )


def test_resolve_amp_threads_path_prefers_env(monkeypatch, tmp_path) -> None:
    path = tmp_path / "amp-threads"
    monkeypatch.setenv("TOKTRAIL_AMP_THREADS", str(path))

    assert resolve_amp_threads_path(None) == path


def test_resolve_amp_threads_path_prefers_cli(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env"
    cli_path = tmp_path / "cli"
    monkeypatch.setenv("TOKTRAIL_AMP_THREADS", str(env_path))

    assert resolve_amp_threads_path(cli_path) == cli_path


def test_resolve_codex_sessions_path_prefers_env(monkeypatch, tmp_path) -> None:
    path = tmp_path / "codex-sessions"
    monkeypatch.setenv("TOKTRAIL_CODEX_SESSIONS", str(path))

    assert resolve_codex_sessions_path(None) == path


def test_resolve_codex_sessions_path_prefers_cli(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env"
    cli_path = tmp_path / "cli"
    monkeypatch.setenv("TOKTRAIL_CODEX_SESSIONS", str(env_path))

    assert resolve_codex_sessions_path(cli_path) == cli_path


def test_resolve_code_sessions_path_prefers_toktrail_env(monkeypatch, tmp_path) -> None:
    source = tmp_path / "code-sessions"
    code_home = tmp_path / "code-home"
    monkeypatch.setenv("TOKTRAIL_CODE_SESSIONS", str(source))
    monkeypatch.setenv("CODE_HOME", str(code_home))

    assert resolve_code_sessions_path(None) == source


def test_resolve_code_sessions_path_prefers_cli(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env"
    cli_path = tmp_path / "cli"
    monkeypatch.setenv("TOKTRAIL_CODE_SESSIONS", str(env_path))

    assert resolve_code_sessions_path(cli_path) == cli_path


def test_resolve_goose_sessions_path_prefers_cli(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env-sessions.db"
    cli_path = tmp_path / "cli-sessions.db"
    monkeypatch.setenv("TOKTRAIL_GOOSE_SESSIONS", str(env_path))

    assert resolve_goose_sessions_path(cli_path) == cli_path


def test_resolve_goose_sessions_path_prefers_env(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env-sessions.db"
    monkeypatch.setenv("TOKTRAIL_GOOSE_SESSIONS", str(env_path))

    assert resolve_goose_sessions_path(None) == env_path


def test_resolve_goose_sessions_path_uses_goose_root_candidate(
    monkeypatch,
    tmp_path,
) -> None:
    goose_root = tmp_path / "goose-root"
    sessions_db = goose_root / "data" / "sessions" / "sessions.db"
    sessions_db.parent.mkdir(parents=True)
    sessions_db.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TOKTRAIL_GOOSE_SESSIONS", raising=False)
    monkeypatch.setenv("GOOSE_PATH_ROOT", str(goose_root))

    assert resolve_goose_sessions_path(None) == sessions_db


def test_resolve_goose_sessions_path_falls_back_to_linux_default(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TOKTRAIL_GOOSE_SESSIONS", raising=False)
    monkeypatch.delenv("GOOSE_PATH_ROOT", raising=False)

    assert resolve_goose_sessions_path(None) == default_goose_sessions_db_path()


def test_default_droid_sessions_path_uses_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_droid_sessions_path() == tmp_path / ".factory" / "sessions"


def test_default_harnessbridge_sessions_path_uses_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_harnessbridge_sessions_path() == (
        tmp_path / ".harnessbridge" / "sessions"
    )


def test_resolve_harnessbridge_sessions_path_prefers_env(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "harnessbridge-sessions"
    monkeypatch.setenv("TOKTRAIL_HARNESSBRIDGE_SESSIONS", str(source))

    assert resolve_harnessbridge_sessions_path(None) == source


def test_resolve_harnessbridge_sessions_path_prefers_cli(
    monkeypatch,
    tmp_path,
) -> None:
    env_source = tmp_path / "env"
    cli_source = tmp_path / "cli"
    monkeypatch.setenv("TOKTRAIL_HARNESSBRIDGE_SESSIONS", str(env_source))

    assert resolve_harnessbridge_sessions_path(cli_source) == cli_source


def test_resolve_droid_sessions_path_prefers_env(monkeypatch, tmp_path) -> None:
    source = tmp_path / "factory-sessions"
    monkeypatch.setenv("TOKTRAIL_DROID_SESSIONS", str(source))

    assert resolve_droid_sessions_path(None) == source


def test_resolve_droid_sessions_path_prefers_cli(monkeypatch, tmp_path) -> None:
    env_source = tmp_path / "env"
    cli_source = tmp_path / "cli"
    monkeypatch.setenv("TOKTRAIL_DROID_SESSIONS", str(env_source))

    assert resolve_droid_sessions_path(cli_source) == cli_source
