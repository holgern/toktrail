"""Shared area detection from working-directory and git metadata."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from toktrail.config import AreasConfig


class SessionAreaMetadata(Protocol):
    cwd: str | None
    source_dir: str | None
    git_root: str | None
    git_remote: str | None


@dataclass(frozen=True)
class DetectedArea:
    area_path: str
    priority: int
    reason: str
    rule_index: int


def detect_area_for_session_metadata(
    areas_config: AreasConfig,
    metadata: SessionAreaMetadata,
) -> DetectedArea | None:
    """Return the best matching area rule for a source-session metadata snapshot.

    Returns ``None`` when auto-detection is disabled or no rule matches.
    """
    if not areas_config.auto_detect:
        return None

    path_candidates = _non_none(
        metadata.cwd,
        metadata.source_dir,
        metadata.git_root,
    )
    remote = metadata.git_remote

    matches: list[tuple[int, int, str, str, int]] = []
    for index, rule in enumerate(areas_config.rules):
        reason: str | None = None
        for candidate in path_candidates:
            for pattern in rule.cwd_globs:
                if _matches_cwd_glob(candidate, pattern):
                    reason = f"cwd matched {pattern}"
                    break
            if reason is not None:
                break
        if reason is None and remote:
            for remote_pattern in rule.git_remotes:
                if fnmatch.fnmatch(remote, remote_pattern):
                    reason = f"git remote matched {remote_pattern}"
                    break
        if reason is None:
            continue
        matches.append((rule.priority, index, rule.area, reason, index))

    if not matches:
        return None

    # Sort: higher priority, then higher rule index, then area path.
    matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    best = matches[0]
    return DetectedArea(
        area_path=best[2],
        priority=best[0],
        reason=best[3],
        rule_index=best[4],
    )


def _matches_cwd_glob(candidate: str, pattern: str) -> bool:
    normalized_candidate = _normalize_path_for_match(candidate)
    normalized_pattern = _normalize_path_for_match(str(Path(pattern).expanduser()))

    if fnmatch.fnmatch(normalized_candidate, normalized_pattern):
        return True

    # A pattern ending in /** should also match the base directory itself.
    if normalized_pattern.endswith("/**"):
        base = normalized_pattern[:-3]
        return (
            normalized_candidate == base
            or normalized_candidate.startswith(base.rstrip("/") + "/")
        )

    return False


def detect_area_for_cwd(
    areas_config: AreasConfig,
    cwd: str,
    *,
    git_remote: str | None = None,
) -> DetectedArea | None:
    """Convenience helper that only uses a cwd (and optional git remote)."""
    return detect_area_for_session_metadata(
        areas_config,
        _CwdOnlyMetadata(cwd=cwd, git_remote=git_remote),
    )


def _normalize_path_for_match(path_text: str) -> str:
    """Normalize separators for cross-platform matching."""
    return path_text.replace("\\", "/").rstrip("/")


def _non_none(*values: str | None) -> list[str]:
    return [v for v in values if v is not None]


@dataclass(frozen=True)
class _CwdOnlyMetadata:
    cwd: str | None = None
    source_dir: str | None = None
    git_root: str | None = None
    git_remote: str | None = None
