from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from toktrail.api.imports import import_configured_usage
from toktrail.api.sync import (
    default_archive_name,
    export_state_archive,
    import_state_archive,
)
from toktrail.config import RuntimeConfig, load_runtime_config
from toktrail.git_sync import (
    DEFAULT_ARCHIVE_DIR,
    DEFAULT_BRANCH,
    DEFAULT_REMOTE,
    ensure_git_repo,
    export_repo_archive,
    git_pull,
    git_sync_status,
    import_repo_archives,
)
from toktrail.paths import resolve_toktrail_config_path, resolve_toktrail_db_path
from toktrail.sync import ConflictMode as SyncConflictMode
from toktrail.sync import RemoteActiveMode as SyncRemoteActiveMode

sync_app = typer.Typer(help="Export and import toktrail state archives.")
sync_git_app = typer.Typer(help="Sync toktrail state through a Git repository.")
sync_app.add_typer(sync_git_app, name="git")

JsonOption = Annotated[bool, typer.Option("--json")]
RefreshOption = Annotated[
    bool,
    typer.Option(
        "--refresh/--no-refresh",
        help="Refresh configured harness usage before export.",
    ),
]
RefreshDetailsOption = Annotated[
    bool,
    typer.Option(
        "--refresh-details",
        help="Print a compact refresh summary before export output.",
    ),
]
RepoOption = Annotated[
    Path | None,
    typer.Option("--repo", help="Path to the Git sync repository."),
]


def _resolve_state_db(ctx: typer.Context) -> Path:
    db_path = None if not isinstance(ctx.obj, dict) else ctx.obj.get("db_path")
    if db_path is not None and not isinstance(db_path, Path):
        msg = f"Invalid --db value: {db_path!r}"
        raise ValueError(msg)
    return resolve_toktrail_db_path(db_path)


def _resolve_config_path(ctx: typer.Context) -> Path:
    config_path = None if not isinstance(ctx.obj, dict) else ctx.obj.get("config_path")
    if config_path is not None and not isinstance(config_path, Path):
        msg = f"Invalid --config value: {config_path!r}"
        raise ValueError(msg)
    return resolve_toktrail_config_path(config_path)


def _load_runtime_sync_config(ctx: typer.Context) -> RuntimeConfig:
    return load_runtime_config(_resolve_config_path(ctx))


def _resolve_git_repo(
    ctx: typer.Context,
    repo: Path | None,
    runtime_config: RuntimeConfig,
) -> Path:
    if repo is not None:
        return repo.expanduser()
    configured = runtime_config.sync_git.repo
    if configured:
        return Path(configured).expanduser()
    return Path("~/.local/share/toktrail/git-sync").expanduser()


def _exit_with_error(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


def _parse_sync_conflict_mode(value: str) -> SyncConflictMode:
    if value == "fail":
        return "fail"
    if value == "skip":
        return "skip"
    msg = "--on-conflict must be one of: fail, skip."
    raise ValueError(msg)


def _parse_sync_remote_active_mode(value: str) -> SyncRemoteActiveMode:
    if value == "fail":
        return "fail"
    if value == "close-at-export":
        return "close-at-export"
    if value == "keep":
        return "keep"
    msg = "--remote-active must be one of: fail, close-at-export, keep."
    raise ValueError(msg)


def _print_refresh_summary(results: tuple[object, ...]) -> None:
    typer.echo("Refresh")
    for item in results:
        result = item.as_dict() if hasattr(item, "as_dict") else {}
        harness = str(result.get("harness", "unknown"))
        imported = int(result.get("rows_imported", 0))
        skipped = int(result.get("rows_skipped", 0))
        status = str(result.get("status", "ok"))
        typer.echo(
            f"  {harness:<10} imported {imported:>6}  "
            f"skipped {skipped:>6}  status={status}"
        )


def _refresh_for_export(
    ctx: typer.Context,
    *,
    enabled: bool,
    details: bool,
    json_output: bool,
) -> list[dict[str, object]]:
    if not enabled:
        return []
    results = import_configured_usage(
        _resolve_state_db(ctx),
        harnesses=None,
        source_path=None,
        session_id=None,
        use_active_session=True,
        include_raw_json=None,
        config_path=_resolve_config_path(ctx),
        since_start=False,
        since_ms=None,
    )
    if details and not json_output:
        _print_refresh_summary(results)
    return [result.as_dict() for result in results]


@sync_app.command("export")
def sync_export(
    ctx: typer.Context,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    refresh: RefreshOption = True,
    refresh_details: RefreshDetailsOption = False,
    include_config: Annotated[bool, typer.Option("--include-config")] = False,
    redact_raw_json: Annotated[bool, typer.Option("--redact-raw-json")] = False,
    json_output: JsonOption = False,
) -> None:
    try:
        refresh_payload = _refresh_for_export(
            ctx,
            enabled=refresh,
            details=refresh_details,
            json_output=json_output,
        )
        archive_path = out or Path(default_archive_name())
        result = export_state_archive(
            _resolve_state_db(ctx),
            archive_path,
            config_path=_resolve_config_path(ctx),
            include_config=include_config,
            redact_raw_json=redact_raw_json,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = result.as_dict()
        if refresh_details:
            payload["refresh"] = refresh_payload
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Exported toktrail state archive:")
    typer.echo(f"  archive: {result.archive_path}")
    typer.echo(f"  schema: {result.schema_version}")
    typer.echo(f"  machine_id: {result.machine_id}")
    typer.echo(f"  runs: {result.run_count}")
    typer.echo(f"  source sessions: {result.source_session_count}")
    typer.echo(f"  usage events: {result.usage_event_count}")
    typer.echo(f"  run links: {result.run_event_count}")
    typer.echo(f"  raw json rows: {result.raw_json_count}")


@sync_app.command("import")
def sync_import(
    ctx: typer.Context,
    archive: Annotated[Path, typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    on_conflict: Annotated[str, typer.Option("--on-conflict")] = "fail",
    remote_active: Annotated[str, typer.Option("--remote-active")] = "fail",
    json_output: JsonOption = False,
) -> None:
    try:
        result = import_state_archive(
            _resolve_state_db(ctx),
            archive,
            dry_run=dry_run,
            on_conflict=on_conflict,
            remote_active=remote_active,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return

    heading = (
        "Dry-run import toktrail state archive:"
        if dry_run
        else "Imported toktrail state archive:"
    )
    typer.echo(heading)
    typer.echo(f"  archive: {result.archive_path}")
    typer.echo(
        f"  runs: inserted {result.runs_inserted}, updated {result.runs_updated}"
    )
    typer.echo(
        "  source sessions: inserted "
        f"{result.source_sessions_inserted}, updated {result.source_sessions_updated}"
    )
    verb = "would insert" if dry_run else "inserted"
    skip_verb = "would skip" if dry_run else "skipped"
    typer.echo(f"  usage events: {verb} {result.usage_events_inserted}")
    typer.echo(f"  usage events: {skip_verb} {result.usage_events_skipped}")
    typer.echo(f"  run links: inserted {result.run_events_inserted}")
    typer.echo(f"  conflicts: {len(result.conflicts)}")


@sync_git_app.command("init")
def sync_git_init(
    ctx: typer.Context,
    repo: RepoOption = None,
    remote_url: Annotated[str | None, typer.Option("--remote")] = None,
    branch: Annotated[str | None, typer.Option("--branch")] = None,
    json_output: JsonOption = False,
) -> None:
    runtime = _load_runtime_sync_config(ctx)
    repo_path = _resolve_git_repo(ctx, repo, runtime)
    branch_name = branch or runtime.sync_git.branch or DEFAULT_BRANCH
    try:
        ensure_git_repo(repo_path, remote_url=remote_url, branch=branch_name)
        status = git_sync_status(
            _resolve_state_db(ctx),
            repo_path,
            archive_dir=runtime.sync_git.archive_dir or DEFAULT_ARCHIVE_DIR,
            remote=runtime.sync_git.remote or DEFAULT_REMOTE,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(status.as_dict(), indent=2))
        return

    typer.echo("Git sync repository initialized:")
    typer.echo(f"  repo: {status.repo_path}")
    typer.echo(f"  branch: {status.branch or 'unknown'}")
    typer.echo(f"  remote: {status.remote or 'not configured'}")
    if status.state_db_paths:
        typer.echo("  warning: live sqlite files found in repo:")
        for relpath in status.state_db_paths:
            typer.echo(f"    - {relpath}")


@sync_git_app.command("status")
def sync_git_status(
    ctx: typer.Context,
    repo: RepoOption = None,
    json_output: JsonOption = False,
) -> None:
    runtime = _load_runtime_sync_config(ctx)
    repo_path = _resolve_git_repo(ctx, repo, runtime)
    try:
        status = git_sync_status(
            _resolve_state_db(ctx),
            repo_path,
            archive_dir=runtime.sync_git.archive_dir or DEFAULT_ARCHIVE_DIR,
            remote=runtime.sync_git.remote or DEFAULT_REMOTE,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(status.as_dict(), indent=2))
        return

    typer.echo("Git sync status")
    typer.echo(f"  repo: {status.repo_path}")
    typer.echo(f"  branch: {status.branch or 'unknown'}")
    typer.echo(f"  remote: {status.remote or 'not configured'}")
    typer.echo(f"  dirty: {'yes' if status.dirty else 'no'}")
    ahead_text = "?" if status.ahead is None else str(status.ahead)
    behind_text = "?" if status.behind is None else str(status.behind)
    typer.echo(f"  ahead/behind: {ahead_text}/{behind_text}")
    typer.echo(f"  archives: {status.archive_count}")
    typer.echo(f"  pending imports: {status.pending_import_count}")
    if status.state_db_paths:
        typer.echo("  warning: live sqlite files found in repo:")
        for relpath in status.state_db_paths:
            typer.echo(f"    - {relpath}")


@sync_git_app.command("pull")
def sync_git_pull(
    ctx: typer.Context,
    repo: RepoOption = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    on_conflict: Annotated[str | None, typer.Option("--on-conflict")] = None,
    remote_active: Annotated[str | None, typer.Option("--remote-active")] = None,
    json_output: JsonOption = False,
) -> None:
    runtime = _load_runtime_sync_config(ctx)
    repo_path = _resolve_git_repo(ctx, repo, runtime)
    remote_name = runtime.sync_git.remote or DEFAULT_REMOTE
    branch_name = runtime.sync_git.branch or DEFAULT_BRANCH
    conflict_mode = on_conflict or runtime.sync_git.on_conflict
    remote_active_mode = remote_active or runtime.sync_git.remote_active
    try:
        git_pull(repo_path, remote=remote_name, branch=branch_name)
        result = import_repo_archives(
            _resolve_state_db(ctx),
            repo_path,
            dry_run=dry_run,
            archive_dir=runtime.sync_git.archive_dir or DEFAULT_ARCHIVE_DIR,
            on_conflict=_parse_sync_conflict_mode(conflict_mode),
            remote_active=_parse_sync_remote_active_mode(remote_active_mode),
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return

    heading = "Dry-run git pull/import:" if dry_run else "Git pull/import:"
    typer.echo(heading)
    typer.echo(f"  repo: {result.repo_path}")
    typer.echo(f"  archives: seen {result.archives_seen}")
    typer.echo(f"  archives: imported {result.archives_imported}")
    typer.echo(f"  archives: skipped {result.archives_skipped}")


@sync_git_app.command("push")
def sync_git_push(
    ctx: typer.Context,
    repo: RepoOption = None,
    refresh: RefreshOption = True,
    message: Annotated[str | None, typer.Option("--message")] = None,
    allow_dirty: Annotated[bool, typer.Option("--allow-dirty")] = False,
    json_output: JsonOption = False,
) -> None:
    runtime = _load_runtime_sync_config(ctx)
    repo_path = _resolve_git_repo(ctx, repo, runtime)
    try:
        _refresh_for_export(
            ctx,
            enabled=refresh,
            details=False,
            json_output=json_output,
        )
        result = export_repo_archive(
            _resolve_state_db(ctx),
            repo_path,
            archive_dir=runtime.sync_git.archive_dir or DEFAULT_ARCHIVE_DIR,
            config_path=_resolve_config_path(ctx),
            include_config=runtime.sync_git.include_config,
            redact_raw_json=runtime.sync_git.redact_raw_json,
            commit_message=message,
            remote=runtime.sync_git.remote or DEFAULT_REMOTE,
            branch=runtime.sync_git.branch or DEFAULT_BRANCH,
            push=True,
            allow_dirty=allow_dirty,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return

    typer.echo("Git push/export:")
    typer.echo(f"  repo: {result.repo_path}")
    typer.echo(f"  archive: {result.archive_path}")
    typer.echo(f"  committed: {'yes' if result.committed else 'no'}")
    typer.echo(f"  commit: {result.commit_hash or 'none'}")
    typer.echo(f"  pushed: {'yes' if result.pushed else 'no'}")


@sync_git_app.command("sync")
def sync_git_sync(
    ctx: typer.Context,
    repo: RepoOption = None,
    refresh: RefreshOption = True,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    allow_dirty: Annotated[bool, typer.Option("--allow-dirty")] = False,
    on_conflict: Annotated[str | None, typer.Option("--on-conflict")] = None,
    remote_active: Annotated[str | None, typer.Option("--remote-active")] = None,
    message: Annotated[str | None, typer.Option("--message")] = None,
    json_output: JsonOption = False,
) -> None:
    runtime = _load_runtime_sync_config(ctx)
    repo_path = _resolve_git_repo(ctx, repo, runtime)
    remote_name = runtime.sync_git.remote or DEFAULT_REMOTE
    branch_name = runtime.sync_git.branch or DEFAULT_BRANCH
    conflict_mode = on_conflict or runtime.sync_git.on_conflict
    remote_active_mode = remote_active or runtime.sync_git.remote_active
    try:
        git_pull(repo_path, remote=remote_name, branch=branch_name)
        pull_result = import_repo_archives(
            _resolve_state_db(ctx),
            repo_path,
            dry_run=dry_run,
            archive_dir=runtime.sync_git.archive_dir or DEFAULT_ARCHIVE_DIR,
            on_conflict=_parse_sync_conflict_mode(conflict_mode),
            remote_active=_parse_sync_remote_active_mode(remote_active_mode),
        )
        push_result = None
        if not dry_run:
            _refresh_for_export(
                ctx,
                enabled=refresh,
                details=False,
                json_output=json_output,
            )
            push_result = export_repo_archive(
                _resolve_state_db(ctx),
                repo_path,
                archive_dir=runtime.sync_git.archive_dir or DEFAULT_ARCHIVE_DIR,
                config_path=_resolve_config_path(ctx),
                include_config=runtime.sync_git.include_config,
                redact_raw_json=runtime.sync_git.redact_raw_json,
                commit_message=message,
                remote=remote_name,
                branch=branch_name,
                push=True,
                allow_dirty=allow_dirty,
            )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload: dict[str, object] = {
            "repo_path": str(repo_path),
            "pull": {
                "archives_seen": pull_result.archives_seen,
                "archives_imported": pull_result.archives_imported,
                "archives_skipped": pull_result.archives_skipped,
            },
            "push": None if push_result is None else {
                "archive_path": str(push_result.archive_path),
                "committed": push_result.committed,
                "pushed": push_result.pushed,
                "commit_hash": push_result.commit_hash,
            },
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Git sync")
    typer.echo(f"  repo: {repo_path}")
    typer.echo(f"  branch: {branch_name}")
    typer.echo("  pulled: yes")
    typer.echo(
        f"  archives: seen {pull_result.archives_seen}, "
        f"imported {pull_result.archives_imported}, "
        f"skipped {pull_result.archives_skipped}"
    )
    if push_result is not None:
        typer.echo(f"  export: {push_result.archive_path}")
        typer.echo(f"  commit: {push_result.commit_hash or 'none'}")
        typer.echo(f"  pushed: {'yes' if push_result.pushed else 'no'}")
