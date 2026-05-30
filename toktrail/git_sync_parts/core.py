from __future__ import annotations

import json
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import time

from toktrail import db
from toktrail.sync import (
    ConflictMode,
    ImportedStateContext,
    RemoteActiveMode,
    StateExportResult,
    StateImportResult,
    merge_imported_state_db,
)

GIT_SYNC_FORMAT = "toktrail.git-sync.v2"
_LEGACY_GIT_SYNC_FORMAT = "toktrail.git-sync.v1"
_SUPPORTED_GIT_SYNC_FORMATS = frozenset({GIT_SYNC_FORMAT, _LEGACY_GIT_SYNC_FORMAT})
DEFAULT_STATE_DIR = "state"
DEFAULT_ARCHIVE_DIR = DEFAULT_STATE_DIR
DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"
_LEGACY_STATE_FORMAT = "toktrail.text-state.v3"
_STATE_FORMAT = "toktrail.text-state.v4"
_SUPPORTED_STATE_FORMATS = frozenset({_STATE_FORMAT, _LEGACY_STATE_FORMAT})
_USAGE_SESSION_FORMAT = "toktrail.usage-session.v1"
_RUN_EVENTS_FORMAT = "toktrail.run-events.v1"
_STAGING_PREFIX = ".state.staging."
_STATE_DB_FILE_NAMES = frozenset({"toktrail.db", "toktrail.db-wal", "toktrail.db-shm"})
_HOOK_MARKER = "# toktrail-managed-hook v1"
_MANAGED_HOOKS = ("post-merge", "post-checkout", "post-rewrite")
_OBSOLETE_FIXED_PATHS = (
    "archives",
    "toktrail.db",
    "toktrail.db-wal",
    "toktrail.db-shm",
)
_OBSOLETE_GLOBS = ("*.tar.gz", "*.sqlite", "*.sqlite3", ".state.staging.*")
_LIVE_SQLITE_GLOBS = ("*.sqlite", "*.sqlite3")
_DEFAULT_REWRITE_PATHS = (
    "archives",
    "toktrail.db",
    "toktrail.db-wal",
    "toktrail.db-shm",
)
_DEFAULT_REWRITE_PATH_GLOBS = ("*.tar.gz", "*.sqlite", "*.sqlite3", ".state.staging.*")
_BASE_ALLOWED_RESET_PATHS = (
    "README.md",
    ".gitignore",
    ".gitattributes",
    "meta",
    "state",
)
_CLEANUP_COMMIT_MESSAGE = "toktrail sync: cleanup obsolete repo files"
_RESET_HISTORY_COMMIT_MESSAGE = "toktrail sync: reset compact state history"
_RESET_EXPORT_COMMIT_MESSAGE = "toktrail sync: current text state before cleanup"
_STATE_TABLES: tuple[str, ...] = (
    "machines",
    "areas",
    "area_session_assignments",
    "machine_active_areas",
    "runs",
    "source_sessions",
    "source_session_metadata",
    "usage_events",
    "run_events",
)


@dataclass(frozen=True)
class GitSyncRepoStatus:
    repo_path: Path
    branch: str | None
    remote: str | None
    dirty: bool
    ahead: int | None
    behind: int | None
    state_file_count: int
    pending_import_count: int
    state_db_paths: tuple[str, ...] = ()

    @property
    def archive_count(self) -> int:
        return self.state_file_count

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "branch": self.branch,
            "remote": self.remote,
            "dirty": self.dirty,
            "ahead": self.ahead,
            "behind": self.behind,
            "state_file_count": self.state_file_count,
            "archive_count": self.state_file_count,
            "pending_import_count": self.pending_import_count,
            "state_db_paths": list(self.state_db_paths),
        }


@dataclass(frozen=True)
class GitSyncImportResult:
    repo_path: Path
    state_files_seen: int
    state_imported: bool
    state_skipped: bool
    import_results: tuple[StateImportResult, ...]

    @property
    def archives_seen(self) -> int:
        return self.state_files_seen

    @property
    def archives_imported(self) -> int:
        return 1 if self.state_imported else 0

    @property
    def archives_skipped(self) -> int:
        return self.state_files_seen if self.state_skipped else 0

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "state_files_seen": self.state_files_seen,
            "state_imported": self.state_imported,
            "state_skipped": self.state_skipped,
            "archives_seen": self.archives_seen,
            "archives_imported": self.archives_imported,
            "archives_skipped": self.archives_skipped,
            "import_results": [
                _state_import_result_dict(item) for item in self.import_results
            ],
        }


@dataclass(frozen=True)
class GitSyncExportResult:
    repo_path: Path
    state_path: Path
    committed: bool
    pushed: bool
    commit_hash: str | None
    export_result: StateExportResult

    @property
    def archive_path(self) -> Path:
        return self.state_path

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "state_path": str(self.state_path),
            "archive_path": str(self.state_path),
            "committed": self.committed,
            "pushed": self.pushed,
            "commit_hash": self.commit_hash,
            "export_result": _state_export_result_dict(self.export_result),
        }


@dataclass(frozen=True)
class GitSyncResult:
    pull: GitSyncImportResult
    push: GitSyncExportResult | None

    def as_dict(self) -> dict[str, object]:
        return {
            "pull": self.pull.as_dict(),
            "push": None if self.push is None else self.push.as_dict(),
        }


@dataclass(frozen=True)
class GitSyncCleanupSize:
    path: str
    apparent_bytes: int
    physical_bytes: int
    file_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "apparent_bytes": self.apparent_bytes,
            "physical_bytes": self.physical_bytes,
            "file_count": self.file_count,
        }


@dataclass(frozen=True)
class GitSyncCleanupAnalysis:
    repo_path: Path
    branch: str | None
    dirty: bool
    state_dir: str
    state_size: GitSyncCleanupSize | None
    obsolete_sizes: tuple[GitSyncCleanupSize, ...]
    live_sqlite_paths: tuple[str, ...]
    git_count_objects: dict[str, str]
    filter_repo_available: bool
    recommended_action: str

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "mode": "analysis",
            "branch": self.branch,
            "dirty": self.dirty,
            "state_dir": self.state_dir,
            "sizes": {
                "git": dict(self.git_count_objects),
                "state": None if self.state_size is None else self.state_size.as_dict(),
                "obsolete": [item.as_dict() for item in self.obsolete_sizes],
            },
            "live_sqlite_paths": list(self.live_sqlite_paths),
            "filter_repo_available": self.filter_repo_available,
            "recommendation": self.recommended_action,
        }


@dataclass(frozen=True)
class GitSyncCleanupResult:
    repo_path: Path
    mode: str
    dry_run: bool
    removed_paths: tuple[str, ...]
    backup_bundle: Path | None
    committed: bool
    commit_hash: str | None
    pushed: bool
    before_git_size: dict[str, str]
    after_git_size: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "mode": self.mode,
            "dry_run": self.dry_run,
            "removed_paths": list(self.removed_paths),
            "backup_bundle": None
            if self.backup_bundle is None
            else str(self.backup_bundle),
            "committed": self.committed,
            "commit_hash": self.commit_hash,
            "pushed": self.pushed,
            "before_git_size": dict(self.before_git_size),
            "after_git_size": dict(self.after_git_size),
        }


@dataclass(frozen=True)
class GitHookInstallResult:
    repo_path: Path
    installed: tuple[str, ...]
    skipped: tuple[str, ...]
    overwritten: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "installed": list(self.installed),
            "skipped": list(self.skipped),
            "overwritten": list(self.overwritten),
        }


@dataclass(frozen=True)
class GitHookStatus:
    repo_path: Path
    hooks: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "hooks": dict(self.hooks),
        }


def ensure_git_repo(repo_path: Path, *, remote_url: str | None, branch: str) -> None:
    resolved_repo = repo_path.expanduser()
    git_dir = resolved_repo / ".git"
    if git_dir.exists() and not git_dir.is_dir():
        msg = f"Invalid git directory path: {git_dir}"
        raise ValueError(msg)

    if git_dir.exists():
        _ensure_branch(resolved_repo, branch)
    else:
        if resolved_repo.exists() and any(resolved_repo.iterdir()):
            msg = f"Repo path exists and is not empty: {resolved_repo}"
            raise ValueError(msg)
        if remote_url and not resolved_repo.exists():
            parent = resolved_repo.parent
            parent.mkdir(parents=True, exist_ok=True)
            _run_git(
                parent,
                "clone",
                "--origin",
                DEFAULT_REMOTE,
                remote_url,
                str(resolved_repo),
            )
            _ensure_branch(resolved_repo, branch)
        else:
            resolved_repo.mkdir(parents=True, exist_ok=True)
            _run_git(resolved_repo, "init", "-b", branch)

    if remote_url:
        _set_remote_url(resolved_repo, DEFAULT_REMOTE, remote_url)

    _write_repo_layout(resolved_repo)


def git_pull(repo_path: Path, *, remote: str, branch: str) -> None:
    resolved_repo = _require_repo(repo_path)
    _ensure_branch(resolved_repo, branch)
    try:
        _run_git(resolved_repo, "pull", "--ff-only", remote, branch)
    except ValueError as exc:
        detail = str(exc).lower()
        if (
            "couldn't find remote ref" in detail
            or "no such ref was fetched" in detail
            or "couldn't find ref" in detail
        ):
            return
        raise


def git_push(repo_path: Path, *, remote: str, branch: str) -> None:
    resolved_repo = _require_repo(repo_path)
    _ensure_branch(resolved_repo, branch)
    _run_git(resolved_repo, "push", remote, branch)


def list_archives(
    repo_path: Path,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
) -> list[Path]:
    return list_state_files(repo_path, state_dir=archive_dir)


def read_archive_manifest(archive_path: Path) -> dict[str, object]:
    payload: object = json.loads(archive_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"State manifest is not an object: {archive_path}"
        raise ValueError(msg)
    return payload


def import_repo_archives(
    db_path: Path,
    repo_path: Path,
    *,
    dry_run: bool,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
    on_conflict: ConflictMode = "fail",
    remote_active: RemoteActiveMode = "close-at-export",
) -> GitSyncImportResult:
    return import_repo_state(
        db_path,
        repo_path,
        dry_run=dry_run,
        state_dir=archive_dir,
        on_conflict=on_conflict,
        remote_active=remote_active,
    )


def export_repo_archive(
    db_path: Path,
    repo_path: Path,
    *,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
    config_path: Path,
    include_config: bool,
    redact_raw_json: bool,
    commit_message: str | None,
    remote: str,
    branch: str,
    push: bool,
    allow_dirty: bool,
    tracked_config_paths: tuple[Path, ...] = (),
) -> GitSyncExportResult:
    return export_repo_state(
        db_path,
        repo_path,
        state_dir=archive_dir,
        config_path=config_path,
        include_config=include_config,
        redact_raw_json=redact_raw_json,
        commit_message=commit_message,
        remote=remote,
        branch=branch,
        push=push,
        allow_dirty=allow_dirty,
        tracked_config_paths=tracked_config_paths,
    )


def export_repo_state(
    db_path: Path,
    repo_path: Path,
    *,
    state_dir: str = DEFAULT_STATE_DIR,
    config_path: Path,
    include_config: bool,
    redact_raw_json: bool,
    commit_message: str | None,
    remote: str,
    branch: str,
    push: bool,
    allow_dirty: bool,
    tracked_config_paths: tuple[Path, ...] = (),
) -> GitSyncExportResult:
    resolved_repo = _require_repo(repo_path)
    if include_config:
        msg = (
            "sync.git.include_config is not supported for git sync; "
            'use [sync.git].track = ["config", ...] instead.'
        )
        raise ValueError(msg)
    _write_repo_layout(resolved_repo)
    _cleanup_stale_staging_dirs(resolved_repo)
    _fail_if_contains_state_db_files(resolved_repo)
    tracked_relpaths, tracked_prefixes = _repo_relative_tracked_paths(
        resolved_repo,
        tracked_config_paths,
    )
    if not allow_dirty and _has_uncommitted_disallowed_changes(
        resolved_repo,
        allowed_prefixes=(
            "README.md",
            ".gitignore",
            ".gitattributes",
            "meta/",
            f"{state_dir.rstrip('/')}/",
            f"{_STAGING_PREFIX}",
            *tracked_prefixes,
        ),
        allowed_relpaths=set(tracked_relpaths),
    ):
        msg = (
            "Git sync repo has uncommitted changes. Commit or stash them, or rerun "
            "with --allow-dirty."
        )
        raise ValueError(msg)

    state_root = resolved_repo / state_dir
    export_result = _export_text_state(
        db_path.expanduser(),
        state_root,
        redact_raw_json=redact_raw_json,
    )

    _run_git(resolved_repo, "add", "-A", _repo_relpath(state_root, resolved_repo))
    _run_git(
        resolved_repo,
        "add",
        "meta/format.json",
        ".gitignore",
        ".gitattributes",
        "README.md",
    )
    for relpath in tracked_relpaths:
        _run_git(resolved_repo, "add", "-A", relpath)
    for relpath in tracked_prefixes:
        _run_git(resolved_repo, "add", "-A", relpath.rstrip("/"))

    committed = False
    commit_hash: str | None = None
    if _repo_has_staged_changes(resolved_repo):
        exported_stamp = datetime.fromtimestamp(
            export_result.exported_at_ms / 1000,
            tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        commit_text = commit_message or (
            "toktrail sync: "
            f"{(export_result.machine_name or export_result.machine_id)} "
            f"{exported_stamp}"
        )
        _run_git(resolved_repo, "commit", "-m", commit_text)
        commit_hash = _run_git_output(resolved_repo, "rev-parse", "HEAD").strip()
        committed = True

    pushed = False
    if push and committed:
        git_push(resolved_repo, remote=remote, branch=branch)
        pushed = True

    return GitSyncExportResult(
        repo_path=resolved_repo,
        state_path=state_root,
        committed=committed,
        pushed=pushed,
        commit_hash=commit_hash,
        export_result=export_result,
    )


def git_sync_status(
    db_path: Path,
    repo_path: Path,
    *,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
    remote: str = DEFAULT_REMOTE,
) -> GitSyncRepoStatus:
    resolved_repo = _require_repo(repo_path)
    branch = _current_branch(resolved_repo)
    remote_url = _remote_url(resolved_repo, remote)
    dirty = _repo_is_dirty(resolved_repo)
    ahead, behind = _ahead_behind(resolved_repo)
    state_root = resolved_repo / archive_dir
    state_files = list_state_files(resolved_repo, state_dir=archive_dir)
    state_fingerprint = _state_files_fingerprint(state_root, state_files)
    pending = 0
    if state_fingerprint:
        conn = db.connect(db_path.expanduser())
        try:
            db.migrate(conn)
            pending = 0 if db.has_imported_sync_archive(conn, state_fingerprint) else 1
        finally:
            conn.close()

    state_db_paths = tuple(
        _repo_relpath(path, resolved_repo)
        for path in _find_state_db_files(resolved_repo)
    )
    return GitSyncRepoStatus(
        repo_path=resolved_repo,
        branch=branch,
        remote=remote_url,
        dirty=dirty,
        ahead=ahead,
        behind=behind,
        state_file_count=len(state_files),
        pending_import_count=pending,
        state_db_paths=state_db_paths,
    )


def analyze_git_sync_cleanup(
    repo_path: Path,
    *,
    state_dir: str = DEFAULT_STATE_DIR,
) -> GitSyncCleanupAnalysis:
    resolved_repo = _require_repo(repo_path)
    state_root = resolved_repo / state_dir
    state_size = (
        _path_size(state_root, repo_path=resolved_repo) if state_root.exists() else None
    )
    obsolete_paths = _find_cleanup_candidates(resolved_repo)
    obsolete_sizes = tuple(
        _path_size(path, repo_path=resolved_repo)
        for path in obsolete_paths
        if path.exists()
    )
    live_sqlite_paths = tuple(
        _repo_relpath(path, resolved_repo)
        for path in _find_live_sqlite_files(resolved_repo)
    )
    recommendation = _cleanup_recommendation(obsolete_paths, live_sqlite_paths)
    return GitSyncCleanupAnalysis(
        repo_path=resolved_repo,
        branch=_current_branch(resolved_repo),
        dirty=_repo_is_dirty(resolved_repo),
        state_dir=state_dir,
        state_size=state_size,
        obsolete_sizes=obsolete_sizes,
        live_sqlite_paths=live_sqlite_paths,
        git_count_objects=_git_count_objects(resolved_repo),
        filter_repo_available=_filter_repo_available(),
        recommended_action=recommendation,
    )


def cleanup_git_sync_worktree(
    repo_path: Path,
    *,
    state_dir: str = DEFAULT_STATE_DIR,
    commit: bool = False,
    dry_run: bool = True,
    allow_dirty: bool = False,
) -> GitSyncCleanupResult:
    resolved_repo = _require_repo(repo_path)
    _require_git_sync_repo_format(resolved_repo)
    before_git_size = _git_count_objects(resolved_repo)
    cleanup_paths = _find_cleanup_candidates(resolved_repo)
    removed_paths = tuple(_repo_relpath(path, resolved_repo) for path in cleanup_paths)
    allowed_relpaths, allowed_prefixes = _cleanup_allowed_dirty_paths(
        resolved_repo,
        cleanup_paths,
    )
    if not allow_dirty and _has_uncommitted_disallowed_changes(
        resolved_repo,
        allowed_prefixes=(
            "README.md",
            ".gitignore",
            ".gitattributes",
            "meta/",
            *allowed_prefixes,
        ),
        allowed_relpaths=allowed_relpaths,
    ):
        msg = (
            "Git sync repo has uncommitted changes outside cleanup targets. Commit or "
            "stash them, or rerun with --allow-dirty."
        )
        raise ValueError(msg)

    if dry_run:
        return GitSyncCleanupResult(
            repo_path=resolved_repo,
            mode="working-tree",
            dry_run=True,
            removed_paths=removed_paths,
            backup_bundle=None,
            committed=False,
            commit_hash=None,
            pushed=False,
            before_git_size=before_git_size,
            after_git_size=before_git_size,
        )

    _write_repo_layout(resolved_repo)
    _remove_cleanup_paths(resolved_repo, cleanup_paths)
    _run_git(
        resolved_repo,
        "add",
        "-A",
        ".gitignore",
        ".gitattributes",
        "README.md",
        "meta/format.json",
    )

    committed = False
    commit_hash: str | None = None
    if commit and _repo_has_staged_changes(resolved_repo):
        _run_git(resolved_repo, "commit", "-m", _CLEANUP_COMMIT_MESSAGE)
        commit_hash = _run_git_output(resolved_repo, "rev-parse", "HEAD").strip()
        committed = True
        _run_git(resolved_repo, "gc")

    after_git_size = _git_count_objects(resolved_repo)
    return GitSyncCleanupResult(
        repo_path=resolved_repo,
        mode="working-tree",
        dry_run=False,
        removed_paths=removed_paths,
        backup_bundle=None,
        committed=committed,
        commit_hash=commit_hash,
        pushed=False,
        before_git_size=before_git_size,
        after_git_size=after_git_size,
    )


def reset_git_sync_history(
    db_path: Path,
    repo_path: Path,
    *,
    config_path: Path,
    tracked_config_paths: tuple[Path, ...],
    redact_raw_json: bool,
    state_dir: str = DEFAULT_STATE_DIR,
    branch: str = DEFAULT_BRANCH,
    remote: str = DEFAULT_REMOTE,
    push: bool = False,
    force: bool = False,
    allow_dirty: bool = False,
) -> GitSyncCleanupResult:
    if not force:
        msg = "History reset is destructive. Rerun with --force to continue."
        raise ValueError(msg)

    resolved_repo = _require_repo(repo_path)
    _require_git_sync_repo_format(resolved_repo)
    _ensure_branch(resolved_repo, branch)
    if not allow_dirty and _repo_is_dirty(resolved_repo):
        msg = (
            "Git sync repo has uncommitted changes. Commit or stash them, or rerun "
            "with --allow-dirty."
        )
        raise ValueError(msg)

    before_git_size = _git_count_objects(resolved_repo)
    import_repo_archives(db_path, resolved_repo, dry_run=False, archive_dir=state_dir)
    export_repo_archive(
        db_path,
        resolved_repo,
        archive_dir=state_dir,
        config_path=config_path,
        include_config=False,
        redact_raw_json=redact_raw_json,
        commit_message=_RESET_EXPORT_COMMIT_MESSAGE,
        remote=remote,
        branch=branch,
        push=False,
        allow_dirty=allow_dirty,
        tracked_config_paths=tracked_config_paths,
    )

    cleanup_paths = _find_cleanup_candidates(resolved_repo)
    removed_paths = tuple(_repo_relpath(path, resolved_repo) for path in cleanup_paths)
    backup_bundle = _create_backup_bundle(resolved_repo)
    orphan_branch = _unique_branch_name(resolved_repo, "toktrail-cleanup-reset")
    _run_git(resolved_repo, "checkout", "--orphan", orphan_branch)
    _clear_git_index(resolved_repo)
    _remove_cleanup_paths(resolved_repo, cleanup_paths)
    _remove_non_reset_paths(
        resolved_repo,
        allowed_roots=_allowed_reset_paths(
            resolved_repo,
            tracked_config_paths=tracked_config_paths,
        ),
    )
    _write_repo_layout(resolved_repo)
    _stage_reset_paths(
        resolved_repo,
        allowed_roots=_allowed_reset_paths(
            resolved_repo,
            tracked_config_paths=tracked_config_paths,
        ),
    )
    if not _repo_has_staged_changes(resolved_repo):
        msg = "Reset history produced no staged sync state to commit."
        raise ValueError(msg)
    _run_git(resolved_repo, "commit", "-m", _RESET_HISTORY_COMMIT_MESSAGE)
    _run_git(resolved_repo, "branch", "-M", branch)
    commit_hash = _run_git_output(resolved_repo, "rev-parse", "HEAD").strip()
    _run_git(resolved_repo, "reflog", "expire", "--expire=now", "--all")
    _run_git(resolved_repo, "gc", "--prune=now", "--aggressive")
    pushed = False
    if push and _remote_branch_exists(resolved_repo, remote, branch):
        _run_git(resolved_repo, "fetch", remote, branch)
        _run_git_force_with_lease(resolved_repo, remote=remote, branch=branch)
        pushed = True
    after_git_size = _git_count_objects(resolved_repo)
    return GitSyncCleanupResult(
        repo_path=resolved_repo,
        mode="reset-history",
        dry_run=False,
        removed_paths=removed_paths,
        backup_bundle=backup_bundle,
        committed=True,
        commit_hash=commit_hash,
        pushed=pushed,
        before_git_size=before_git_size,
        after_git_size=after_git_size,
    )


def rewrite_git_sync_history(
    repo_path: Path,
    *,
    branch: str = DEFAULT_BRANCH,
    remote: str = DEFAULT_REMOTE,
    push: bool = False,
    force: bool = False,
    allow_dirty: bool = False,
    paths: tuple[str, ...] = _DEFAULT_REWRITE_PATHS,
    path_globs: tuple[str, ...] = _DEFAULT_REWRITE_PATH_GLOBS,
) -> GitSyncCleanupResult:
    if not force:
        msg = "History rewrite is destructive. Rerun with --force to continue."
        raise ValueError(msg)
    if not _filter_repo_available():
        msg = (
            "git-filter-repo is required for --rewrite-history. "
            "Install git-filter-repo or use --reset-history instead."
        )
        raise ValueError(msg)

    resolved_repo = _require_repo(repo_path)
    _require_git_sync_repo_format(resolved_repo)
    _ensure_branch(resolved_repo, branch)
    if not allow_dirty and _repo_is_dirty(resolved_repo):
        msg = (
            "Git sync repo has uncommitted changes. Commit or stash them, or rerun "
            "with --allow-dirty."
        )
        raise ValueError(msg)

    before_git_size = _git_count_objects(resolved_repo)
    cleanup_paths = _find_cleanup_candidates(resolved_repo)
    removed_paths = tuple(_repo_relpath(path, resolved_repo) for path in cleanup_paths)
    backup_bundle = _create_backup_bundle(resolved_repo)
    command = ["filter-repo", "--force", "--invert-paths"]
    for item in paths:
        command.extend(("--path", item))
    for item in path_globs:
        command.extend(("--path-glob", item))
    _run_git(resolved_repo, *command)
    _write_repo_layout(resolved_repo)
    _run_git(resolved_repo, "reflog", "expire", "--expire=now", "--all")
    _run_git(resolved_repo, "gc", "--prune=now", "--aggressive")

    pushed = False
    if push and _remote_branch_exists(resolved_repo, remote, branch):
        _run_git(resolved_repo, "fetch", remote, branch)
        _run_git_force_with_lease(resolved_repo, remote=remote, branch=branch)
        pushed = True
    commit_hash = _run_git_output(resolved_repo, "rev-parse", "HEAD").strip()
    after_git_size = _git_count_objects(resolved_repo)
    return GitSyncCleanupResult(
        repo_path=resolved_repo,
        mode="rewrite-history",
        dry_run=False,
        removed_paths=removed_paths,
        backup_bundle=backup_bundle,
        committed=True,
        commit_hash=commit_hash,
        pushed=pushed,
        before_git_size=before_git_size,
        after_git_size=after_git_size,
    )


def install_git_hooks(
    repo_path: Path,
    *,
    toktrail_command: tuple[str, ...] = ("toktrail",),
    config_path: Path | None = None,
    db_path: Path | None = None,
    force: bool = False,
) -> GitHookInstallResult:
    resolved_repo = _require_repo(repo_path)
    hooks_dir = resolved_repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_script = _render_import_local_hook_script(
        toktrail_command=toktrail_command,
        config_path=config_path,
        db_path=db_path,
        repo_path=resolved_repo,
    )

    installed: list[str] = []
    skipped: list[str] = []
    overwritten: list[str] = []
    for hook_name in _MANAGED_HOOKS:
        hook_path = hooks_dir / hook_name
        if hook_path.exists():
            existing = hook_path.read_text(encoding="utf-8")
            if _is_managed_hook(existing):
                if existing == hook_script:
                    skipped.append(hook_name)
                    continue
                hook_path.write_text(hook_script, encoding="utf-8")
                hook_path.chmod(0o755)
                overwritten.append(hook_name)
                continue
            if not force:
                sample_path = hook_path.with_suffix(
                    f"{hook_path.suffix}.toktrail.sample"
                )
                sample_path.write_text(hook_script, encoding="utf-8")
                skipped.append(hook_name)
                continue
            hook_path.write_text(hook_script, encoding="utf-8")
            hook_path.chmod(0o755)
            overwritten.append(hook_name)
            continue

        hook_path.write_text(hook_script, encoding="utf-8")
        hook_path.chmod(0o755)
        installed.append(hook_name)

    return GitHookInstallResult(
        repo_path=resolved_repo,
        installed=tuple(installed),
        skipped=tuple(skipped),
        overwritten=tuple(overwritten),
    )


def uninstall_git_hooks(repo_path: Path) -> GitHookInstallResult:
    resolved_repo = _require_repo(repo_path)
    hooks_dir = resolved_repo / ".git" / "hooks"
    installed: list[str] = []
    skipped: list[str] = []
    overwritten: list[str] = []
    for hook_name in _MANAGED_HOOKS:
        hook_path = hooks_dir / hook_name
        if not hook_path.exists():
            skipped.append(hook_name)
            continue
        content = hook_path.read_text(encoding="utf-8")
        if not _is_managed_hook(content):
            skipped.append(hook_name)
            continue
        hook_path.unlink()
        overwritten.append(hook_name)
    return GitHookInstallResult(
        repo_path=resolved_repo,
        installed=tuple(installed),
        skipped=tuple(skipped),
        overwritten=tuple(overwritten),
    )


def git_hooks_status(repo_path: Path) -> GitHookStatus:
    resolved_repo = _require_repo(repo_path)
    hooks_dir = resolved_repo / ".git" / "hooks"
    status: dict[str, str] = {}
    for hook_name in _MANAGED_HOOKS:
        hook_path = hooks_dir / hook_name
        if not hook_path.exists():
            status[hook_name] = "missing"
            continue
        content = hook_path.read_text(encoding="utf-8")
        status[hook_name] = "installed" if _is_managed_hook(content) else "foreign"
    return GitHookStatus(repo_path=resolved_repo, hooks=status)


def _write_repo_layout(repo_path: Path) -> None:
    (repo_path / "meta").mkdir(parents=True, exist_ok=True)
    format_path = repo_path / "meta" / "format.json"
    if not format_path.exists():
        format_path.write_text(
            json.dumps(
                {
                    "format": GIT_SYNC_FORMAT,
                    "state_format": _STATE_FORMAT,
                    "created_by": "toktrail",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    gitignore_path = repo_path / ".gitignore"
    gitignore_lines = [
        "*.tmp",
        "*.lock",
        ".DS_Store",
        "toktrail.db",
        "toktrail.db-wal",
        "toktrail.db-shm",
        "*.sqlite",
        "*.sqlite3",
        "*.tar.gz",
        "archives/",
        ".state.staging.*",
    ]
    if not gitignore_path.exists():
        gitignore_path.write_text("\n".join(gitignore_lines) + "\n", encoding="utf-8")
    gitattributes_path = repo_path / ".gitattributes"
    if not gitattributes_path.exists():
        gitattributes_path.write_text(
            "\n".join(
                (
                    "*.json text eol=lf",
                    "*.jsonl text eol=lf",
                    "*.toml text eol=lf",
                    "*.md text eol=lf",
                    "*.db binary",
                    "*.sqlite binary",
                    "*.sqlite3 binary",
                    "*.db-wal binary",
                    "*.db-shm binary",
                    "*.tar.gz binary",
                )
            )
            + "\n",
            encoding="utf-8",
        )

    readme_path = repo_path / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
            "# Toktrail state sync\n\n"
            "This repo stores toktrail text state files under `state/`.\n"
            "Do not commit live sqlite state files (`toktrail.db*`).\n",
            encoding="utf-8",
        )


def _render_import_local_hook_script(
    *,
    toktrail_command: tuple[str, ...],
    config_path: Path | None,
    db_path: Path | None,
    repo_path: Path,
) -> str:
    if not toktrail_command:
        msg = "toktrail_command must contain at least one token."
        raise ValueError(msg)
    command_parts = list(toktrail_command)
    if db_path is not None:
        command_parts.extend(("--db", str(db_path.expanduser())))
    if config_path is not None:
        command_parts.extend(("--config", str(config_path.expanduser())))
    command_parts.extend(
        (
            "sync",
            "git",
            "import-local",
            "--repo",
            str(repo_path.expanduser()),
            "--quiet",
        )
    )
    command = shlex.join(command_parts)
    return f"#!/bin/sh\n{_HOOK_MARKER}\nTOKTRAIL_GIT_HOOK=1 exec {command}\n"


def _is_managed_hook(content: str) -> bool:
    return _HOOK_MARKER in content


def _require_repo(repo_path: Path) -> Path:
    resolved = repo_path.expanduser()
    if not (resolved / ".git").is_dir():
        msg = f"Not a git repository: {resolved}"
        raise ValueError(msg)
    return resolved


def _repo_relpath(path: Path, repo_path: Path) -> str:
    """Return a Git-compatible repo-relative path.

    pathlib uses backslashes in stringified Windows paths, but Git porcelain
    output and pathspecs are slash-separated on every platform. Keep all
    internally compared/stored repo paths in POSIX form so dirty-path checks,
    manifests, and import fingerprints are stable across Linux/macOS/Windows.
    """

    return path.resolve().relative_to(repo_path.resolve()).as_posix()


def _require_git_sync_repo_format(repo_path: Path) -> None:
    format_path = repo_path / "meta" / "format.json"
    if not format_path.is_file():
        msg = f"Not a toktrail git sync repository: {repo_path}"
        raise ValueError(msg)
    payload = json.loads(format_path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("format") not in _SUPPORTED_GIT_SYNC_FORMATS
    ):
        msg = f"Not a toktrail git sync repository: {repo_path}"
        raise ValueError(msg)


def _set_remote_url(repo_path: Path, remote: str, remote_url: str) -> None:
    remotes = _run_git_output(repo_path, "remote").splitlines()
    if remote in remotes:
        _run_git(repo_path, "remote", "set-url", remote, remote_url)
    else:
        _run_git(repo_path, "remote", "add", remote, remote_url)


def _ensure_branch(repo_path: Path, branch: str) -> None:
    if _current_symbolic_branch(repo_path) == branch:
        return
    if _local_branch_exists(repo_path, branch):
        _run_git(repo_path, "checkout", branch)
        return
    remote_ref = _remote_branch_ref(repo_path, branch)
    if remote_ref is not None:
        _run_git(repo_path, "checkout", "-B", branch, "--track", remote_ref)
        return
    _run_git(repo_path, "checkout", "-b", branch)


def _current_symbolic_branch(repo_path: Path) -> str | None:
    try:
        text = _run_git_output(
            repo_path,
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
        ).strip()
    except ValueError:
        return None
    return text or None


def _local_branch_exists(repo_path: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _remote_branch_ref(repo_path: Path, branch: str) -> str | None:
    refs = _run_git_output(repo_path, "for-each-ref", "--format=%(refname:short)")
    for line in refs.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.endswith(f"/{branch}") and "/" in text:
            remote, _, name = text.partition("/")
            if remote and name == branch and remote != branch:
                return f"{remote}/{branch}"
    return None


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        msg = "git executable not found in PATH."
        raise ValueError(msg) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        msg = f"git {' '.join(args)} failed: {detail}"
        raise ValueError(msg) from exc


def _run_git_output(repo_path: Path, *args: str) -> str:
    return _run_git(repo_path, *args).stdout


def _current_branch(repo_path: Path) -> str | None:
    try:
        branch = _run_git_output(repo_path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except ValueError:
        return None
    return branch or None


def _remote_url(repo_path: Path, remote: str) -> str | None:
    try:
        url = _run_git_output(repo_path, "remote", "get-url", remote).strip()
    except ValueError:
        return None
    return url or None


def _repo_is_dirty(repo_path: Path) -> bool:
    status = _run_git_output(repo_path, "status", "--porcelain")
    return bool(status.strip())


def _path_size(path: Path, *, repo_path: Path) -> GitSyncCleanupSize:
    resolved = path.resolve()
    apparent = 0
    physical = 0
    count = 0
    if resolved.is_file():
        stat_result = resolved.stat()
        apparent = stat_result.st_size
        blocks = getattr(stat_result, "st_blocks", 0)
        physical = blocks * 512 if blocks else stat_result.st_size
        count = 1
    else:
        for item in resolved.rglob("*"):
            if not item.is_file():
                continue
            stat_result = item.stat()
            apparent += stat_result.st_size
            blocks = getattr(stat_result, "st_blocks", 0)
            physical += blocks * 512 if blocks else stat_result.st_size
            count += 1
    return GitSyncCleanupSize(
        path=_repo_relpath(resolved, repo_path),
        apparent_bytes=apparent,
        physical_bytes=physical,
        file_count=count,
    )


def _find_cleanup_candidates(repo_path: Path) -> list[Path]:
    matches: list[Path] = []
    for relpath in _OBSOLETE_FIXED_PATHS:
        target = (repo_path / relpath).resolve()
        if target.exists() and _path_is_within_repo(target, repo_path=repo_path):
            matches.append(target)
    for pattern in _OBSOLETE_GLOBS:
        for target in repo_path.rglob(pattern):
            resolved = target.resolve()
            if not _path_is_within_repo(resolved, repo_path=repo_path):
                continue
            if ".git" in resolved.parts:
                continue
            matches.append(resolved)
    return _dedupe_cleanup_paths(matches, repo_path=repo_path)


def _find_live_sqlite_files(repo_path: Path) -> list[Path]:
    matches = _find_state_db_files(repo_path)
    for pattern in _LIVE_SQLITE_GLOBS:
        for target in repo_path.rglob(pattern):
            resolved = target.resolve()
            if not resolved.is_file():
                continue
            if not _path_is_within_repo(resolved, repo_path=repo_path):
                continue
            if ".git" in resolved.parts:
                continue
            matches.append(resolved)
    unique: dict[str, Path] = {}
    for path in matches:
        unique[_repo_relpath(path, repo_path)] = path
    return [unique[key] for key in sorted(unique)]


def _cleanup_recommendation(
    cleanup_paths: list[Path],
    live_sqlite_paths: tuple[str, ...],
) -> str:
    if any(
        path.name == "archives" or path.name.endswith(".tar.gz")
        for path in cleanup_paths
    ):
        return "reset-history"
    if live_sqlite_paths or cleanup_paths:
        return "working-tree"
    return "none"


def _cleanup_allowed_dirty_paths(
    repo_path: Path,
    paths: list[Path],
) -> tuple[set[str], tuple[str, ...]]:
    allowed_relpaths: set[str] = set()
    allowed_prefixes: list[str] = []
    for path in paths:
        relpath = _repo_relpath(path, repo_path)
        allowed_relpaths.add(relpath)
        if path.is_dir():
            allowed_prefixes.append(f"{relpath.rstrip('/')}/")
    return allowed_relpaths, tuple(allowed_prefixes)


def _path_is_within_repo(path: Path, *, repo_path: Path) -> bool:
    resolved_repo = repo_path.resolve()
    resolved_path = path.resolve()
    return resolved_path == resolved_repo or resolved_repo in resolved_path.parents


def _dedupe_cleanup_paths(paths: list[Path], *, repo_path: Path) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in paths:
        relpath = _repo_relpath(path, repo_path)
        unique[relpath] = path
    ordered = [
        unique[key] for key in sorted(unique, key=lambda item: (item.count("/"), item))
    ]
    deduped: list[Path] = []
    for path in ordered:
        if any(path == parent or parent in path.parents for parent in deduped):
            continue
        deduped.append(path)
    return deduped


def _remove_cleanup_paths(repo_path: Path, paths: list[Path]) -> None:
    for path in paths:
        relpath = _repo_relpath(path, repo_path)
        if path.is_dir():
            subprocess.run(
                ["git", "rm", "-r", "-f", "--ignore-unmatch", relpath],
                cwd=repo_path,
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(
                ["git", "rm", "-f", "--ignore-unmatch", relpath],
                cwd=repo_path,
                check=False,
                capture_output=True,
                text=True,
            )
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _allowed_reset_paths(
    repo_path: Path,
    *,
    tracked_config_paths: tuple[Path, ...],
) -> tuple[str, ...]:
    allowed = list(_BASE_ALLOWED_RESET_PATHS)
    tracked_relpaths, tracked_prefixes = _repo_relative_tracked_paths(
        repo_path,
        tracked_config_paths,
    )
    include_config = bool(tracked_relpaths or tracked_prefixes) or _has_tracked_prefix(
        repo_path,
        "config/",
    )
    if include_config:
        allowed.append("config")
    return tuple(allowed)


def _remove_non_reset_paths(repo_path: Path, *, allowed_roots: tuple[str, ...]) -> None:
    allowed = set(allowed_roots)
    for child in sorted(repo_path.iterdir(), key=lambda item: item.name):
        if child.name == ".git":
            continue
        if child.name in allowed:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _stage_reset_paths(repo_path: Path, *, allowed_roots: tuple[str, ...]) -> None:
    stageable = [relpath for relpath in allowed_roots if (repo_path / relpath).exists()]
    if stageable:
        _run_git(repo_path, "add", "-A", *stageable)


def _path_is_tracked(repo_path: Path, relpath: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relpath],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _has_tracked_prefix(repo_path: Path, prefix: str) -> bool:
    output = _run_git_output(repo_path, "ls-files", prefix)
    return bool(output.strip())


def _clear_git_index(repo_path: Path) -> None:
    result = subprocess.run(
        ["git", "rm", "-r", "--cached", "."],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 128}:
        stderr = (result.stderr or "").strip()
        msg = f"git rm -r --cached . failed: {stderr or result.stdout.strip()}"
        raise ValueError(msg)


def _create_backup_bundle(repo_path: Path) -> Path:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate = repo_path.parent / f"{repo_path.name}-backup-{timestamp}.bundle"
    index = 1
    while candidate.exists():
        candidate = (
            repo_path.parent / f"{repo_path.name}-backup-{timestamp}-{index}.bundle"
        )
        index += 1
    _run_git(repo_path, "bundle", "create", str(candidate), "--all")
    return candidate


def _unique_branch_name(repo_path: Path, prefix: str) -> str:
    candidate = prefix
    counter = 1
    while _local_branch_exists(repo_path, candidate):
        counter += 1
        candidate = f"{prefix}-{counter}"
    return candidate


def _remote_branch_exists(repo_path: Path, remote: str, branch: str) -> bool:
    if _remote_url(repo_path, remote) is None:
        return False
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", remote, branch],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _run_git_force_with_lease(repo_path: Path, *, remote: str, branch: str) -> None:
    _run_git(repo_path, "push", "--force-with-lease", remote, branch)


def _filter_repo_available() -> bool:
    return shutil.which("git-filter-repo") is not None


def _git_count_objects(repo_path: Path) -> dict[str, str]:
    output = _run_git_output(repo_path, "count-objects", "-vH")
    values: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip()
    return values


def _repo_relative_tracked_paths(
    repo_path: Path,
    paths: tuple[Path, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    relpaths: list[str] = []
    prefixes: list[str] = []
    parent_prefixes: list[str] = []
    resolved_repo = repo_path.resolve()

    def _record_parent_prefixes(path_text: str) -> None:
        parent = Path(path_text).parent
        while str(parent) not in {"", "."}:
            parent_prefixes.append(f"{parent.as_posix().rstrip('/')}/")
            parent = parent.parent

    for path in paths:
        expanded = path.expanduser()
        resolved = expanded.resolve()
        if resolved_repo != resolved and resolved_repo not in resolved.parents:
            continue
        relpath = resolved.relative_to(resolved_repo).as_posix()
        if expanded.exists():
            if expanded.is_dir():
                prefix = f"{relpath.rstrip('/')}/"
                prefixes.append(prefix)
                _record_parent_prefixes(prefix.rstrip("/"))
            else:
                relpaths.append(relpath)
                _record_parent_prefixes(relpath)
            continue
        if expanded.suffix.lower() == ".toml":
            relpaths.append(relpath)
            _record_parent_prefixes(relpath)
            continue
        prefix = f"{relpath.rstrip('/')}/"
        prefixes.append(prefix)
        _record_parent_prefixes(prefix.rstrip("/"))
    return tuple(dict.fromkeys(relpaths)), tuple(
        dict.fromkeys((*prefixes, *parent_prefixes))
    )


def _has_uncommitted_disallowed_changes(
    repo_path: Path,
    *,
    allowed_prefixes: tuple[str, ...],
    allowed_relpaths: set[str] | frozenset[str] = frozenset(),
) -> bool:
    for relpath in _dirty_paths(repo_path):
        if relpath in allowed_relpaths:
            continue
        if any(
            relpath == prefix or relpath.startswith(prefix)
            for prefix in allowed_prefixes
        ):
            continue
        return True
    return False


def _dirty_paths(repo_path: Path) -> list[str]:
    output = _run_git_output(repo_path, "status", "--porcelain")
    paths: list[str] = []
    for line in output.splitlines():
        text = line.strip()
        if not text:
            continue
        path = text[3:] if len(text) >= 4 else text
        if " -> " in path:
            path = path.split(" -> ", maxsplit=1)[1]
        paths.append(path.strip())
    return paths


def _repo_has_staged_changes(repo_path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_path,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        msg = "git executable not found in PATH."
        raise ValueError(msg) from exc
    if result.returncode not in {0, 1}:
        stderr = result.stderr.strip()
        msg = f"git diff --cached --quiet failed: {stderr}"
        raise ValueError(msg)
    return result.returncode == 1


def _ahead_behind(repo_path: Path) -> tuple[int | None, int | None]:
    try:
        text = _run_git_output(
            repo_path,
            "rev-list",
            "--left-right",
            "--count",
            "@{upstream}...HEAD",
        )
    except ValueError:
        return None, None
    pieces = text.strip().split()
    if len(pieces) != 2:
        return None, None
    behind = int(pieces[0])
    ahead = int(pieces[1])
    return ahead, behind


def _find_state_db_files(repo_path: Path) -> list[Path]:
    matches: list[Path] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        if path.name in _STATE_DB_FILE_NAMES:
            matches.append(path)
    matches.sort()
    return matches


def _fail_if_contains_state_db_files(repo_path: Path) -> None:
    matches = _find_state_db_files(repo_path)
    if not matches:
        return
    listed = ", ".join(_repo_relpath(path, repo_path) for path in matches)
    msg = (
        "Git sync repo contains live sqlite state files "
        f"({listed}). Remove them from the repo before continuing."
    )
    raise ValueError(msg)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def list_state_files(
    repo_path: Path, *, state_dir: str = DEFAULT_STATE_DIR
) -> list[Path]:
    resolved_repo = _require_repo(repo_path)
    base = resolved_repo / state_dir
    if not base.exists():
        return []
    files = [path for path in base.rglob("*") if path.is_file()]
    files.sort()
    return files


def import_repo_state(
    db_path: Path,
    repo_path: Path,
    *,
    dry_run: bool,
    state_dir: str = DEFAULT_STATE_DIR,
    on_conflict: ConflictMode = "fail",
    remote_active: RemoteActiveMode = "close-at-export",
) -> GitSyncImportResult:
    resolved_repo = _require_repo(repo_path)
    state_root = resolved_repo / state_dir
    if not state_root.exists():
        return GitSyncImportResult(
            repo_path=resolved_repo,
            state_files_seen=0,
            state_imported=False,
            state_skipped=False,
            import_results=(),
        )
    state_files = list_state_files(resolved_repo, state_dir=state_dir)
    state_fingerprint = _state_files_fingerprint(state_root, state_files)
    conn = db.connect(db_path.expanduser())
    try:
        db.migrate(conn)
        if state_fingerprint and db.has_imported_sync_archive(conn, state_fingerprint):
            return GitSyncImportResult(
                repo_path=resolved_repo,
                state_files_seen=len(state_files),
                state_imported=False,
                state_skipped=True,
                import_results=(),
            )
    finally:
        conn.close()
    with tempfile.TemporaryDirectory(
        prefix="toktrail-sync-state-import-"
    ) as temp_dir_text:
        temp_db_path = Path(temp_dir_text) / "imported-state.sqlite"
        context = _load_text_state_into_db(state_root, temp_db_path)
        result = merge_imported_state_db(
            target_db_path=db_path.expanduser(),
            imported_db_path=temp_db_path,
            context=context,
            dry_run=dry_run,
            on_conflict=on_conflict,
            remote_active=remote_active,
        )
    if not dry_run and state_fingerprint:
        conn = db.connect(db_path.expanduser())
        try:
            db.migrate(conn)
            db.record_imported_sync_archive(
                conn,
                archive_sha256=state_fingerprint,
                source_machine_id=context.imported_machine_id,
                exported_at_ms=context.imported_at_ms,
                archive_path=_repo_relpath(state_root, resolved_repo),
                result_json=json.dumps(
                    _state_import_result_dict(result),
                    sort_keys=True,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return GitSyncImportResult(
        repo_path=resolved_repo,
        state_files_seen=len(state_files),
        state_imported=True,
        state_skipped=False,
        import_results=(result,),
    )


def _export_text_state(
    db_path: Path,
    state_root: Path,
    *,
    redact_raw_json: bool,
) -> StateExportResult:
    from toktrail.config import load_machine_config

    _cleanup_stale_staging_dirs(state_root.parent)

    with tempfile.TemporaryDirectory(prefix="toktrail-git-sync-export-") as temp_dir:
        snapshot_path = Path(temp_dir) / "snapshot.sqlite"
        src = db.connect(db_path)
        src.row_factory = sqlite3.Row
        dest = sqlite3.connect(snapshot_path)
        dest.row_factory = sqlite3.Row
        staged_root: Path | None = None
        try:
            db.migrate(src)
            machine_config = load_machine_config().config
            db.apply_local_machine_config(src, machine_config)
            src.commit()
            src.backup(dest)
            if redact_raw_json:
                dest.execute("UPDATE usage_events SET raw_json = NULL")
            dest.commit()
            exported_at_ms = int(time() * 1000)
            machine = dest.execute(
                "SELECT machine_id, name FROM machines "
                "WHERE is_local = 1 "
                "ORDER BY updated_at_ms DESC LIMIT 1"
            ).fetchone()
            if machine is None:
                msg = "No local machine row found for git sync export."
                raise ValueError(msg)
            machine_id = str(machine["machine_id"])
            machine_name = machine["name"] if isinstance(machine["name"], str) else None
            raw_json_rows = int(
                dest.execute(
                    "SELECT COUNT(*) AS count FROM usage_events "
                    "WHERE raw_json IS NOT NULL"
                ).fetchone()["count"]
            )
            run_sync_by_id = {
                int(row["id"]): str(row["sync_id"])
                for row in dest.execute("SELECT id, sync_id FROM runs").fetchall()
            }
            staged_root = Path(
                tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=str(state_root.parent))
            )
            counts: dict[str, int] = {}
            manifest_tables: dict[str, dict[str, object]] = {}
            for table in _STATE_TABLES:
                rows = _fetch_export_rows(dest, table)
                counts[table] = len(rows)
                if table == "usage_events":
                    manifest_tables[table] = _write_usage_event_session_files(
                        staged_root,
                        rows,
                        fallback_origin_machine_id=machine_id,
                    )
                    continue
                if table == "run_events":
                    manifest_tables[table] = _write_run_event_files(
                        staged_root,
                        rows,
                        run_sync_by_id=run_sync_by_id,
                    )
                    continue
                files: list[dict[str, object]] = []
                for row in rows:
                    record = {key: row[key] for key in row.keys()}
                    relpath = _state_record_relpath(table, record)
                    target = staged_root / relpath
                    payload = (
                        json.dumps(
                            record,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                        + b"\n"
                    )
                    _write_if_changed(target, payload)
                    files.append(
                        {
                            "path": relpath,
                            "sha256": _sha256_file(target),
                        }
                    )
                manifest_tables[table] = {
                    "rows": len(rows),
                    "files": len(files),
                    "entries": files,
                }
            manifest = {
                "format": _STATE_FORMAT,
                "schema_version": db.SCHEMA_VERSION,
                "exported_at_ms": exported_at_ms,
                "machine_id": machine_id,
                "machine_name": machine_name,
                "raw_json_redacted": redact_raw_json,
                "tables": manifest_tables,
            }
            _write_if_changed(
                staged_root / "manifest.json",
                (
                    json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
                    + b"\n"
                ),
            )
            _sync_state_directory(staged_root, state_root)
        finally:
            if staged_root is not None:
                shutil.rmtree(staged_root, ignore_errors=True)
            dest.close()
            src.close()

    return StateExportResult(
        archive_path=state_root,
        exported_at_ms=exported_at_ms,
        schema_version=db.SCHEMA_VERSION,
        machine_id=machine_id,
        machine_name=machine_name,
        run_count=counts["runs"],
        source_session_count=counts["source_sessions"],
        usage_event_count=counts["usage_events"],
        run_event_count=counts["run_events"],
        raw_json_count=raw_json_rows,
    )


def _sync_state_directory(staged_root: Path, target_root: Path) -> None:
    target_root.parent.mkdir(parents=True, exist_ok=True)
    if target_root.is_symlink() or target_root.is_file():
        target_root.unlink()
    target_root.mkdir(parents=True, exist_ok=True)

    old_paths = _manifest_relative_paths(target_root)
    new_paths = {
        path.relative_to(staged_root).as_posix()
        for path in staged_root.rglob("*")
        if path.is_file()
    }
    for relpath in sorted(new_paths):
        _copy_file_if_changed(staged_root / relpath, target_root / relpath)
    for relpath in sorted(old_paths - new_paths, reverse=True):
        stale_path = target_root / relpath
        if stale_path.exists():
            stale_path.unlink()
            _prune_empty_parents(stale_path.parent, stop_at=target_root)


def _manifest_relative_paths(state_root: Path) -> set[str]:
    if not state_root.exists() or not (state_root / "manifest.json").is_file():
        return set()
    try:
        manifest = _load_state_manifest(state_root)
    except ValueError:
        return set()
    paths = {"manifest.json"}
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        return paths
    for table_data in tables.values():
        if not isinstance(table_data, dict):
            continue
        entries = table_data.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            relpath = entry.get("path")
            if isinstance(relpath, str) and relpath:
                paths.add(relpath)
    return paths


def _copy_file_if_changed(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if (
        target.exists()
        and target.is_file()
        and _sha256_file(source) == _sha256_file(target)
    ):
        return
    shutil.copy2(source, target)


def _prune_empty_parents(path: Path, *, stop_at: Path) -> None:
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _load_text_state_into_db(
    state_root: Path, temp_db_path: Path
) -> ImportedStateContext:
    manifest = _load_state_manifest(state_root)
    conn = sqlite3.connect(temp_db_path)
    conn.row_factory = sqlite3.Row
    try:
        db.migrate(conn)
        _validate_state_manifest(state_root, manifest)
        for table in _STATE_TABLES:
            rows = _load_state_table_records(state_root, manifest, table)
            if not rows:
                continue
            valid_columns = _table_columns(conn, table)
            columns = [column for column in valid_columns if column in rows[0]]
            col_sql = ", ".join(columns)
            val_sql = ", ".join("?" for _ in columns)
            for row in rows:
                values = [row.get(column) for column in columns]
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({val_sql})",
                    values,
                )
        conn.commit()
        machine = conn.execute(
            "SELECT machine_id, name FROM machines "
            "ORDER BY is_local DESC, updated_at_ms DESC LIMIT 1"
        ).fetchone()
        if machine is None:
            msg = f"State files missing machines row under {state_root}"
            raise ValueError(msg)
        return ImportedStateContext(
            source_path=state_root,
            imported_at_ms=int(time() * 1000),
            imported_machine_id=str(machine["machine_id"]),
            imported_machine_name=(
                machine["name"] if isinstance(machine["name"], str) else None
            ),
            schema_version=db.SCHEMA_VERSION,
            source_format=str(manifest["format"]),
        )
    finally:
        conn.close()


def _fetch_export_rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    order_by = {
        "machines": "machine_id",
        "areas": "sync_id",
        "area_session_assignments": "sync_id",
        "machine_active_areas": "machine_id",
        "runs": "sync_id",
        "source_sessions": "sync_id",
        "source_session_metadata": "origin_machine_id, harness, source_session_id",
        "usage_events": (
            "origin_machine_id, harness, source_session_id, "
            "created_ms, source_row_id, global_dedup_key, id"
        ),
        "run_events": "tracking_session_id, created_at_ms, usage_event_id",
    }[table]
    return conn.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()


def _write_usage_event_session_files(
    staged_root: Path,
    rows: list[sqlite3.Row],
    *,
    fallback_origin_machine_id: str,
) -> dict[str, object]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in rows:
        record = {key: row[key] for key in row.keys()}
        key = _usage_event_session_key(
            record,
            fallback_origin_machine_id=fallback_origin_machine_id,
        )
        grouped.setdefault(key, []).append(record)

    entries: list[dict[str, object]] = []
    for (origin, harness, source_session_id), records in sorted(grouped.items()):
        records.sort(
            key=lambda item: (
                _int_sort_value(item.get("created_ms")),
                str(item.get("source_row_id") or ""),
                str(item.get("global_dedup_key") or ""),
                _int_sort_value(item.get("id")),
            )
        )
        relpath = _usage_session_record_relpath(
            origin_machine_id=origin,
            harness=harness,
            source_session_id=source_session_id,
        )
        target = staged_root / relpath
        header = {
            "format": _USAGE_SESSION_FORMAT,
            "origin_machine_id": origin,
            "harness": harness,
            "source_session_id": source_session_id,
        }
        _write_if_changed(target, _grouped_records_payload(header, records))
        entries.append(
            {
                "path": relpath,
                "sha256": _sha256_file(target),
                "rows": len(records),
                "group": {
                    "origin_machine_id": origin,
                    "harness": harness,
                    "source_session_id": source_session_id,
                },
            }
        )

    return {
        "rows": len(rows),
        "files": len(entries),
        "encoding": "jsonl-session-v1",
        "entries": entries,
    }


def _usage_event_session_key(
    record: dict[str, object],
    *,
    fallback_origin_machine_id: str,
) -> tuple[str, str, str]:
    harness = str(record["harness"])
    source_session_id = str(record["source_session_id"])
    origin_machine_id = str(
        record.get("origin_machine_id") or fallback_origin_machine_id
    )
    return origin_machine_id, harness, source_session_id


def _usage_session_record_relpath(
    *,
    origin_machine_id: str,
    harness: str,
    source_session_id: str,
) -> str:
    origin = _safe_segment(origin_machine_id)
    harness_segment = _safe_segment(harness)
    digest = _hash_key(f"{origin_machine_id}\0{harness}\0{source_session_id}")
    return f"usage-events/{harness_segment}/{origin}/{digest}.jsonl"


def _write_run_event_files(
    staged_root: Path,
    rows: list[sqlite3.Row],
    *,
    run_sync_by_id: dict[int, str],
) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        record = {key: row[key] for key in row.keys()}
        run_id = int(record["tracking_session_id"])
        run_sync_id = str(run_sync_by_id.get(run_id, f"run-{run_id}"))
        grouped.setdefault(run_sync_id, []).append(record)

    entries: list[dict[str, object]] = []
    for run_sync_id, records in sorted(grouped.items()):
        records.sort(
            key=lambda item: (
                _int_sort_value(item.get("created_at_ms")),
                _int_sort_value(item.get("usage_event_id")),
                _int_sort_value(item.get("tracking_session_id")),
            )
        )
        relpath = _run_event_records_relpath(run_sync_id=run_sync_id)
        target = staged_root / relpath
        header = {
            "format": _RUN_EVENTS_FORMAT,
            "run_sync_id": run_sync_id,
        }
        _write_if_changed(target, _grouped_records_payload(header, records))
        entries.append(
            {
                "path": relpath,
                "sha256": _sha256_file(target),
                "rows": len(records),
                "group": {"run_sync_id": run_sync_id},
            }
        )

    return {
        "rows": len(rows),
        "files": len(entries),
        "encoding": "jsonl-run-v1",
        "entries": entries,
    }


def _run_event_records_relpath(*, run_sync_id: str) -> str:
    return f"run-events/{_safe_segment(run_sync_id)}.jsonl"


def _grouped_records_payload(
    header: Mapping[str, object],
    records: list[dict[str, object]],
) -> bytes:
    lines = [
        json.dumps(header, sort_keys=True, separators=(",", ":")),
        *(
            json.dumps(
                {"record": record},
                sort_keys=True,
                separators=(",", ":"),
            )
            for record in records
        ),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _int_sort_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        return 0
    return int(str(value))


def _state_record_relpath(
    table: str,
    record: dict[str, object],
) -> str:
    if table == "machines":
        return f"machines/{_safe_segment(str(record['machine_id']))}.json"
    if table == "areas":
        return f"areas/{_safe_segment(str(record['sync_id']))}.json"
    if table == "area_session_assignments":
        return f"area-session-assignments/{_safe_segment(str(record['sync_id']))}.json"
    if table == "machine_active_areas":
        return f"machine-active-areas/{_safe_segment(str(record['machine_id']))}.json"
    if table == "runs":
        return f"runs/{_safe_segment(str(record['sync_id']))}.json"
    if table == "source_sessions":
        return f"source-sessions/{_safe_segment(str(record['sync_id']))}.json"
    if table == "source_session_metadata":
        origin = _safe_segment(str(record["origin_machine_id"]))
        harness = _safe_segment(str(record["harness"]))
        session_hash = _hash_key(str(record["source_session_id"]))
        return f"source-session-metadata/{origin}/{harness}/{session_hash}.json"
    if table == "usage_events":
        origin_machine_id, harness, source_session_id = _usage_event_session_key(
            record,
            fallback_origin_machine_id=str(record.get("origin_machine_id") or ""),
        )
        return _usage_session_record_relpath(
            origin_machine_id=origin_machine_id,
            harness=harness,
            source_session_id=source_session_id,
        )
    if table == "run_events":
        return _run_event_records_relpath(
            run_sync_id=str(record.get("tracking_session_id") or "run"),
        )
    msg = f"Unsupported state table: {table}"
    raise ValueError(msg)


def _safe_segment(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_")


def _hash_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _load_state_manifest(state_root: Path) -> dict[str, object]:
    manifest_path = state_root / "manifest.json"
    if not manifest_path.exists():
        msg = f"State manifest missing: {manifest_path}"
        raise ValueError(msg)
    payload: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"State manifest is not an object: {manifest_path}"
        raise ValueError(msg)
    return payload


def _validate_state_manifest(state_root: Path, manifest: dict[str, object]) -> None:
    if manifest.get("format") not in _SUPPORTED_STATE_FORMATS:
        msg = f"Unsupported state format: {manifest.get('format')!r}"
        raise ValueError(msg)
    schema_version = _optional_manifest_int(manifest, "schema_version")
    if schema_version != db.SCHEMA_VERSION:
        msg = (
            "State schema version mismatch: "
            f"{schema_version!r} (expected {db.SCHEMA_VERSION})"
        )
        raise ValueError(msg)
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        msg = "State manifest missing tables object."
        raise ValueError(msg)
    for table in _STATE_TABLES:
        table_meta = tables.get(table)
        if not isinstance(table_meta, dict):
            msg = f"State manifest missing table metadata: {table}"
            raise ValueError(msg)
        entries = table_meta.get("entries")
        if not isinstance(entries, list):
            msg = f"State manifest table entries must be a list: {table}"
            raise ValueError(msg)
        for entry in entries:
            if not isinstance(entry, dict):
                msg = f"Invalid manifest entry for table {table}"
                raise ValueError(msg)
            relpath = entry.get("path")
            checksum = entry.get("sha256")
            if not isinstance(relpath, str) or not relpath:
                msg = f"Invalid manifest path entry for table {table}"
                raise ValueError(msg)
            if not isinstance(checksum, str) or not checksum:
                msg = f"Invalid manifest checksum entry for table {table}"
                raise ValueError(msg)
            target = state_root / relpath
            if not target.exists() or not target.is_file():
                msg = f"State file missing: {target}"
                raise ValueError(msg)
            if _sha256_file(target) != checksum:
                msg = f"State checksum mismatch: {target}"
                raise ValueError(msg)


def _load_state_table_records(
    state_root: Path,
    manifest: dict[str, object],
    table: str,
) -> list[dict[str, object]]:
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        return []
    table_meta = tables.get(table)
    if not isinstance(table_meta, dict):
        return []
    entries = table_meta.get("entries")
    if not isinstance(entries, list):
        return []
    rows: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        relpath = entry.get("path")
        if not isinstance(relpath, str) or not relpath:
            continue
        target = state_root / relpath
        if table == "usage_events":
            rows.extend(
                _load_grouped_state_records_file(
                    target,
                    header_format=_USAGE_SESSION_FORMAT,
                    header_name="Usage session",
                )
            )
            continue
        if table == "run_events":
            rows.extend(
                _load_grouped_state_records_file(
                    target,
                    header_format=_RUN_EVENTS_FORMAT,
                    header_name="Run event group",
                )
            )
            continue
        payload: object = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            msg = f"State record is not an object: {target}"
            raise ValueError(msg)
        rows.append(payload)
    return rows


def _load_grouped_state_records_file(
    path: Path,
    *,
    header_format: str,
    header_name: str,
) -> list[dict[str, object]]:
    lines = [
        (line_no, line)
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        )
        if line.strip()
    ]
    if not lines:
        msg = f"State file is empty: {path}"
        raise ValueError(msg)

    if len(lines) == 1:
        _, line = lines[0]
        payload = json.loads(line)
        if not isinstance(payload, dict):
            msg = f"State record is not an object: {path}"
            raise ValueError(msg)
        if payload.get("format") != header_format and "record" not in payload:
            return [payload]

    rows: list[dict[str, object]] = []
    header_seen = False
    for line_no, line in lines:
        payload = json.loads(line)
        if not isinstance(payload, dict):
            msg = f"State line is not an object: {path}:{line_no}"
            raise ValueError(msg)
        if payload.get("format") == header_format:
            if header_seen:
                msg = f"Duplicate {header_name.lower()} header: {path}:{line_no}"
                raise ValueError(msg)
            header_seen = True
            continue
        record = payload.get("record")
        if not isinstance(record, dict):
            msg = f"{header_name} line missing record: {path}:{line_no}"
            raise ValueError(msg)
        rows.append(record)

    if not header_seen:
        msg = f"{header_name} header missing: {path}"
        raise ValueError(msg)
    return rows


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return tuple(str(row[1]) for row in rows)


def _cleanup_stale_staging_dirs(parent: Path) -> None:
    if not parent.exists():
        return
    for path in parent.glob(f"{_STAGING_PREFIX}*"):
        if not path.is_dir():
            continue
        for nested in sorted(path.rglob("*"), reverse=True):
            if nested.is_file():
                nested.unlink()
            elif nested.is_dir():
                nested.rmdir()
        path.rmdir()


def _write_if_changed(path: Path, content: bytes) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def _state_files_fingerprint(state_root: Path, files: list[Path]) -> str:
    digest = sha256()
    for path in files:
        rel = path.relative_to(state_root).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest() if files else ""


def _state_export_result_dict(result: StateExportResult) -> dict[str, object]:
    return {
        "state_path": str(result.archive_path),
        "archive_path": str(result.archive_path),
        "exported_at_ms": result.exported_at_ms,
        "schema_version": result.schema_version,
        "machine_id": result.machine_id,
        "machine_name": result.machine_name,
        "run_count": result.run_count,
        "source_session_count": result.source_session_count,
        "usage_event_count": result.usage_event_count,
        "run_event_count": result.run_event_count,
        "raw_json_count": result.raw_json_count,
    }


def _state_import_result_dict(result: StateImportResult) -> dict[str, object]:
    return {
        "state_path": str(result.archive_path),
        "archive_path": str(result.archive_path),
        "dry_run": result.dry_run,
        "runs_inserted": result.runs_inserted,
        "runs_updated": result.runs_updated,
        "source_sessions_inserted": result.source_sessions_inserted,
        "source_sessions_updated": result.source_sessions_updated,
        "usage_events_inserted": result.usage_events_inserted,
        "usage_events_skipped": result.usage_events_skipped,
        "run_events_inserted": result.run_events_inserted,
        "conflicts": [
            {
                "kind": conflict.kind,
                "harness": conflict.harness,
                "global_dedup_key": conflict.global_dedup_key,
                "local_fingerprint": conflict.local_fingerprint,
                "imported_fingerprint": conflict.imported_fingerprint,
                "message": conflict.message,
            }
            for conflict in result.conflicts
        ],
    }


def _optional_manifest_str(manifest: dict[str, object], key: str) -> str | None:
    value = manifest.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _optional_manifest_int(manifest: dict[str, object], key: str) -> int | None:
    value = manifest.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
