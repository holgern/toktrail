from __future__ import annotations

import json
import shlex
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import time

from toktrail import db
from toktrail.sync import (
    MANIFEST_NAME,
    ConflictMode,
    ImportedStateContext,
    RemoteActiveMode,
    StateExportResult,
    StateImportResult,
    merge_imported_state_db,
)

GIT_SYNC_FORMAT = "toktrail.git-sync.v2"
DEFAULT_STATE_DIR = "state"
DEFAULT_ARCHIVE_DIR = DEFAULT_STATE_DIR
DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"
_STATE_DB_FILE_NAMES = frozenset({"toktrail.db", "toktrail.db-wal", "toktrail.db-shm"})
_HOOK_MARKER = "# toktrail-managed-hook v1"
_MANAGED_HOOKS = ("post-merge", "post-checkout", "post-rewrite")


@dataclass(frozen=True)
class GitSyncRepoStatus:
    repo_path: Path
    branch: str | None
    remote: str | None
    dirty: bool
    ahead: int | None
    behind: int | None
    archive_count: int
    pending_import_count: int
    state_db_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "branch": self.branch,
            "remote": self.remote,
            "dirty": self.dirty,
            "ahead": self.ahead,
            "behind": self.behind,
            "archive_count": self.archive_count,
            "pending_import_count": self.pending_import_count,
            "state_db_paths": list(self.state_db_paths),
        }


@dataclass(frozen=True)
class GitSyncImportResult:
    repo_path: Path
    archives_seen: int
    archives_imported: int
    archives_skipped: int
    import_results: tuple[StateImportResult, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
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
    archive_path: Path
    committed: bool
    pushed: bool
    commit_hash: str | None
    export_result: StateExportResult

    def as_dict(self) -> dict[str, object]:
        return {
            "repo_path": str(self.repo_path),
            "archive_path": str(self.archive_path),
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
    with tarfile.open(archive_path, "r:gz") as tar:
        member = tar.extractfile(MANIFEST_NAME)
        if member is None:
            msg = f"Archive missing {MANIFEST_NAME}: {archive_path}"
            raise ValueError(msg)
        try:
            payload: object = json.loads(member.read().decode("utf-8"))
        except json.JSONDecodeError as exc:
            msg = f"Invalid archive manifest JSON: {archive_path}"
            raise ValueError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"Archive manifest is not an object: {archive_path}"
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
    _write_repo_layout(resolved_repo)
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

    _run_git(resolved_repo, "add", "-A", str(state_root.relative_to(resolved_repo)))
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
        archive_path=state_root,
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
    archives = list_state_files(resolved_repo, state_dir=archive_dir)
    pending = 0

    state_db_paths = tuple(
        str(path.relative_to(resolved_repo))
        for path in _find_state_db_files(resolved_repo)
    )
    return GitSyncRepoStatus(
        repo_path=resolved_repo,
        branch=branch,
        remote=remote_url,
        dirty=dirty,
        ahead=ahead,
        behind=behind,
        archive_count=len(archives),
        pending_import_count=pending,
        state_db_paths=state_db_paths,
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
                    "state_format": "toktrail.text-state.v1",
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


def _set_remote_url(repo_path: Path, remote: str, remote_url: str) -> None:
    remotes = _run_git_output(repo_path, "remote").splitlines()
    if remote in remotes:
        _run_git(repo_path, "remote", "set-url", remote, remote_url)
    else:
        _run_git(repo_path, "remote", "add", remote, remote_url)


def _ensure_branch(repo_path: Path, branch: str) -> None:
    try:
        _run_git(repo_path, "checkout", branch)
    except ValueError:
        _run_git(repo_path, "checkout", "-b", branch)


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
        relpath = str(resolved.relative_to(resolved_repo))
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
    listed = ", ".join(str(path.relative_to(repo_path)) for path in matches)
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
            archives_seen=0,
            archives_imported=0,
            archives_skipped=0,
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
                archives_seen=len(state_files),
                archives_imported=0,
                archives_skipped=len(state_files),
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
                archive_path=str(state_root.relative_to(resolved_repo)),
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
        archives_seen=len(state_files),
        archives_imported=1,
        archives_skipped=0,
        import_results=(result,),
    )


def _export_text_state(
    db_path: Path,
    state_root: Path,
    *,
    redact_raw_json: bool,
) -> StateExportResult:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        db.migrate(conn)
        from toktrail.config import load_machine_config

        machine_config = load_machine_config().config
        db.apply_local_machine_config(conn, machine_config)
        conn.commit()
        exported_at_ms = int(time() * 1000)
        machine = conn.execute(
            "SELECT machine_id, name FROM machines "
            "WHERE is_local = 1 "
            "ORDER BY updated_at_ms DESC LIMIT 1"
        ).fetchone()
        if machine is None:
            msg = "No local machine row found for git sync export."
            raise ValueError(msg)
        machine_id = str(machine["machine_id"])
        machine_name = machine["name"]

        staged_root = state_root.parent / f".{state_root.name}.staging"
        if staged_root.exists():
            for path in sorted(staged_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
        staged_root.mkdir(parents=True, exist_ok=True)

        counts: dict[str, int] = {}
        raw_json_rows = 0
        for table in (
            "machines",
            "areas",
            "area_session_assignments",
            "machine_active_areas",
            "runs",
            "source_sessions",
            "source_session_metadata",
            "usage_events",
            "run_events",
        ):
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            counts[table] = len(rows)
            target = staged_root / f"{table}.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            lines: list[str] = []
            for row in rows:
                record = {key: row[key] for key in row.keys()}
                if table == "usage_events" and redact_raw_json:
                    record["raw_json"] = None
                elif table == "usage_events" and row["raw_json"] is not None:
                    raw_json_rows += 1
                lines.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
            serialized = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
            _write_if_changed(target, serialized)

        if state_root.exists():
            for old_file in sorted(state_root.rglob("*"), reverse=True):
                if old_file.is_file():
                    old_file.unlink()
                elif old_file.is_dir():
                    old_file.rmdir()
        state_root.parent.mkdir(parents=True, exist_ok=True)
        staged_root.rename(state_root)

        return StateExportResult(
            archive_path=state_root,
            exported_at_ms=exported_at_ms,
            schema_version=db.SCHEMA_VERSION,
            machine_id=machine_id,
            machine_name=machine_name if isinstance(machine_name, str) else None,
            run_count=counts["runs"],
            source_session_count=counts["source_sessions"],
            usage_event_count=counts["usage_events"],
            run_event_count=counts["run_events"],
            raw_json_count=raw_json_rows,
        )
    finally:
        conn.close()


def _load_text_state_into_db(
    state_root: Path, temp_db_path: Path
) -> ImportedStateContext:
    conn = sqlite3.connect(temp_db_path)
    conn.row_factory = sqlite3.Row
    try:
        db.migrate(conn)
        for table in (
            "machines",
            "areas",
            "area_session_assignments",
            "machine_active_areas",
            "runs",
            "source_sessions",
            "source_session_metadata",
            "usage_events",
            "run_events",
        ):
            path = state_root / f"{table}.jsonl"
            if not path.exists():
                continue
            rows = []
            for line in path.read_text(encoding="utf-8").splitlines():
                text = line.strip()
                if not text:
                    continue
                rows.append(json.loads(text))
            if not rows:
                continue
            columns = sorted(rows[0].keys())
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
            source_format="toktrail.text-state.v1",
        )
    finally:
        conn.close()


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
        rel = str(path.relative_to(state_root)).encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest() if files else ""


def _state_export_result_dict(result: StateExportResult) -> dict[str, object]:
    return {
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
