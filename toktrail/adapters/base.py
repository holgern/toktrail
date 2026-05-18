from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

from toktrail.config import CostingConfig
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import CostTotals


@dataclass(frozen=True)
class ScanResult:
    source_path: Path
    rows_seen: int
    rows_skipped: int
    events: list[UsageEvent]
    files_seen: int | None = None
    session_metadata: tuple[SourceSessionMetadata, ...] = ()
    file_states: tuple[ImportSourceFileState, ...] = ()
    discovered_file_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportSourceState:
    harness: str
    source_path: str
    source_session_id: str | None = None
    fingerprint_size: int | None = None
    fingerprint_mtime_ns: int | None = None
    fingerprint_inode: int | None = None
    sqlite_page_count: int | None = None
    sqlite_schema_version: int | None = None
    last_imported_created_ms: int | None = None
    last_seen_rowid: int | None = None
    last_file_offset: int | None = None
    updated_at_ms: int | None = None


@dataclass(frozen=True)
class ImportSourceFileState:
    harness: str
    source_path: str
    file_path: str
    size: int | None = None
    mtime_ns: int | None = None
    inode: int | None = None
    last_imported_created_ms: int | None = None
    last_file_offset: int | None = None
    parser_version: int | None = None
    parser_state_json: str | None = None
    updated_at_ms: int | None = None

    def matches_signature(
        self,
        *,
        size: int,
        mtime_ns: int,
        inode: int,
        parser_version: int,
    ) -> bool:
        return (
            self.size == size
            and self.mtime_ns == mtime_ns
            and self.inode == inode
            and self.parser_version == parser_version
        )

    def can_resume_append(
        self,
        *,
        size: int,
        inode: int,
        parser_version: int,
    ) -> bool:
        return (
            self.inode == inode
            and self.parser_version == parser_version
            and self.last_file_offset is not None
            and self.last_file_offset >= 0
            and self.last_file_offset <= size
            and self.size is not None
            and size > self.size
        )


@dataclass(frozen=True)
class FileSignature:
    size: int
    mtime_ns: int
    inode: int


@dataclass(frozen=True)
class FileScanDecision:
    mode: Literal["full", "resume", "skip"]
    signature: FileSignature
    prior_state: ImportSourceFileState | None = None


@dataclass(frozen=True)
class ImportScanState:
    source_state: ImportSourceState | None = None
    file_states: Mapping[str, ImportSourceFileState] = field(default_factory=dict)

    def file_state(self, path: Path | str) -> ImportSourceFileState | None:
        key = str(path)
        state = self.file_states.get(key)
        if state is not None:
            return state
        return self.file_states.get(str(Path(key).expanduser()))

    @classmethod
    def from_file_states(
        cls,
        *,
        source_state: ImportSourceState | None,
        file_states: tuple[ImportSourceFileState, ...],
    ) -> ImportScanState:
        return cls(
            source_state=source_state,
            file_states={state.file_path: state for state in file_states},
        )


def stat_file_signature(path: Path) -> FileSignature | None:
    try:
        if not path.is_file():
            return None
        stat = path.stat()
    except OSError:
        return None
    return FileSignature(
        size=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
        inode=int(stat.st_ino),
    )


def decide_file_scan(
    file_path: Path,
    *,
    parser_version: int,
    import_state: ImportScanState | None = None,
    allow_resume: bool = False,
) -> FileScanDecision | None:
    signature = stat_file_signature(file_path)
    if signature is None:
        return None
    prior_state = (
        import_state.file_state(file_path) if import_state is not None else None
    )
    if prior_state is None:
        return FileScanDecision(mode="full", signature=signature)
    if prior_state.matches_signature(
        size=signature.size,
        mtime_ns=signature.mtime_ns,
        inode=signature.inode,
        parser_version=parser_version,
    ):
        return FileScanDecision(
            mode="skip",
            signature=signature,
            prior_state=prior_state,
        )
    if allow_resume and prior_state.can_resume_append(
        size=signature.size,
        inode=signature.inode,
        parser_version=parser_version,
    ):
        return FileScanDecision(
            mode="resume",
            signature=signature,
            prior_state=prior_state,
        )
    return FileScanDecision(mode="full", signature=signature, prior_state=prior_state)


def build_import_source_file_state(
    *,
    harness: str,
    source_path: Path,
    file_path: Path,
    signature: FileSignature,
    last_imported_created_ms: int | None,
    last_file_offset: int | None,
    parser_version: int,
    parser_state_json: str | None = None,
) -> ImportSourceFileState:
    return ImportSourceFileState(
        harness=harness,
        source_path=str(source_path),
        file_path=str(file_path),
        size=signature.size,
        mtime_ns=signature.mtime_ns,
        inode=signature.inode,
        last_imported_created_ms=last_imported_created_ms,
        last_file_offset=last_file_offset,
        parser_version=parser_version,
        parser_state_json=parser_state_json,
    )


@dataclass(frozen=True)
class SourceSessionMetadata:
    harness: str
    source_session_id: str
    source_paths: tuple[str, ...] = ()
    cwd: str | None = None
    source_dir: str | None = None
    git_root: str | None = None
    git_remote: str | None = None
    session_title: str | None = None
    started_ms: int | None = None
    last_seen_ms: int | None = None


@dataclass(frozen=True)
class SourceSessionSummary:
    harness: str
    source_session_id: str
    first_created_ms: int
    last_created_ms: int
    assistant_message_count: int
    tokens: TokenBreakdown
    costs: CostTotals
    models: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()

    @property
    def source_cost_usd(self) -> Decimal:
        return self.costs.source_cost_usd

    @property
    def actual_cost_usd(self) -> Decimal:
        return self.costs.actual_cost_usd

    @property
    def virtual_cost_usd(self) -> Decimal:
        return self.costs.virtual_cost_usd

    @property
    def savings_usd(self) -> Decimal:
        return self.costs.savings_usd

    @property
    def unpriced_count(self) -> int:
        return self.costs.unpriced_count


class HarnessAdapter(Protocol):
    name: str
    display_name: str

    def scan(
        self,
        source_path: Path,
        *,
        source_session_id: str | None = None,
        include_raw_json: bool = True,
        since_ms: int | None = None,
        import_state: ImportScanState | None = None,
    ) -> ScanResult: ...

    def list_sessions(
        self,
        source_path: Path,
        *,
        costing_config: CostingConfig | None = None,
    ) -> list[SourceSessionSummary]: ...

    def parse(self, source_path: Path) -> list[UsageEvent]: ...
