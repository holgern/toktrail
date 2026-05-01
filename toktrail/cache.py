"""Source file cache for avoiding reparsing large event files.

Caches normalized events and parser metadata by harness, path, parser version, and
fingerprint. Cache is validated by exact fingerprint match. If fingerprint,
parser version, or file metadata changes, cache is invalidated and reparsed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from toktrail.models import UsageEvent


@dataclass(frozen=True)
class CacheKey:
    """Unique cache identifier combining harness, path, and parser version."""

    harness: str
    canonical_source_path: Path
    parser_version: int


@dataclass(frozen=True)
class CacheEntry:
    """Cached parse result with metadata for validation."""

    events: list[UsageEvent]
    parser_warnings: list[str]
    fingerprint: str
    parser_version: int
    schema_version: int = 1


def fingerprint_for_path(path: Path) -> str:
    """Compute fingerprint for source file.

    Combines file size, mtime, and content hash for validation.
    """
    try:
        stat = path.stat()
        # Read first 64KB for hash to avoid large file overhead
        with open(path, "rb") as f:
            chunk = f.read(65536)
            content_hash = hashlib.sha256(chunk).hexdigest()[:16]
        return f"{stat.st_size}:{stat.st_mtime_ns}:{content_hash}"
    except OSError:
        return ""


def is_cache_valid(entry: CacheEntry, current_path: Path, parser_version: int) -> bool:
    """Check if cached entry is still valid.

    Valid if:
    - Parser version matches
    - File fingerprint still matches
    - File exists and has not shrunk
    """
    if not current_path.exists():
        return False
    if parser_version != entry.parser_version:
        return False

    current_fp = fingerprint_for_path(current_path)
    if not current_fp or current_fp != entry.fingerprint:
        return False

    return True
