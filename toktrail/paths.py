from __future__ import annotations

import os
from pathlib import Path

TOKTRAIL_DB_ENV = "TOKTRAIL_DB"
COPILOT_FILE_ENV = "TOKTRAIL_COPILOT_FILE"


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


def resolve_copilot_file_path(cli_value: Path | None = None) -> Path | None:
    if cli_value is not None:
        return cli_value.expanduser()
    env_value = os.environ.get(COPILOT_FILE_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return None
