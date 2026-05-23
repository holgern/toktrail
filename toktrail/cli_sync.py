from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, cast

import typer

from toktrail.api.imports import import_configured_usage
from toktrail.api.sync import (
    default_archive_name,
    export_state_archive,
    import_state_archive,
)
from toktrail.config import LoadedToktrailConfig, load_resolved_toktrail_config
from toktrail.git_sync import (
    DEFAULT_BRANCH,
    DEFAULT_REMOTE,
    DEFAULT_STATE_DIR,
    analyze_git_sync_cleanup,
    cleanup_git_sync_worktree,
    ensure_git_repo,
    export_repo_archive,
    git_hooks_status,
    git_pull,
    git_push,
    git_sync_status,
    import_repo_archives,
    install_git_hooks,
    reset_git_sync_history,
    rewrite_git_sync_history,
    uninstall_git_hooks,
)
from toktrail.paths import (
    resolve_toktrail_config_path,
    resolve_toktrail_db_path,
)
from toktrail.sync import ConflictMode as SyncConflictMode
from toktrail.sync import RemoteActiveMode as SyncRemoteActiveMode

sync_app = typer.Typer(help="Export and import toktrail state archives.")
sync_git_app = typer.Typer(help="Sync toktrail state through a Git repository.")
sync_git_hooks_app = typer.Typer(help="Manage toktrail-managed Git hooks.")
sync_app.add_typer(sync_git_app, name="git")
sync_git_app.add_typer(sync_git_hooks_app, name="hooks")

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
    root_obj = ctx.find_root().obj or {}
    db_path = root_obj.get("db_path")
    if db_path is not None and not isinstance(db_path, Path):
        msg = f"Invalid --db value: {db_path!r}"
        raise ValueError(msg)
    return resolve_toktrail_db_path(db_path)


def _config_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    config_path = root_obj.get("config_path")
    if config_path is not None and not isinstance(config_path, Path):
        msg = f"Invalid --config value: {config_path!r}"
        raise ValueError(msg)
    return config_path


def _prices_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    prices_path = root_obj.get("prices_path")
    if prices_path is not None and not isinstance(prices_path, Path):
        msg = f"Invalid --prices value: {prices_path!r}"
        raise ValueError(msg)
    return prices_path


def _prices_dir_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    prices_dir_path = root_obj.get("prices_dir_path")
    if prices_dir_path is not None and not isinstance(prices_dir_path, Path):
        msg = f"Invalid --prices-dir value: {prices_dir_path!r}"
        raise ValueError(msg)
    return prices_dir_path


def _subscriptions_cli_path(ctx: typer.Context) -> Path | None:
    root_obj = ctx.find_root().obj or {}
    subscriptions_path = root_obj.get("subscriptions_path")
    if subscriptions_path is not None and not isinstance(subscriptions_path, Path):
        msg = f"Invalid --subscriptions value: {subscriptions_path!r}"
        raise ValueError(msg)
    return subscriptions_path


def _resolve_config_path(ctx: typer.Context) -> Path:
    return resolve_toktrail_config_path(_config_cli_path(ctx))


def _load_resolved_sync_config(ctx: typer.Context) -> LoadedToktrailConfig:
    return load_resolved_toktrail_config(
        config_cli_value=_config_cli_path(ctx),
        prices_cli_value=_prices_cli_path(ctx),
        prices_dir_cli_value=_prices_dir_cli_path(ctx),
        subscriptions_cli_value=_subscriptions_cli_path(ctx),
    )


def _resolve_git_repo(
    repo: Path | None,
    loaded_config: LoadedToktrailConfig,
) -> Path:
    if repo is not None:
        return repo.expanduser()
    configured = loaded_config.runtime.sync_git.repo
    if configured:
        return Path(configured).expanduser()
    return Path("~/.local/share/toktrail/git-sync").expanduser()


def _tracked_config_paths(loaded: LoadedToktrailConfig) -> tuple[Path, ...]:
    track = set(loaded.runtime.sync_git.track)
    paths: list[Path] = []
    if "config" in track:
        paths.append(loaded.config_path)
    if "prices" in track:
        paths.append(loaded.prices_path)
    if "provider-prices" in track:
        paths.append(loaded.prices_dir)
    if "subscriptions" in track:
        paths.append(loaded.subscriptions_path)
    return tuple(paths)


def _repo_relpath_or_none(
    repo_path: Path, path: Path, *, directory: bool = False
) -> str | None:
    repo = repo_path.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if resolved != repo and repo not in resolved.parents:
        return None
    relpath = resolved.relative_to(repo).as_posix()
    if directory:
        return f"{relpath.rstrip('/')}/"
    return relpath


def _git_path_is_tracked(repo_path: Path, relpath: str) -> bool:
    if relpath.endswith("/"):
        result = subprocess.run(
            ["git", "ls-files", relpath],
            cwd=repo_path,
            check=False,
            capture_output=True,
            text=True,
        )
        return bool((result.stdout or "").strip())
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relpath],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _config_file_status(
    *,
    loaded: LoadedToktrailConfig,
    repo_path: Path,
    track_key: str,
    path: Path,
    expected_relpath: str,
    exists: bool,
    file_count: int | None = None,
) -> dict[str, object]:
    track = set(loaded.runtime.sync_git.track)
    repo_relpath = _repo_relpath_or_none(
        repo_path,
        path,
        directory=expected_relpath.endswith("/"),
    )
    git_backed = track_key in track and repo_relpath == expected_relpath
    if git_backed:
        source = "git"
    elif track_key in track:
        source = "override"
    else:
        source = "local"

    payload: dict[str, object] = {
        "source": source,
        "git_backed": git_backed,
        "exists": exists,
        "git_tracked": bool(
            repo_relpath and _git_path_is_tracked(repo_path, repo_relpath)
        ),
        "path": str(path),
        "repo_path": repo_relpath,
    }
    if file_count is not None:
        payload["file_count"] = file_count
    return payload


def _git_status_config_files_payload(
    loaded: LoadedToktrailConfig,
    repo_path: Path,
) -> dict[str, object]:
    provider_file_count = (
        len(tuple(loaded.prices_dir.glob("*.toml")))
        if loaded.prices_dir.is_dir()
        else 0
    )
    return {
        "track": list(loaded.runtime.sync_git.track),
        "prices": _config_file_status(
            loaded=loaded,
            repo_path=repo_path,
            track_key="prices",
            path=loaded.prices_path,
            expected_relpath="config/prices.toml",
            exists=loaded.manual_prices_exists,
        ),
        "provider_prices": _config_file_status(
            loaded=loaded,
            repo_path=repo_path,
            track_key="provider-prices",
            path=loaded.prices_dir,
            expected_relpath="config/prices/",
            exists=loaded.provider_prices_exists,
            file_count=provider_file_count,
        ),
        "subscriptions": _config_file_status(
            loaded=loaded,
            repo_path=repo_path,
            track_key="subscriptions",
            path=loaded.subscriptions_path,
            expected_relpath="config/subscriptions.toml",
            exists=loaded.subscriptions_exists,
        ),
    }


def _yes_no(value: object) -> str:
    return "yes" if bool(value) else "no"


def _format_track(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "none"
    return ", ".join(str(item) for item in values)


def _format_config_path(item: Mapping[str, object]) -> str:
    repo_path = item.get("repo_path")
    if isinstance(repo_path, str) and repo_path:
        return repo_path
    return str(item.get("path") or "")


def _print_config_item(label: str, item: Mapping[str, object]) -> None:
    parts = [
        f"source {item.get('source')}",
        f"git {_yes_no(item.get('git_backed'))}",
        f"exists {_yes_no(item.get('exists'))}",
        f"tracked {_yes_no(item.get('git_tracked'))}",
    ]
    if "file_count" in item:
        parts.append(f"files {item['file_count']}")
    parts.append(f"path {_format_config_path(item)}")
    typer.echo(f"    {label}: " + ", ".join(parts))


def _print_git_status_config_files(config_files: Mapping[str, object]) -> None:
    typer.echo("  config files:")
    typer.echo(f"    track: {_format_track(config_files.get('track'))}")
    prices = config_files.get("prices")
    provider_prices = config_files.get("provider_prices")
    subscriptions = config_files.get("subscriptions")
    if isinstance(prices, Mapping):
        _print_config_item("prices", prices)
    if isinstance(provider_prices, Mapping):
        _print_config_item("provider prices", provider_prices)
    if isinstance(subscriptions, Mapping):
        _print_config_item("subscriptions", subscriptions)


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


def _format_bytes(value: int) -> str:
    size = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if size < 1024 or candidate == units[-1]:
            break
        size /= 1024
    if unit == "B":
        return f"{int(size)} {unit}"
    return f"{size:.1f} {unit}"


def _print_cleanup_analysis(repo: Path, analysis: object) -> None:
    payload = analysis.as_dict() if hasattr(analysis, "as_dict") else {}
    sizes = payload.get("sizes", {}) if isinstance(payload, dict) else {}
    git_sizes = sizes.get("git", {}) if isinstance(sizes, dict) else {}
    state_size = sizes.get("state") if isinstance(sizes, dict) else None
    obsolete = sizes.get("obsolete", []) if isinstance(sizes, dict) else []
    recommendation = (
        payload.get("recommendation", "none") if isinstance(payload, dict) else "none"
    )

    typer.echo("Git sync cleanup analysis")
    typer.echo(f"  repo: {payload.get('repo_path', repo)}")
    typer.echo(f"  branch: {payload.get('branch') or 'unknown'}")
    typer.echo(f"  dirty: {'yes' if payload.get('dirty') else 'no'}")
    typer.echo("  working tree:")
    if isinstance(state_size, dict):
        typer.echo(
            "    state files: "
            f"{int(state_size.get('file_count', 0)):,} files, "
            f"{_format_bytes(int(state_size.get('apparent_bytes', 0)))} apparent, "
            f"{_format_bytes(int(state_size.get('physical_bytes', 0)))} physical"
        )
    else:
        typer.echo("    state files: none")
    if isinstance(obsolete, list) and obsolete:
        for item in obsolete:
            if not isinstance(item, dict):
                continue
            physical_bytes = int(item.get("physical_bytes", 0))
            typer.echo(
                f"    obsolete {item.get('path')}: {_format_bytes(physical_bytes)}"
            )
    else:
        typer.echo("    obsolete paths: none")
    live_sqlite_paths = (
        payload.get("live_sqlite_paths", []) if isinstance(payload, dict) else []
    )
    if isinstance(live_sqlite_paths, list) and live_sqlite_paths:
        typer.echo("    live sqlite files:")
        for relpath in live_sqlite_paths:
            typer.echo(f"      - {relpath}")
    else:
        typer.echo("    live sqlite files: none")
    typer.echo("  git objects:")
    if isinstance(git_sizes, dict):
        typer.echo(f"    size-pack: {git_sizes.get('size-pack', 'unknown')}")
        if "count" in git_sizes:
            typer.echo(f"    count: {git_sizes['count']}")
    typer.echo(f"  recommendation: {recommendation}")
    if payload.get("filter_repo_available") is False:
        typer.echo("  note: git-filter-repo not found; rewrite-history is unavailable.")
    if recommendation in {"working-tree", "reset-history"}:
        typer.echo("")
        typer.echo("Next:")
        typer.echo(f"  toktrail sync git cleanup --repo {repo}")
        if recommendation == "working-tree":
            typer.echo(
                f"  toktrail sync git cleanup --repo {repo} --working-tree --commit"
            )
        else:
            typer.echo(
                f"  toktrail sync git cleanup --repo {repo} --reset-history --force"
            )
            typer.echo(
                f"  toktrail sync git cleanup --repo {repo} --rewrite-history --force"
            )


def _print_cleanup_result(result: object) -> None:
    payload = result.as_dict() if hasattr(result, "as_dict") else {}
    typer.echo("Git sync cleanup")
    typer.echo(f"  repo: {payload.get('repo_path')}")
    typer.echo(f"  mode: {payload.get('mode')}")
    typer.echo(f"  dry-run: {'yes' if payload.get('dry_run') else 'no'}")
    removed_paths = payload.get("removed_paths", [])
    if isinstance(removed_paths, list) and removed_paths:
        typer.echo(f"  removed paths: {len(removed_paths)}")
        for relpath in removed_paths:
            typer.echo(f"    - {relpath}")
    else:
        typer.echo("  removed paths: none")
    backup_bundle = payload.get("backup_bundle")
    if backup_bundle:
        typer.echo(f"  backup bundle: {backup_bundle}")
    typer.echo(f"  committed: {'yes' if payload.get('committed') else 'no'}")
    typer.echo(f"  commit: {payload.get('commit_hash') or 'none'}")
    typer.echo(f"  pushed: {'yes' if payload.get('pushed') else 'no'}")
    before_git = payload.get("before_git_size", {})
    after_git = payload.get("after_git_size", {})
    if isinstance(before_git, dict) and isinstance(after_git, dict):
        before_size_pack = before_git.get("size-pack", "unknown")
        after_size_pack = after_git.get("size-pack", "unknown")
        typer.echo(f"  git size-pack: {before_size_pack} -> {after_size_pack}")
    if payload.get("pushed") and payload.get("mode") in {
        "reset-history",
        "rewrite-history",
    }:
        typer.echo(
            "  warning: other clones must reclone or hard-reset before continuing."
        )


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


def _resolve_hook_command() -> tuple[str, ...]:
    configured = os.environ.get("TOKTRAIL_GIT_HOOK_COMMAND")
    if configured is None:
        return ("toktrail",)
    pieces = tuple(part for part in shlex.split(configured) if part)
    if not pieces:
        msg = "TOKTRAIL_GIT_HOOK_COMMAND must not be empty."
        raise ValueError(msg)
    return pieces


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
    hooks: Annotated[
        bool,
        typer.Option("--hooks/--no-hooks", help="Install toktrail-managed hooks."),
    ] = True,
    import_existing: Annotated[
        bool,
        typer.Option(
            "--import-existing/--no-import-existing",
            help="Import existing archives from the repo after init.",
        ),
    ] = True,
    force_hooks: Annotated[
        bool,
        typer.Option("--force-hooks", help="Overwrite foreign hooks."),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    runtime = loaded.runtime
    repo_path = _resolve_git_repo(repo, loaded)
    branch_name = branch or runtime.sync_git.branch or DEFAULT_BRANCH
    hook_payload: dict[str, object] | None = None
    import_payload: dict[str, object] | None = None
    try:
        ensure_git_repo(repo_path, remote_url=remote_url, branch=branch_name)
        if hooks:
            hook_result = install_git_hooks(
                repo_path,
                toktrail_command=_resolve_hook_command(),
                config_path=_resolve_config_path(ctx),
                db_path=_resolve_state_db(ctx),
                force=force_hooks,
            )
            hook_payload = hook_result.as_dict()
        if import_existing:
            import_result = import_repo_archives(
                _resolve_state_db(ctx),
                repo_path,
                dry_run=False,
                archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
                on_conflict=_parse_sync_conflict_mode(runtime.sync_git.on_conflict),
                remote_active=_parse_sync_remote_active_mode(
                    runtime.sync_git.remote_active
                ),
            )
            import_payload = import_result.as_dict()
        status = git_sync_status(
            _resolve_state_db(ctx),
            repo_path,
            archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
            remote=runtime.sync_git.remote or DEFAULT_REMOTE,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = status.as_dict()
        if hook_payload is not None:
            payload["hooks"] = hook_payload
        if import_payload is not None:
            payload["import"] = import_payload
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Git sync repository initialized:")
    typer.echo(f"  repo: {status.repo_path}")
    typer.echo(f"  branch: {status.branch or 'unknown'}")
    typer.echo(f"  remote: {status.remote or 'not configured'}")
    if hook_payload is not None:
        typer.echo(
            "  hooks: installed "
            f"{len(cast(list, hook_payload['installed']))}, "
            f"overwritten {len(cast(list, hook_payload['overwritten']))}, "
            f"skipped {len(cast(list, hook_payload['skipped']))}"
        )
    if import_payload is not None:
        typer.echo(
            "  import: "
            f"files {import_payload['state_files_seen']}, "
            f"imported {'yes' if import_payload['state_imported'] else 'no'}, "
            f"skipped {'yes' if import_payload['state_skipped'] else 'no'}"
        )
    if status.state_db_paths:
        typer.echo("  warning: live sqlite files found in repo:")
        for relpath in status.state_db_paths:
            typer.echo(f"    - {relpath}")
    typer.echo(f"  next: toktrail sync git sync --repo {status.repo_path}")


@sync_git_app.command("status")
def sync_git_status(
    ctx: typer.Context,
    repo: RepoOption = None,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    runtime = loaded.runtime
    repo_path = _resolve_git_repo(repo, loaded)
    try:
        status = git_sync_status(
            _resolve_state_db(ctx),
            repo_path,
            archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
            remote=runtime.sync_git.remote or DEFAULT_REMOTE,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    config_files = _git_status_config_files_payload(loaded, status.repo_path)

    if json_output:
        payload = status.as_dict()
        payload["config_files"] = config_files
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Git sync status")
    typer.echo(f"  repo: {status.repo_path}")
    typer.echo(f"  branch: {status.branch or 'unknown'}")
    typer.echo(f"  remote: {status.remote or 'not configured'}")
    typer.echo(f"  dirty: {'yes' if status.dirty else 'no'}")
    ahead_text = "?" if status.ahead is None else str(status.ahead)
    behind_text = "?" if status.behind is None else str(status.behind)
    typer.echo(f"  ahead/behind: {ahead_text}/{behind_text}")
    typer.echo(f"  state files: {status.state_file_count}")
    typer.echo(f"  pending imports: {status.pending_import_count}")
    if status.state_db_paths:
        typer.echo("  warning: live sqlite files found in repo:")
        for relpath in status.state_db_paths:
            typer.echo(f"    - {relpath}")
    _print_git_status_config_files(config_files)


@sync_git_app.command("cleanup")
def sync_git_cleanup(
    ctx: typer.Context,
    repo: RepoOption = None,
    working_tree: Annotated[bool, typer.Option("--working-tree")] = False,
    reset_history: Annotated[bool, typer.Option("--reset-history")] = False,
    rewrite_history: Annotated[bool, typer.Option("--rewrite-history")] = False,
    commit: Annotated[bool, typer.Option("--commit")] = False,
    push: Annotated[bool, typer.Option("--push")] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
    allow_dirty: Annotated[bool, typer.Option("--allow-dirty")] = False,
    json_output: JsonOption = False,
) -> None:
    selected_modes = [working_tree, reset_history, rewrite_history]
    if sum(1 for item in selected_modes if item) > 1:
        _exit_with_error(
            "Cleanup modes are mutually exclusive. Choose at most one of "
            "--working-tree, --reset-history, or --rewrite-history."
        )
    if commit and not working_tree:
        _exit_with_error("--commit only applies to --working-tree.")
    if push and not (reset_history or rewrite_history):
        _exit_with_error("--push only applies to --reset-history or --rewrite-history.")
    if (reset_history or rewrite_history) and not force:
        _exit_with_error("--force is required for destructive cleanup modes.")

    loaded = _load_resolved_sync_config(ctx)
    runtime = loaded.runtime
    repo_path = _resolve_git_repo(repo, loaded)
    try:
        if working_tree:
            result = cleanup_git_sync_worktree(
                repo_path,
                state_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
                commit=commit,
                dry_run=False,
                allow_dirty=allow_dirty,
            )
            if json_output:
                typer.echo(json.dumps(result.as_dict(), indent=2))
                return
            _print_cleanup_result(result)
            return
        if reset_history:
            result = reset_git_sync_history(
                _resolve_state_db(ctx),
                repo_path,
                config_path=loaded.config_path,
                tracked_config_paths=_tracked_config_paths(loaded),
                redact_raw_json=runtime.sync_git.redact_raw_json,
                state_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
                branch=runtime.sync_git.branch or DEFAULT_BRANCH,
                remote=runtime.sync_git.remote or DEFAULT_REMOTE,
                push=push,
                force=force,
                allow_dirty=allow_dirty,
            )
            if json_output:
                typer.echo(json.dumps(result.as_dict(), indent=2))
                return
            _print_cleanup_result(result)
            return
        if rewrite_history:
            result = rewrite_git_sync_history(
                repo_path,
                branch=runtime.sync_git.branch or DEFAULT_BRANCH,
                remote=runtime.sync_git.remote or DEFAULT_REMOTE,
                push=push,
                force=force,
                allow_dirty=allow_dirty,
            )
            if json_output:
                typer.echo(json.dumps(result.as_dict(), indent=2))
                return
            _print_cleanup_result(result)
            return
        analysis = analyze_git_sync_cleanup(
            repo_path,
            state_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(analysis.as_dict(), indent=2))
        return
    _print_cleanup_analysis(repo_path, analysis)


@sync_git_hooks_app.command("install")
def sync_git_hooks_install(
    ctx: typer.Context,
    repo: RepoOption = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    repo_path = _resolve_git_repo(repo, loaded)
    try:
        result = install_git_hooks(
            repo_path,
            toktrail_command=_resolve_hook_command(),
            config_path=_resolve_config_path(ctx),
            db_path=_resolve_state_db(ctx),
            force=force,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return

    typer.echo("Git hooks install")
    typer.echo(f"  repo: {result.repo_path}")
    typer.echo(f"  installed: {', '.join(result.installed) or 'none'}")
    typer.echo(f"  overwritten: {', '.join(result.overwritten) or 'none'}")
    typer.echo(f"  skipped: {', '.join(result.skipped) or 'none'}")


@sync_git_hooks_app.command("status")
def sync_git_hooks_status(
    ctx: typer.Context,
    repo: RepoOption = None,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    repo_path = _resolve_git_repo(repo, loaded)
    try:
        result = git_hooks_status(repo_path)
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return

    typer.echo("Git hooks status")
    typer.echo(f"  repo: {result.repo_path}")
    for hook_name, hook_status in result.hooks.items():
        typer.echo(f"  {hook_name}: {hook_status}")


@sync_git_hooks_app.command("uninstall")
def sync_git_hooks_uninstall(
    ctx: typer.Context,
    repo: RepoOption = None,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    repo_path = _resolve_git_repo(repo, loaded)
    try:
        result = uninstall_git_hooks(repo_path)
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return

    typer.echo("Git hooks uninstall")
    typer.echo(f"  repo: {result.repo_path}")
    typer.echo(f"  removed: {', '.join(result.overwritten) or 'none'}")
    typer.echo(f"  skipped: {', '.join(result.skipped) or 'none'}")


@sync_git_app.command("import-local")
def sync_git_import_local(
    ctx: typer.Context,
    repo: RepoOption = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    on_conflict: Annotated[str | None, typer.Option("--on-conflict")] = None,
    remote_active: Annotated[str | None, typer.Option("--remote-active")] = None,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    runtime = loaded.runtime
    repo_path = _resolve_git_repo(repo, loaded)
    conflict_mode = on_conflict or runtime.sync_git.on_conflict
    remote_active_mode = remote_active or runtime.sync_git.remote_active
    try:
        result = import_repo_archives(
            _resolve_state_db(ctx),
            repo_path,
            dry_run=dry_run,
            archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
            on_conflict=_parse_sync_conflict_mode(conflict_mode),
            remote_active=_parse_sync_remote_active_mode(remote_active_mode),
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return
    if quiet:
        return

    heading = "Dry-run local import:" if dry_run else "Local import:"
    typer.echo(heading)
    typer.echo(f"  repo: {result.repo_path}")
    typer.echo(f"  state files: {result.state_files_seen}")
    typer.echo(f"  state imported: {'yes' if result.state_imported else 'no'}")
    typer.echo(f"  state skipped: {'yes' if result.state_skipped else 'no'}")


@sync_git_app.command("export-local")
def sync_git_export_local(
    ctx: typer.Context,
    repo: RepoOption = None,
    refresh: RefreshOption = True,
    message: Annotated[str | None, typer.Option("--message")] = None,
    allow_dirty: Annotated[bool, typer.Option("--allow-dirty")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    runtime = loaded.runtime
    repo_path = _resolve_git_repo(repo, loaded)
    try:
        refresh_payload = _refresh_for_export(
            ctx,
            enabled=refresh,
            details=False,
            json_output=json_output,
        )
        result = export_repo_archive(
            _resolve_state_db(ctx),
            repo_path,
            archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
            config_path=loaded.config_path,
            include_config=False,
            redact_raw_json=runtime.sync_git.redact_raw_json,
            commit_message=message,
            remote=runtime.sync_git.remote or DEFAULT_REMOTE,
            branch=runtime.sync_git.branch or DEFAULT_BRANCH,
            push=False,
            allow_dirty=allow_dirty,
            tracked_config_paths=_tracked_config_paths(loaded),
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = result.as_dict()
        if refresh:
            payload["refresh"] = refresh_payload
        typer.echo(json.dumps(payload, indent=2))
        return
    if quiet:
        return

    typer.echo("Git export (local only):")
    typer.echo(f"  repo: {result.repo_path}")
    typer.echo(f"  state: {result.state_path}")
    typer.echo(f"  committed: {'yes' if result.committed else 'no'}")
    typer.echo(f"  commit: {result.commit_hash or 'none'}")


@sync_git_app.command("pull")
def sync_git_pull(
    ctx: typer.Context,
    repo: RepoOption = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    on_conflict: Annotated[str | None, typer.Option("--on-conflict")] = None,
    remote_active: Annotated[str | None, typer.Option("--remote-active")] = None,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    runtime = loaded.runtime
    repo_path = _resolve_git_repo(repo, loaded)
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
            archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
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
    typer.echo(f"  state files: {result.state_files_seen}")
    typer.echo(f"  state imported: {'yes' if result.state_imported else 'no'}")
    typer.echo(f"  state skipped: {'yes' if result.state_skipped else 'no'}")


@sync_git_app.command("push")
def sync_git_push(
    ctx: typer.Context,
    repo: RepoOption = None,
    refresh: RefreshOption = True,
    message: Annotated[str | None, typer.Option("--message")] = None,
    allow_dirty: Annotated[bool, typer.Option("--allow-dirty")] = False,
    json_output: JsonOption = False,
) -> None:
    loaded = _load_resolved_sync_config(ctx)
    runtime = loaded.runtime
    repo_path = _resolve_git_repo(repo, loaded)
    remote_name = runtime.sync_git.remote or DEFAULT_REMOTE
    branch_name = runtime.sync_git.branch or DEFAULT_BRANCH
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
            archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
            config_path=loaded.config_path,
            include_config=False,
            redact_raw_json=runtime.sync_git.redact_raw_json,
            commit_message=message,
            remote=remote_name,
            branch=branch_name,
            push=False,
            allow_dirty=allow_dirty,
            tracked_config_paths=_tracked_config_paths(loaded),
        )
        pushed = False
        if result.committed:
            git_push(repo_path, remote=remote_name, branch=branch_name)
            pushed = True
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload = result.as_dict()
        payload["pushed"] = pushed
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Git push/export:")
    typer.echo(f"  repo: {result.repo_path}")
    typer.echo(f"  state: {result.state_path}")
    typer.echo(f"  committed: {'yes' if result.committed else 'no'}")
    typer.echo(f"  commit: {result.commit_hash or 'none'}")
    typer.echo(f"  pushed: {'yes' if pushed else 'no'}")


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
    loaded = _load_resolved_sync_config(ctx)
    runtime = loaded.runtime
    repo_path = _resolve_git_repo(repo, loaded)
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
            archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
            on_conflict=_parse_sync_conflict_mode(conflict_mode),
            remote_active=_parse_sync_remote_active_mode(remote_active_mode),
        )
        push_result = None
        pushed = False
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
                archive_dir=runtime.sync_git.state_dir or DEFAULT_STATE_DIR,
                config_path=loaded.config_path,
                include_config=False,
                redact_raw_json=runtime.sync_git.redact_raw_json,
                commit_message=message,
                remote=remote_name,
                branch=branch_name,
                push=False,
                allow_dirty=allow_dirty,
                tracked_config_paths=_tracked_config_paths(loaded),
            )
            if push_result.committed:
                git_push(repo_path, remote=remote_name, branch=branch_name)
                pushed = True
    except (OSError, ValueError) as exc:
        _exit_with_error(str(exc))

    if json_output:
        payload: dict[str, object] = {
            "repo_path": str(repo_path),
            "pull": {
                "state_files_seen": pull_result.state_files_seen,
                "state_imported": pull_result.state_imported,
                "state_skipped": pull_result.state_skipped,
            },
            "push": None
            if push_result is None
            else {
                "state_path": str(push_result.state_path),
                "committed": push_result.committed,
                "pushed": pushed,
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
        f"  state files: scanned {pull_result.archives_seen}, "
        f"imported {'yes' if pull_result.state_imported else 'no'}, "
        f"skipped {'yes' if pull_result.state_skipped else 'no'}"
    )
    if push_result is not None:
        typer.echo(f"  export: {push_result.state_path}")
        typer.echo(f"  commit: {push_result.commit_hash or 'none'}")
        typer.echo(f"  pushed: {'yes' if pushed else 'no'}")
