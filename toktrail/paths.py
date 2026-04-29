from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

TOKTRAIL_DB_ENV = "TOKTRAIL_DB"
COPILOT_FILE_ENV = "TOKTRAIL_COPILOT_FILE"
COPILOT_OTEL_FILE_EXPORTER_PATH_ENV = "COPILOT_OTEL_FILE_EXPORTER_PATH"
COPILOT_OTEL_DIR_ENV = "TOKTRAIL_COPILOT_OTEL_DIR"
TOKTRAIL_PI_SESSIONS_ENV = "TOKTRAIL_PI_SESSIONS"


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
