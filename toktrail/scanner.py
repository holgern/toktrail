"""Central source discovery and fingerprinting for multi-path harness discovery."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from toktrail.adapters.registry import HARNESS_REGISTRY
from toktrail.config import ToktrailConfig, default_toktrail_config


@dataclass(frozen=True)
class SourceFingerprint:
    """Fingerprint of a source file for caching and deduplication."""

    size: int | None
    mtime_ns: int | None
    inode: int | None
    sqlite_page_count: int | None = None
    sqlite_schema_version: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class SourceFile:
    """A discovered source file ready for import."""

    harness: str
    path: Path
    kind: Literal["json", "jsonl", "sqlite", "directory"]
    fingerprint: SourceFingerprint
    source_session_id: str | None = None
    workspace_key: str | None = None
    workspace_label: str | None = None


@dataclass(frozen=True)
class ScanWarning:
    """A non-fatal warning during source discovery."""

    harness: str
    path: Path | None
    message: str


@dataclass(frozen=True)
class DiscoverSourcesResult:
    """Result of source discovery with sources and warnings."""

    sources: tuple[SourceFile, ...]
    warnings: tuple[ScanWarning, ...]


def _compute_fingerprint(path: Path) -> SourceFingerprint:
    """Compute a fingerprint for a source file."""
    if not path.exists():
        return SourceFingerprint(
            size=None,
            mtime_ns=None,
            inode=None,
        )

    try:
        stat = path.stat()
        size = stat.st_size
        mtime_ns = int(stat.st_mtime_ns)
        inode = stat.st_ino
    except (OSError, ValueError):
        return SourceFingerprint(
            size=None,
            mtime_ns=None,
            inode=None,
        )

    # For SQLite files, also get schema version
    sqlite_page_count = None
    sqlite_schema_version = None
    if path.suffix == ".db" and path.is_file():
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.execute("PRAGMA query_only = ON")
            try:
                cursor = conn.execute("PRAGMA page_count")
                sqlite_page_count = cursor.fetchone()[0]
                cursor = conn.execute("PRAGMA user_version")
                sqlite_schema_version = cursor.fetchone()[0]
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            pass

    return SourceFingerprint(
        size=size,
        mtime_ns=mtime_ns,
        inode=inode,
        sqlite_page_count=sqlite_page_count,
        sqlite_schema_version=sqlite_schema_version,
    )


def discover_sources(  # noqa: C901
    *,
    harnesses: list[str] | None = None,
    config: ToktrailConfig | None = None,
    explicit_source: Path | None = None,
    home_dir: Path | None = None,
    use_env_roots: bool = True,
) -> DiscoverSourcesResult:
    """Discover source files for imports.

    Args:
        harnesses: List of harness names to discover; if None, uses all.
        config: ToktrailConfig with configured sources; if None, creates default.
        explicit_source: Single explicit source path; requires exactly one harness.
        home_dir: Override home directory for path resolution.
        use_env_roots: Whether to use environment variable roots.

    Returns:
        DiscoverSourcesResult with discovered sources and warnings.
    """
    sources: list[SourceFile] = []
    warnings: list[ScanWarning] = []

    if config is None:
        config = default_toktrail_config()

    if harnesses is None:
        harnesses = list(config.imports.harnesses)

    if explicit_source is not None:
        if len(harnesses) != 1:
            return DiscoverSourcesResult(
                sources=(),
                warnings=(
                    ScanWarning(
                        harness="",
                        path=explicit_source,
                        message=(
                            "Explicit source requires exactly one harness; "
                            f"got {len(harnesses)}"
                        ),
                    ),
                ),
            )

    for harness_name in harnesses:
        harness = HARNESS_REGISTRY.get(harness_name)
        if not harness:
            warnings.append(
                ScanWarning(
                    harness=harness_name,
                    path=None,
                    message=f"Unknown harness: {harness_name}",
                )
            )
            continue

        # Determine source paths for this harness
        source_paths: list[Path] = []

        if explicit_source is not None:
            source_paths = [explicit_source]
        else:
            # Get configured sources
            configured = None
            if config.imports.sources and harness_name in config.imports.sources:
                configured = config.imports.sources[harness_name]

            if configured:
                if isinstance(configured, Path):
                    source_paths = [configured]
                elif isinstance(configured, list):
                    source_paths = [
                        p if isinstance(p, Path) else Path(p).expanduser()
                        for p in configured
                    ]
            else:
                # Use default from registry
                default = harness.resolve_source_path(None)
                if default:
                    source_paths = [default]

        # Process each source path
        for source_path in source_paths:
            source_path = source_path.expanduser()

            if not source_path.exists():
                if config.imports.missing_source == "error":
                    warnings.append(
                        ScanWarning(
                            harness=harness_name,
                            path=source_path,
                            message=f"Source path does not exist: {source_path}",
                        )
                    )
                elif config.imports.missing_source == "warn":
                    warnings.append(
                        ScanWarning(
                            harness=harness_name,
                            path=source_path,
                            message=f"Source path does not exist: {source_path}",
                        )
                    )
                continue

            # Determine kind
            kind: Literal["json", "jsonl", "sqlite", "directory"]
            if source_path.is_dir():
                kind = "directory"
            elif source_path.suffix == ".db":
                kind = "sqlite"
            elif source_path.suffix == ".json":
                kind = "json"
            elif source_path.suffix == ".jsonl":
                kind = "jsonl"
            else:
                kind = "directory"

            fingerprint = _compute_fingerprint(source_path)

            sources.append(
                SourceFile(
                    harness=harness_name,
                    path=source_path,
                    kind=kind,
                    fingerprint=fingerprint,
                )
            )

    return DiscoverSourcesResult(
        sources=tuple(sources),
        warnings=tuple(warnings),
    )
