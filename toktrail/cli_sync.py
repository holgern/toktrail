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
from toktrail.paths import resolve_toktrail_config_path, resolve_toktrail_db_path

sync_app = typer.Typer(help="Export and import toktrail state archives.")

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


def _exit_with_error(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


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
