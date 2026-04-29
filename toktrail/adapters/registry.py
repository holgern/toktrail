from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from toktrail.adapters.base import ScanResult, SourceSessionSummary
from toktrail.adapters.codex import list_codex_sessions, scan_codex_path
from toktrail.adapters.copilot import list_copilot_sessions, scan_copilot_path
from toktrail.adapters.goose import list_goose_sessions, scan_goose_sqlite
from toktrail.adapters.opencode import list_opencode_sessions, scan_opencode_sqlite
from toktrail.adapters.pi import list_pi_sessions, scan_pi_path
from toktrail.paths import (
    resolve_codex_sessions_path,
    resolve_copilot_source_path,
    resolve_goose_sessions_path,
    resolve_opencode_db_path,
    resolve_pi_sessions_path,
)


@dataclass(frozen=True)
class HarnessDefinition:
    name: str
    display_name: str
    resolve_source_path: Callable[[Path | None], Path | None]
    scan: Callable[..., ScanResult]
    list_sessions: Callable[..., list[SourceSessionSummary]]


HARNESS_REGISTRY: dict[str, HarnessDefinition] = {
    "opencode": HarnessDefinition(
        name="opencode",
        display_name="OpenCode",
        resolve_source_path=resolve_opencode_db_path,
        scan=scan_opencode_sqlite,
        list_sessions=list_opencode_sessions,
    ),
    "pi": HarnessDefinition(
        name="pi",
        display_name="Pi",
        resolve_source_path=resolve_pi_sessions_path,
        scan=scan_pi_path,
        list_sessions=list_pi_sessions,
    ),
    "copilot": HarnessDefinition(
        name="copilot",
        display_name="Copilot",
        resolve_source_path=resolve_copilot_source_path,
        scan=scan_copilot_path,
        list_sessions=list_copilot_sessions,
    ),
    "codex": HarnessDefinition(
        name="codex",
        display_name="Codex",
        resolve_source_path=resolve_codex_sessions_path,
        scan=scan_codex_path,
        list_sessions=list_codex_sessions,
    ),
    "goose": HarnessDefinition(
        name="goose",
        display_name="Goose",
        resolve_source_path=resolve_goose_sessions_path,
        scan=scan_goose_sqlite,
        list_sessions=list_goose_sessions,
    ),
}


def get_harness(name: str) -> HarnessDefinition:
    try:
        return HARNESS_REGISTRY[name]
    except KeyError as exc:
        msg = f"Unknown harness: {name}"
        raise ValueError(msg) from exc
