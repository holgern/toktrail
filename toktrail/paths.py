from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

TOKTRAIL_DB_ENV = "TOKTRAIL_DB"
TOKTRAIL_CONFIG_ENV = "TOKTRAIL_CONFIG"
COPILOT_FILE_ENV = "TOKTRAIL_COPILOT_FILE"
COPILOT_OTEL_FILE_EXPORTER_PATH_ENV = "COPILOT_OTEL_FILE_EXPORTER_PATH"
COPILOT_OTEL_DIR_ENV = "TOKTRAIL_COPILOT_OTEL_DIR"
TOKTRAIL_PI_SESSIONS_ENV = "TOKTRAIL_PI_SESSIONS"
TOKTRAIL_AMP_THREADS_ENV = "TOKTRAIL_AMP_THREADS"
TOKTRAIL_CODEX_SESSIONS_ENV = "TOKTRAIL_CODEX_SESSIONS"
TOKTRAIL_GOOSE_SESSIONS_ENV = "TOKTRAIL_GOOSE_SESSIONS"
TOKTRAIL_DROID_SESSIONS_ENV = "TOKTRAIL_DROID_SESSIONS"
GOOSE_PATH_ROOT_ENV = "GOOSE_PATH_ROOT"


def default_toktrail_db_path() -> Path:
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "toktrail" / "toktrail.db"
    return Path.home() / ".local" / "state" / "toktrail" / "toktrail.db"


def resolve_toktrail_db_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        path = cli_value.expanduser()
    elif os.environ.get(TOKTRAIL_DB_ENV):
        path = Path(os.environ[TOKTRAIL_DB_ENV]).expanduser()
    else:
        path = default_toktrail_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def default_toktrail_config_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "toktrail" / "config.toml"
    return Path.home() / ".config" / "toktrail" / "config.toml"


def resolve_toktrail_config_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_CONFIG_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_toktrail_config_path()


def default_opencode_db_path() -> Path:
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


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
    return Path.home() / ".copilot" / "otel"


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
    return Path.home() / ".pi" / "agent" / "sessions"


def resolve_pi_sessions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_PI_SESSIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_pi_sessions_path()


def default_amp_threads_path() -> Path:
    return Path.home() / ".local" / "share" / "amp" / "threads"


def resolve_amp_threads_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_AMP_THREADS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_amp_threads_path()


def default_codex_sessions_path() -> Path:
    return Path.home() / ".codex" / "sessions"


def resolve_codex_sessions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_CODEX_SESSIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_codex_sessions_path()


def default_goose_sessions_db_path() -> Path:
    return Path.home() / ".local" / "share" / "goose" / "sessions" / "sessions.db"


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
            Path.home()
            / "Library"
            / "Application Support"
            / "goose"
            / "sessions"
            / "sessions.db",
            Path.home()
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
    return Path.home() / ".factory" / "sessions"


def resolve_droid_sessions_path(cli_value: Path | None = None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(TOKTRAIL_DROID_SESSIONS_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return default_droid_sessions_path()
