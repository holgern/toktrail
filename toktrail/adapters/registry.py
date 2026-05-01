from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from toktrail.adapters.amp import list_amp_sessions, scan_amp_path
from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.claude import list_claude_sessions, scan_claude_path
from toktrail.adapters.codex import list_codex_sessions, scan_codex_path
from toktrail.adapters.copilot import list_copilot_sessions, scan_copilot_path
from toktrail.adapters.droid import list_droid_sessions, scan_droid_path
from toktrail.adapters.goose import list_goose_sessions, scan_goose_sqlite
from toktrail.adapters.opencode import list_opencode_sessions, scan_opencode_sqlite
from toktrail.adapters.pi import list_pi_sessions, scan_pi_path
from toktrail.adapters.vibe import list_vibe_sessions, scan_vibe_path
from toktrail.paths import (
    COPILOT_FILE_ENV,
    COPILOT_OTEL_DIR_ENV,
    GOOSE_PATH_ROOT_ENV,
    TOKTRAIL_AMP_THREADS_ENV,
    TOKTRAIL_CLAUDE_PROJECTS_ENV,
    TOKTRAIL_CODEX_SESSIONS_ENV,
    TOKTRAIL_DROID_SESSIONS_ENV,
    TOKTRAIL_GOOSE_SESSIONS_ENV,
    TOKTRAIL_PI_SESSIONS_ENV,
    TOKTRAIL_VIBE_LOGS_ENV,
    resolve_amp_threads_path,
    resolve_claude_projects_path,
    resolve_codex_sessions_path,
    resolve_copilot_source_path,
    resolve_droid_sessions_path,
    resolve_goose_sessions_path,
    resolve_opencode_db_path,
    resolve_pi_sessions_path,
    resolve_vibe_logs_path,
)


@dataclass(frozen=True)
class EnvRoot:
    """Environment variable-based path discovery."""

    env_var: str
    suffix: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathTemplate:
    """Template for default path discovery."""

    parts: tuple[str, ...]


@dataclass(frozen=True)
class HarnessDefinition:
    """Definition of a harness for discovery and import."""

    name: str
    display_name: str
    default_roots: tuple[PathTemplate, ...]
    env_roots: tuple[EnvRoot, ...]
    patterns: tuple[str, ...]
    resolve_source_path: Callable[[Path | None], Path | None]
    scan: Callable[..., ScanResult]
    list_sessions: Callable[..., list[SourceSessionSummary]]
    ignored_patterns: tuple[str, ...] = ()
    source_kind: Literal["json", "jsonl", "sqlite", "directory", "mixed"] = "directory"
    supports_watch: bool = False
    supports_environment: bool = False


HARNESS_REGISTRY: dict[str, HarnessDefinition] = {
    "amp": HarnessDefinition(
        name="amp",
        display_name="Amp",
        default_roots=(PathTemplate((".local", "share", "amp", "threads")),),
        env_roots=(EnvRoot(TOKTRAIL_AMP_THREADS_ENV),),
        patterns=("*.json",),
        source_kind="json",
        resolve_source_path=resolve_amp_threads_path,
        scan=scan_amp_path,
        list_sessions=list_amp_sessions,
        supports_watch=True,
    ),
    "opencode": HarnessDefinition(
        name="opencode",
        display_name="OpenCode",
        default_roots=(PathTemplate((".local", "share", "opencode", "opencode.db")),),
        env_roots=(EnvRoot("XDG_DATA_HOME", ("opencode", "opencode.db")),),
        patterns=("*.db",),
        source_kind="sqlite",
        resolve_source_path=resolve_opencode_db_path,
        scan=scan_opencode_sqlite,
        list_sessions=list_opencode_sessions,
        supports_watch=True,
    ),
    "pi": HarnessDefinition(
        name="pi",
        display_name="Pi",
        default_roots=(PathTemplate((".pi", "agent", "sessions")),),
        env_roots=(EnvRoot(TOKTRAIL_PI_SESSIONS_ENV),),
        patterns=("*.jsonl", "*.json"),
        ignored_patterns=("*.settings.json",),
        source_kind="mixed",
        resolve_source_path=resolve_pi_sessions_path,
        scan=scan_pi_path,
        list_sessions=list_pi_sessions,
        supports_watch=True,
    ),
    "copilot": HarnessDefinition(
        name="copilot",
        display_name="Copilot",
        default_roots=(PathTemplate((".copilot", "otel")),),
        env_roots=(
            EnvRoot(COPILOT_FILE_ENV),
            EnvRoot(COPILOT_OTEL_DIR_ENV),
        ),
        patterns=("*.jsonl", "*.json"),
        source_kind="mixed",
        resolve_source_path=resolve_copilot_source_path,
        scan=scan_copilot_path,
        list_sessions=list_copilot_sessions,
        supports_watch=True,
        supports_environment=True,
    ),
    "codex": HarnessDefinition(
        name="codex",
        display_name="Codex",
        default_roots=(PathTemplate((".codex", "sessions")),),
        env_roots=(EnvRoot(TOKTRAIL_CODEX_SESSIONS_ENV),),
        patterns=("*.json", "*.jsonl"),
        source_kind="mixed",
        resolve_source_path=resolve_codex_sessions_path,
        scan=scan_codex_path,
        list_sessions=list_codex_sessions,
        supports_watch=True,
    ),
    "goose": HarnessDefinition(
        name="goose",
        display_name="Goose",
        default_roots=(
            PathTemplate((".local", "share", "goose", "sessions", "sessions.db")),
            PathTemplate(
                ("Library", "Application Support", "goose", "sessions", "sessions.db")
            ),
            PathTemplate(
                (".local", "share", "Block", "goose", "sessions", "sessions.db")
            ),
        ),
        env_roots=(
            EnvRoot(TOKTRAIL_GOOSE_SESSIONS_ENV),
            EnvRoot(GOOSE_PATH_ROOT_ENV, ("data", "sessions", "sessions.db")),
        ),
        patterns=("*.db",),
        source_kind="sqlite",
        resolve_source_path=resolve_goose_sessions_path,
        scan=scan_goose_sqlite,
        list_sessions=list_goose_sessions,
        supports_watch=True,
    ),
    "droid": HarnessDefinition(
        name="droid",
        display_name="Droid",
        default_roots=(PathTemplate((".factory", "sessions")),),
        env_roots=(EnvRoot(TOKTRAIL_DROID_SESSIONS_ENV),),
        patterns=("*.settings.json",),
        source_kind="json",
        resolve_source_path=resolve_droid_sessions_path,
        scan=scan_droid_path,
        list_sessions=list_droid_sessions,
        supports_watch=True,
    ),
    "claude": HarnessDefinition(
        name="claude",
        display_name="Claude Code",
        default_roots=(PathTemplate((".claude", "projects")),),
        env_roots=(EnvRoot(TOKTRAIL_CLAUDE_PROJECTS_ENV),),
        patterns=("*.jsonl", "*.json"),
        ignored_patterns=("*.meta.json",),
        source_kind="mixed",
        resolve_source_path=resolve_claude_projects_path,
        scan=scan_claude_path,
        list_sessions=list_claude_sessions,
        supports_watch=True,
    ),
    "vibe": HarnessDefinition(
        name="vibe",
        display_name="Vibe",
        default_roots=(PathTemplate((".vibe", "logs", "session")),),
        env_roots=(EnvRoot(TOKTRAIL_VIBE_LOGS_ENV),),
        patterns=("meta.json",),
        source_kind="directory",
        resolve_source_path=resolve_vibe_logs_path,
        scan=scan_vibe_path,
        list_sessions=list_vibe_sessions,
        supports_watch=True,
    ),
}


def get_harness(name: str) -> HarnessDefinition:
    try:
        return HARNESS_REGISTRY[name]
    except KeyError as exc:
        msg = f"Unknown harness: {name}"
        raise ValueError(msg) from exc
