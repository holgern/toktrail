from __future__ import annotations

from pathlib import Path

from toktrail.api.harnesses import get_harness_definition
from toktrail.errors import StateDatabaseError
from toktrail.paths import (
    default_codex_sessions_path as _default_codex_sessions_path,
)
from toktrail.paths import (
    default_goose_sessions_db_path as _default_goose_sessions_db_path,
)
from toktrail.paths import (
    default_toktrail_config_path as _default_toktrail_config_path,
)
from toktrail.paths import (
    default_toktrail_db_path as _default_toktrail_db_path,
)
from toktrail.paths import (
    resolve_codex_sessions_path,
    resolve_copilot_source_path,
    resolve_goose_sessions_path,
    resolve_opencode_db_path,
    resolve_pi_sessions_path,
)
from toktrail.paths import (
    resolve_toktrail_config_path as _resolve_toktrail_config_path,
)
from toktrail.paths import (
    resolve_toktrail_db_path as _resolve_toktrail_db_path,
)


def default_toktrail_db_path() -> Path:
    return _default_toktrail_db_path()


def resolve_toktrail_db_path(db_path: Path | None = None) -> Path:
    try:
        return _resolve_toktrail_db_path(db_path)
    except OSError as exc:
        resolved = db_path if db_path is not None else _default_toktrail_db_path()
        msg = f"Could not prepare toktrail state database path {resolved}: {exc}"
        raise StateDatabaseError(msg) from exc


def default_toktrail_config_path() -> Path:
    return _default_toktrail_config_path()


def resolve_toktrail_config_path(config_path: Path | None = None) -> Path:
    return _resolve_toktrail_config_path(config_path)


def default_source_path(harness: str) -> Path | None:
    return get_harness_definition(harness).default_source_path


def default_codex_sessions_path() -> Path:
    return _default_codex_sessions_path()


def default_goose_sessions_db_path() -> Path:
    return _default_goose_sessions_db_path()


def resolve_source_path(
    harness: str,
    source_path: Path | None = None,
) -> Path | None:
    normalized = get_harness_definition(harness).name
    if normalized == "opencode":
        return resolve_opencode_db_path(source_path)
    if normalized == "pi":
        return resolve_pi_sessions_path(source_path)
    if normalized == "copilot":
        return resolve_copilot_source_path(source_path)
    if normalized == "codex":
        return resolve_codex_sessions_path(source_path)
    if normalized == "goose":
        return resolve_goose_sessions_path(source_path)
    msg = f"Unsupported harness: {harness}"
    raise StateDatabaseError(msg)


__all__ = [
    "default_codex_sessions_path",
    "default_goose_sessions_db_path",
    "default_source_path",
    "default_toktrail_config_path",
    "default_toktrail_db_path",
    "resolve_source_path",
    "resolve_toktrail_config_path",
    "resolve_toktrail_db_path",
]
