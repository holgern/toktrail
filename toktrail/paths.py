from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

TOKTRAIL_DB_ENV = "TOKTRAIL_DB"
TOKTRAIL_CONFIG_ENV = "TOKTRAIL_CONFIG"
TOKTRAIL_PRICES_ENV = "TOKTRAIL_PRICES"
TOKTRAIL_PRICES_DIR_ENV = "TOKTRAIL_PRICES_DIR"
TOKTRAIL_SUBSCRIPTIONS_ENV = "TOKTRAIL_SUBSCRIPTIONS"
COPILOT_FILE_ENV = "TOKTRAIL_COPILOT_FILE"
COPILOT_OTEL_FILE_EXPORTER_PATH_ENV = "COPILOT_OTEL_FILE_EXPORTER_PATH"
COPILOT_OTEL_DIR_ENV = "TOKTRAIL_COPILOT_OTEL_DIR"
TOKTRAIL_PI_SESSIONS_ENV = "TOKTRAIL_PI_SESSIONS"
TOKTRAIL_AMP_THREADS_ENV = "TOKTRAIL_AMP_THREADS"
TOKTRAIL_CODEX_SESSIONS_ENV = "TOKTRAIL_CODEX_SESSIONS"
TOKTRAIL_GOOSE_SESSIONS_ENV = "TOKTRAIL_GOOSE_SESSIONS"
TOKTRAIL_DROID_SESSIONS_ENV = "TOKTRAIL_DROID_SESSIONS"
TOKTRAIL_HARNESSBRIDGE_SESSIONS_ENV = "TOKTRAIL_HARNESSBRIDGE_SESSIONS"
TOKTRAIL_VIBE_LOGS_ENV = "TOKTRAIL_VIBE_LOGS"
GOOSE_PATH_ROOT_ENV = "GOOSE_PATH_ROOT"
TOKTRAIL_CLAUDE_PROJECTS_ENV = "TOKTRAIL_CLAUDE_PROJECTS"
_SEPARATOR_RE = re.compile(r"[/_\s]+")
_INVALID_IDENTITY_CHARS_RE = re.compile(r"[^a-z0-9.-]+")
_DASH_RE = re.compile(r"-+")


def _home_dir() -> Path:
    home = os.environ.get("HOME")
    if home:
        return Path(home).expanduser()
    return Path.home()


def default_toktrail_db_path() -> Path:
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "toktrail" / "toktrail.db"
    return _home_dir() / ".local" / "state" / "toktrail" / "toktrail.db"


def resolve_toktrail_db_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        path = cli_value.expanduser()
    elif os.environ.get(TOKTRAIL_DB_ENV):
        path = Path(os.environ[TOKTRAIL_DB_ENV]).expanduser()
    else:
        path = default_toktrail_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def default_toktrail_config_dir() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "toktrail"
    return _home_dir() / ".config" / "toktrail"


def default_toktrail_config_path() -> Path:
    return default_toktrail_config_dir() / "config.toml"


def default_toktrail_prices_path() -> Path:
    return default_toktrail_config_dir() / "prices.toml"


def default_toktrail_prices_dir() -> Path:
    return default_toktrail_config_dir() / "prices"


def default_provider_prices_path(provider: str) -> Path:
    filename = f"{_normalize_provider_filename(provider)}.toml"
    return default_toktrail_prices_dir() / filename


def default_toktrail_subscriptions_path() -> Path:
    return default_toktrail_config_dir() / "subscriptions.toml"


def resolve_toktrail_config_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_CONFIG_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_toktrail_config_path()


def resolve_toktrail_prices_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_PRICES_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_toktrail_prices_path()


def resolve_toktrail_prices_dir(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_PRICES_DIR_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_toktrail_prices_dir()


def resolve_provider_prices_path(
    provider: str,
    cli_dir_value: Path | None = None,
) -> Path:
    prices_dir = resolve_toktrail_prices_dir(cli_dir_value)
    return prices_dir / f"{_normalize_provider_filename(provider)}.toml"


def resolve_toktrail_subscriptions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_SUBSCRIPTIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_toktrail_subscriptions_path()


def default_opencode_db_path() -> Path:
    return _home_dir() / ".local" / "share" / "opencode" / "opencode.db"


def resolve_opencode_db_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        candidate = Path(xdg_data_home).expanduser() / "opencode" / "opencode.db"
        if candidate.exists():
            return candidate
    return default_opencode_db_path()


def default_copilot_otel_dir() -> Path:
    return _home_dir() / ".copilot" / "otel"


def new_copilot_otel_file_path(now: datetime | None = None) -> Path:
    timestamp = now or datetime.now()
    return default_copilot_otel_dir() / f"copilot-otel-{timestamp:%Y%m%d-%H%M%S}.jsonl"


def latest_copilot_otel_file(directory: Path | None = None) -> Path | None:
    root = (directory or default_copilot_otel_dir()).expanduser()
    if not root.exists() or not root.is_dir():
        return None
    candidates = sorted(
        root.glob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def resolve_copilot_source_path(cli_value: Path | None = None) -> Path | None:
    if cli_value is not None:
        return cli_value.expanduser()

    for env_name in (COPILOT_FILE_ENV, COPILOT_OTEL_FILE_EXPORTER_PATH_ENV):
        env_value = os.environ.get(env_name)
        if env_value:
            return Path(env_value).expanduser()

    env_dir = os.environ.get(COPILOT_OTEL_DIR_ENV)
    if env_dir:
        return Path(env_dir).expanduser()

    latest = latest_copilot_otel_file()
    if latest is not None:
        return latest

    return default_copilot_otel_dir()


def resolve_copilot_file_path(cli_value: Path | None = None) -> Path | None:
    return resolve_copilot_source_path(cli_value)


def default_pi_sessions_path() -> Path:
    return _home_dir() / ".pi" / "agent" / "sessions"


def resolve_pi_sessions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_PI_SESSIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_pi_sessions_path()


def default_amp_threads_path() -> Path:
    return _home_dir() / ".local" / "share" / "amp" / "threads"


def resolve_amp_threads_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_AMP_THREADS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_amp_threads_path()


def default_codex_sessions_path() -> Path:
    return _home_dir() / ".codex" / "sessions"


def resolve_codex_sessions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_CODEX_SESSIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_codex_sessions_path()


def default_goose_sessions_db_path() -> Path:
    return _home_dir() / ".local" / "share" / "goose" / "sessions" / "sessions.db"


def goose_sessions_db_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    goose_root = os.environ.get(GOOSE_PATH_ROOT_ENV)
    if goose_root:
        candidates.append(
            Path(goose_root).expanduser() / "data" / "sessions" / "sessions.db"
        )

    candidates.extend(
        [
            default_goose_sessions_db_path(),
            _home_dir()
            / "Library"
            / "Application Support"
            / "goose"
            / "sessions"
            / "sessions.db",
            _home_dir()
            / ".local"
            / "share"
            / "Block"
            / "goose"
            / "sessions"
            / "sessions.db",
        ]
    )
    return tuple(candidates)


def resolve_goose_sessions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_GOOSE_SESSIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    for candidate in goose_sessions_db_candidates():
        if candidate.exists():
            return candidate
    return default_goose_sessions_db_path()


def default_droid_sessions_path() -> Path:
    return _home_dir() / ".factory" / "sessions"


def resolve_droid_sessions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_DROID_SESSIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_droid_sessions_path()


def default_harnessbridge_sessions_path() -> Path:
    return _home_dir() / ".harnessbridge" / "sessions"


def resolve_harnessbridge_sessions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_HARNESSBRIDGE_SESSIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_harnessbridge_sessions_path()


def default_claude_projects_path() -> Path:
    return _home_dir() / ".claude" / "projects"


def resolve_claude_projects_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_CLAUDE_PROJECTS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_claude_projects_path()


def default_vibe_logs_path() -> Path:
    return _home_dir() / ".vibe" / "logs" / "session"


def resolve_vibe_logs_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_VIBE_LOGS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_vibe_logs_path()


def _normalize_provider_filename(provider: str) -> str:
    normalized = provider.strip().lower()
    normalized = _SEPARATOR_RE.sub("-", normalized)
    normalized = _INVALID_IDENTITY_CHARS_RE.sub("", normalized)
    normalized = _DASH_RE.sub("-", normalized).strip("-")
    if not normalized:
        msg = "Provider name is empty after normalization."
        raise ValueError(msg)
    return normalized
