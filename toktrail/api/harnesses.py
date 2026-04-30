from __future__ import annotations

from toktrail.api._common import _normalize_harness_name
from toktrail.api.models import HarnessDefinition
from toktrail.errors import UnsupportedHarnessError
from toktrail.paths import (
    COPILOT_FILE_ENV,
    COPILOT_OTEL_DIR_ENV,
    COPILOT_OTEL_FILE_EXPORTER_PATH_ENV,
    GOOSE_PATH_ROOT_ENV,
    TOKTRAIL_AMP_THREADS_ENV,
    TOKTRAIL_CLAUDE_PROJECTS_ENV,
    TOKTRAIL_CODEX_SESSIONS_ENV,
    TOKTRAIL_DROID_SESSIONS_ENV,
    TOKTRAIL_GOOSE_SESSIONS_ENV,
    TOKTRAIL_PI_SESSIONS_ENV,
    default_amp_threads_path,
    default_claude_projects_path,
    default_codex_sessions_path,
    default_copilot_otel_dir,
    default_droid_sessions_path,
    default_goose_sessions_db_path,
    default_opencode_db_path,
    default_pi_sessions_path,
)

_HARNESSES: tuple[HarnessDefinition, ...] = (
    HarnessDefinition(
        name="amp",
        display_name="Amp",
        supports_watch=True,
        supports_environment=False,
        default_source_path=default_amp_threads_path(),
        source_path_env_vars=(TOKTRAIL_AMP_THREADS_ENV,),
        source_path_kind="path",
    ),
    HarnessDefinition(
        name="opencode",
        display_name="OpenCode",
        supports_watch=True,
        supports_environment=False,
        default_source_path=default_opencode_db_path(),
        source_path_env_vars=("XDG_DATA_HOME",),
        source_path_kind="file",
    ),
    HarnessDefinition(
        name="pi",
        display_name="Pi",
        supports_watch=True,
        supports_environment=False,
        default_source_path=default_pi_sessions_path(),
        source_path_env_vars=(TOKTRAIL_PI_SESSIONS_ENV,),
        source_path_kind="path",
    ),
    HarnessDefinition(
        name="copilot",
        display_name="Copilot",
        supports_watch=True,
        supports_environment=True,
        default_source_path=default_copilot_otel_dir(),
        source_path_env_vars=(
            COPILOT_FILE_ENV,
            COPILOT_OTEL_FILE_EXPORTER_PATH_ENV,
            COPILOT_OTEL_DIR_ENV,
        ),
        source_path_kind="path",
    ),
    HarnessDefinition(
        name="codex",
        display_name="Codex",
        supports_watch=True,
        supports_environment=False,
        default_source_path=default_codex_sessions_path(),
        source_path_env_vars=(TOKTRAIL_CODEX_SESSIONS_ENV,),
        source_path_kind="path",
    ),
    HarnessDefinition(
        name="goose",
        display_name="Goose",
        supports_watch=False,
        supports_environment=False,
        default_source_path=default_goose_sessions_db_path(),
        source_path_env_vars=(TOKTRAIL_GOOSE_SESSIONS_ENV, GOOSE_PATH_ROOT_ENV),
        source_path_kind="file",
    ),
    HarnessDefinition(
        name="droid",
        display_name="Droid",
        supports_watch=False,
        supports_environment=False,
        default_source_path=default_droid_sessions_path(),
        source_path_env_vars=(TOKTRAIL_DROID_SESSIONS_ENV,),
        source_path_kind="path",
    ),
    HarnessDefinition(
        name="claude",
        display_name="Claude Code",
        supports_watch=True,
        supports_environment=False,
        default_source_path=default_claude_projects_path(),
        source_path_env_vars=(TOKTRAIL_CLAUDE_PROJECTS_ENV,),
        source_path_kind="path",
    ),
)

_HARNESS_BY_NAME = {definition.name: definition for definition in _HARNESSES}


def supported_harnesses() -> tuple[HarnessDefinition, ...]:
    return _HARNESSES


def get_harness_definition(harness: str) -> HarnessDefinition:
    normalized = normalize_harness_name(harness)
    try:
        return _HARNESS_BY_NAME[normalized]
    except KeyError as exc:
        msg = f"Unsupported harness: {harness}"
        raise UnsupportedHarnessError(msg) from exc


def normalize_harness_name(harness: str) -> str:
    return _normalize_harness_name(harness)


def is_supported_harness(harness: str) -> bool:
    return normalize_harness_name(harness) in _HARNESS_BY_NAME


__all__ = [
    "get_harness_definition",
    "is_supported_harness",
    "normalize_harness_name",
    "supported_harnesses",
]
