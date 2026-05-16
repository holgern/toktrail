from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from toktrail.adapters.base import (
    ScanResult,
    SourceSessionMetadata,
    SourceSessionSummary,
)
from toktrail.adapters.codex import (
    CODEX_PARSER_VERSION,
    _make_fingerprint,
    scan_codex_file,
    scan_codex_path,
)
from toktrail.adapters.summary import summarize_events_by_source_session
from toktrail.config import CostingConfig
from toktrail.models import UsageEvent

CODE_HARNESS = "code"
CODE_PARSER_VERSION = CODEX_PARSER_VERSION

CodeScanResult = ScanResult
CodeSessionSummary = SourceSessionSummary


def scan_code_path(
    source_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> CodeScanResult:
    scan = scan_codex_path(
        source_path,
        source_session_id=source_session_id,
        include_raw_json=include_raw_json,
        since_ms=since_ms,
        import_state=import_state,
    )
    return _as_code_scan(scan)


def scan_code_file(
    file_path: Path,
    *,
    source_session_id: str | None = None,
    include_raw_json: bool = True,
    since_ms: int | None = None,
    import_state: object | None = None,
) -> CodeScanResult:
    scan = scan_codex_file(
        file_path,
        source_session_id=source_session_id,
        include_raw_json=include_raw_json,
        since_ms=since_ms,
        import_state=import_state,
    )
    return _as_code_scan(scan)


def parse_code_file(path: Path) -> list[UsageEvent]:
    return scan_code_file(path).events


def parse_code_path(path: Path) -> list[UsageEvent]:
    return scan_code_path(path).events


def list_code_sessions(
    source_path: Path,
    *,
    costing_config: CostingConfig | None = None,
) -> list[CodeSessionSummary]:
    scan = scan_code_path(source_path, include_raw_json=False)
    return summarize_events_by_source_session(
        CODE_HARNESS,
        scan.events,
        source_paths_by_session=_code_source_paths_by_session(source_path),
        costing_config=costing_config,
    )


def _as_code_scan(scan: ScanResult) -> CodeScanResult:
    return CodeScanResult(
        source_path=scan.source_path,
        files_seen=scan.files_seen,
        rows_seen=scan.rows_seen,
        rows_skipped=scan.rows_skipped,
        events=[_as_code_event(event) for event in scan.events],
        session_metadata=tuple(
            _as_code_session_metadata(metadata) for metadata in scan.session_metadata
        ),
    )


def _as_code_event(event: UsageEvent) -> UsageEvent:
    remapped = replace(
        event,
        harness=CODE_HARNESS,
        global_dedup_key=f"{CODE_HARNESS}:{event.source_session_id}:{event.source_dedup_key}",
    )
    return replace(remapped, fingerprint_hash=_make_fingerprint(remapped))


def _as_code_session_metadata(
    metadata: SourceSessionMetadata,
) -> SourceSessionMetadata:
    return replace(metadata, harness=CODE_HARNESS)


def _code_source_paths_by_session(source_path: Path) -> dict[str, list[Path]]:
    resolved_path = source_path.expanduser()
    if not resolved_path.exists():
        return {}

    file_paths = (
        [resolved_path]
        if resolved_path.is_file()
        else sorted(path for path in resolved_path.rglob("*.jsonl") if path.is_file())
    )
    grouped: dict[str, list[Path]] = {}
    for file_path in file_paths:
        scan = scan_code_file(file_path, include_raw_json=False)
        for event in scan.events:
            grouped.setdefault(event.source_session_id, []).append(file_path)
    return grouped


__all__ = [
    "CODE_HARNESS",
    "CODE_PARSER_VERSION",
    "CodeScanResult",
    "CodeSessionSummary",
    "list_code_sessions",
    "parse_code_file",
    "parse_code_path",
    "scan_code_file",
    "scan_code_path",
]
