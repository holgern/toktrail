from __future__ import annotations

import datetime
import fnmatch
import json
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from toktrail.cli_parts import usage as usage_parts
from toktrail.db import (
    archive_area_path,
    assign_area_to_source_session,
    ensure_area,
    get_active_area_status,
    get_area_by_path,
    get_local_machine_id,
    list_areas,
    merge_area_paths,
    move_area_path,
    normalize_area_path,
    resolve_machine_selector,
    set_active_area,
    unassign_area_from_source_session,
)
from toktrail.formatting import format_epoch_ms_compact
from toktrail.periods import resolve_time_range

OpenConnection = Callable[[typer.Context], sqlite3.Connection]
ExitWithError = Callable[[str], None]
PrintTable = Callable[..., None]
FormatInt = Callable[[int], str]
ResolveConfigPath = Callable[[typer.Context], Path]
LoadCostingConfig = Callable[[typer.Context], object]
LoadResolvedConfig = Callable[[typer.Context], object]
ResolveMachineId = Callable[[sqlite3.Connection, str | None], str | None]

HarnessOption = Annotated[str | None, typer.Option("--harness")]
MachineOption = Annotated[
    str | None,
    typer.Option("--machine", help="Filter by machine name or machine id."),
]
AreaOption = Annotated[str | None, typer.Option("--area")]
AreaLeafOption = Annotated[str | None, typer.Option("--area-leaf")]
UsagePeriodOption = Annotated[str | None, typer.Option("--period")]
JsonOption = Annotated[bool, typer.Option("--json")]
RichOption = Annotated[
    bool,
    typer.Option(
        "--rich",
        help="Render tables with Rich formatting. Default output stays borderless.",
    ),
]
NameOption = Annotated[str | None, typer.Option("--name")]
LastOption = Annotated[bool, typer.Option("--last")]
TimezoneOption = Annotated[str | None, typer.Option("--timezone")]
UtcOption = Annotated[bool, typer.Option("--utc")]


def register_area_commands(
    area_app: typer.Typer,
    *,
    open_toktrail_connection: OpenConnection,
    exit_with_error: ExitWithError,
    print_table: PrintTable,
    format_int: FormatInt,
    resolve_config_path: ResolveConfigPath,
    load_costing_config_or_exit: LoadCostingConfig,
    load_resolved_toktrail_config_or_exit: LoadResolvedConfig,
    resolve_machine_id_or_exit: ResolveMachineId,
) -> None:
    @area_app.command("create")
    def area_create(
        ctx: typer.Context,
        path: Annotated[str, typer.Argument(help="Area path.")],
        name: NameOption = None,
        json_output: JsonOption = False,
    ) -> None:
        conn = open_toktrail_connection(ctx)
        try:
            area = ensure_area(conn, path, name=name)
            conn.commit()
        except ValueError as exc:
            exit_with_error(str(exc))
        finally:
            conn.close()
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "area_id": area.id,
                        "local_id": area.id,
                        "sync_id": area.sync_id,
                        "stable_id": area.sync_id,
                        "path": area.path,
                        "name": area.name,
                        "parent_id": area.parent_id,
                        "archived_at_ms": area.archived_at_ms,
                    },
                    indent=2,
                )
            )
            return
        typer.echo(f"Created area: {area.path}")

    @area_app.command("list")
    def area_list(
        ctx: typer.Context,
        json_output: JsonOption = False,
        rich_output: RichOption = False,
        verbose: Annotated[bool, typer.Option("--verbose")] = False,
    ) -> None:
        conn = open_toktrail_connection(ctx)
        try:
            areas = list_areas(conn)
            active_status = get_active_area_status(conn)
        finally:
            conn.close()
        if json_output:
            typer.echo(
                json.dumps(
                    [
                        {
                            "area_id": area.id,
                            "local_id": area.id,
                            "sync_id": area.sync_id,
                            "stable_id": area.sync_id,
                            "path": area.path,
                            "name": area.name,
                            "parent_id": area.parent_id,
                            "archived_at_ms": area.archived_at_ms,
                        }
                        for area in areas
                    ],
                    indent=2,
                )
            )
            return
        if not areas:
            typer.echo("No areas defined.")
            return
        active_area_id = active_status.area.id if active_status.area is not None else None
        if not verbose:
            typer.echo("area")
            for area in areas:
                depth = area.path.count("/")
                suffix = " *" if active_area_id == area.id else ""
                typer.echo(f"{'  ' * depth}{area.path}{suffix}")
            return
        print_table(
            [
                {
                    "area": f"{'  ' * area.path.count('/')}{area.path}",
                    "stable_id": area.sync_id[:12],
                    "local_id": format_int(area.id),
                    "active": "*" if active_area_id == area.id else "",
                }
                for area in areas
            ],
            ["area", "stable_id", "local_id", "active"],
            {
                "area": "area",
                "stable_id": "stable id",
                "local_id": "local id",
                "active": "active",
            },
            rich_output=rich_output,
            numeric_columns={"local_id"},
            wrap_columns={"area"},
            max_widths={"area": 52},
        )

    @area_app.command("use")
    def area_use(
        ctx: typer.Context,
        path: Annotated[str, typer.Argument(help="Area path.")],
        create: Annotated[bool, typer.Option("--create/--no-create")] = True,
        ttl: Annotated[
            str | None,
            typer.Option(
                "--ttl",
                help="Auto-expire active area after duration like 30m, 4h, or 1d.",
            ),
        ] = None,
        until: Annotated[
            str | None,
            typer.Option(
                "--until",
                help="Auto-expire active area at ISO local/UTC timestamp.",
            ),
        ] = None,
        json_output: JsonOption = False,
    ) -> None:
        if ttl is not None and until is not None:
            exit_with_error("Use either --ttl or --until, not both.")
        expires_at_ms = _parse_area_expiry_or_exit(ttl=ttl, until=until, exit_with_error=exit_with_error)
        conn = open_toktrail_connection(ctx)
        try:
            area = get_area_by_path(conn, path)
            if area is None:
                if not create:
                    exit_with_error(f"Area not found: {path}")
                area = ensure_area(conn, path)
            set_active_area(conn, area.id, expires_at_ms=expires_at_ms)
            conn.commit()
        except ValueError as exc:
            exit_with_error(str(exc))
        finally:
            conn.close()
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "active_area": {
                            "area_id": area.id,
                            "local_id": area.id,
                            "sync_id": area.sync_id,
                            "stable_id": area.sync_id,
                            "path": area.path,
                            "name": area.name,
                            "expires_at_ms": expires_at_ms,
                        }
                    },
                    indent=2,
                )
            )
            return
        typer.echo(f"Active area: {area.path}")
        typer.echo(
            "New source sessions imported on this machine will be assigned to this area."
        )
        typer.echo(
            "Existing imported sessions are unchanged; use "
            "`toktrail area assign --last` to move the latest session."
        )

    @area_app.command("clear")
    def area_clear(ctx: typer.Context) -> None:
        conn = open_toktrail_connection(ctx)
        try:
            set_active_area(conn, None)
            conn.commit()
        finally:
            conn.close()
        typer.echo(
            "Cleared active area. New source sessions will be unassigned "
            "until another area is selected."
        )

    @area_app.command("status")
    def area_status(ctx: typer.Context, json_output: JsonOption = False) -> None:
        conn = open_toktrail_connection(ctx)
        try:
            status = get_active_area_status(conn)
        finally:
            conn.close()
        active = status.area
        if json_output:
            payload: dict[str, object] = {
                "machine_id": status.machine_id,
                "machine_label": status.machine_label,
                "updated_at_ms": status.updated_at_ms,
                "expires_at_ms": status.expires_at_ms,
                "active_area": None,
            }
            if active is not None:
                payload["active_area"] = {
                    "area_id": active.id,
                    "local_id": active.id,
                    "sync_id": active.sync_id,
                    "stable_id": active.sync_id,
                    "path": active.path,
                    "name": active.name,
                }
            typer.echo(json.dumps(payload, indent=2))
            return
        if active is None:
            typer.echo(f"Active area ({status.machine_label}): none")
            return
        if status.expires_at_ms is None:
            typer.echo(f"Active area ({status.machine_label}): {active.path}")
            return
        expiry_text = format_epoch_ms_compact(status.expires_at_ms, utc=False)
        typer.echo(
            f"Active area ({status.machine_label}): {active.path}, expires {expiry_text}"
        )

    @area_app.command("assign")
    def area_assign(
        ctx: typer.Context,
        path: Annotated[str, typer.Argument(help="Area path.")],
        session: Annotated[
            str | None,
            typer.Option(
                "--session",
                help="Session key machine/harness/source_session_id from usage sessions.",
            ),
        ] = None,
        harness: HarnessOption = None,
        source_session_id: Annotated[
            str | None,
            typer.Option("--source-session-id", "--source-session"),
        ] = None,
        machine: MachineOption = None,
        last: LastOption = False,
        all_machines: Annotated[bool, typer.Option("--all-machines")] = False,
    ) -> None:
        if all_machines and machine is not None:
            exit_with_error("Use either --machine or --all-machines, not both.")
        if session is not None and (
            harness is not None or source_session_id is not None or last
        ):
            exit_with_error(
                "Use --session by itself, or use --harness/--source-session-id, or --last."
            )
        conn = open_toktrail_connection(ctx)
        try:
            area = ensure_area(conn, path)
            if session is not None:
                selected_machine_id, selected_harness, selected_source_session = (
                    _resolve_session_key_or_exit(conn, session_key=session, exit_with_error=exit_with_error)
                )
            elif last:
                if source_session_id is not None:
                    exit_with_error("Use either --last or --source-session-id, not both.")
                from toktrail.db import summarize_usage_sessions
                from toktrail.reporting import UsageSessionsFilter

                costing_config = load_costing_config_or_exit(ctx)
                default_machine_id = (
                    None
                    if all_machines
                    else (
                        resolve_machine_id_or_exit(conn, machine)
                        if machine is not None
                        else get_local_machine_id(conn)
                    )
                )
                latest = summarize_usage_sessions(
                    conn,
                    UsageSessionsFilter(
                        machine_id=default_machine_id,
                        harness=harness,
                        limit=1,
                        order="desc",
                    ),
                    costing_config=costing_config,
                ).sessions
                if not latest:
                    exit_with_error("No source session matched --last.")
                target = latest[0]
                selected_machine_id = target.origin_machine_id
                selected_harness = target.harness
                selected_source_session = target.source_session_id
            else:
                if harness is None or source_session_id is None:
                    exit_with_error(
                        "Provide --harness and --source-session-id, "
                        "or use --last or --session."
                    )
                selected_machine_id = _resolve_assignment_machine_id_or_exit(
                    conn,
                    harness=harness,
                    source_session_id=source_session_id,
                    machine=machine,
                    resolve_machine_id_or_exit=resolve_machine_id_or_exit,
                    exit_with_error=exit_with_error,
                )
                selected_harness = harness
                selected_source_session = source_session_id
            if selected_machine_id is None:
                exit_with_error("Source session has no origin machine id.")
            assign_area_to_source_session(
                conn,
                area_id=area.id,
                origin_machine_id=selected_machine_id,
                harness=selected_harness,
                source_session_id=selected_source_session,
            )
            conn.commit()
        except ValueError as exc:
            exit_with_error(str(exc))
        finally:
            conn.close()
        typer.echo(
            f"Assigned {area.path} to "
            f"{selected_harness}/{selected_source_session} ({selected_machine_id[:8]})."
        )

    @area_app.command("unassign")
    def area_unassign(
        ctx: typer.Context,
        session: Annotated[
            str | None,
            typer.Option(
                "--session",
                help="Session key machine/harness/source_session_id from usage sessions.",
            ),
        ] = None,
        harness: HarnessOption = None,
        source_session_id: Annotated[
            str | None,
            typer.Option("--source-session-id", "--source-session"),
        ] = None,
        machine: MachineOption = None,
    ) -> None:
        conn = open_toktrail_connection(ctx)
        try:
            if session is not None:
                if harness is not None or source_session_id is not None:
                    exit_with_error(
                        "Use --session by itself, or use --harness "
                        "with --source-session-id."
                    )
                machine_id, resolved_harness, resolved_source_session = (
                    _resolve_session_key_or_exit(conn, session_key=session, exit_with_error=exit_with_error)
                )
            else:
                if harness is None or source_session_id is None:
                    exit_with_error(
                        "Provide --harness and --source-session-id, or use --session."
                    )
                machine_id = _resolve_assignment_machine_id_or_exit(
                    conn,
                    harness=harness,
                    source_session_id=source_session_id,
                    machine=machine,
                    resolve_machine_id_or_exit=resolve_machine_id_or_exit,
                    exit_with_error=exit_with_error,
                )
                resolved_harness = harness
                resolved_source_session = source_session_id
            unassign_area_from_source_session(
                conn,
                origin_machine_id=machine_id,
                harness=resolved_harness,
                source_session_id=resolved_source_session,
            )
            conn.commit()
        except ValueError as exc:
            exit_with_error(str(exc))
        finally:
            conn.close()
        typer.echo(
            "Unassigned area from "
            f"{resolved_harness}/{resolved_source_session} ({machine_id[:8]})."
        )

    @area_app.command("sessions")
    def area_sessions(
        ctx: typer.Context,
        path: Annotated[
            str | None,
            typer.Argument(help="Area path.", show_default=False),
        ] = None,
        area: AreaOption = None,
        area_leaf: AreaLeafOption = None,
        exact: Annotated[bool, typer.Option("--exact")] = False,
        unassigned: Annotated[bool, typer.Option("--unassigned")] = False,
        recent: Annotated[int, typer.Option("--recent")] = 20,
        harness: HarnessOption = None,
        machine: MachineOption = None,
        today: Annotated[bool, typer.Option("--today")] = False,
        yesterday: Annotated[bool, typer.Option("--yesterday")] = False,
        this_week: Annotated[bool, typer.Option("--this-week")] = False,
        last_week: Annotated[bool, typer.Option("--last-week")] = False,
        this_month: Annotated[bool, typer.Option("--this-month")] = False,
        last_month: Annotated[bool, typer.Option("--last-month")] = False,
        period: UsagePeriodOption = None,
        timezone_name: TimezoneOption = None,
        utc: UtcOption = False,
        order: Annotated[str, typer.Option("--order")] = "desc",
        json_output: JsonOption = False,
        rich_output: RichOption = False,
    ) -> None:
        if path is not None and area is not None:
            exit_with_error("Use either the area path argument or --area, not both.")
        if recent < 1:
            exit_with_error("--recent must be positive.")
        selected_area_input = area if area is not None else path
        selected_area, area_match = usage_parts._resolve_area_filter_inputs_or_exit(
            area=selected_area_input,
            area_leaf=area_leaf,
            unassigned_area=unassigned,
        )
        selected_period = usage_parts._resolve_usage_session_period_or_exit(
            period=period,
            today=today,
            yesterday=yesterday,
            this_week=this_week,
            last_week=last_week,
            this_month=this_month,
            last_month=last_month,
        )
        try:
            resolved_range = resolve_time_range(
                period=selected_period,
                timezone_name=timezone_name,
                utc=utc,
            )
        except ValueError as exc:
            exit_with_error(str(exc))

        from toktrail.db import summarize_usage_sessions
        from toktrail.reporting import UsageSessionsFilter

        conn = open_toktrail_connection(ctx)
        try:
            machine_id = resolve_machine_id_or_exit(conn, machine)
            report = summarize_usage_sessions(
                conn,
                UsageSessionsFilter(
                    machine_id=machine_id,
                    harness=harness,
                    area=selected_area,
                    area_match=area_match,
                    area_exact=exact,
                    unassigned_area=unassigned,
                    since_ms=resolved_range.since_ms,
                    until_ms=resolved_range.until_ms,
                    limit=recent,
                    order=order,
                ),
                costing_config=load_costing_config_or_exit(ctx),
            )
        except ValueError as exc:
            exit_with_error(str(exc))
        finally:
            conn.close()
        if json_output:
            payload = report.as_dict()
            filters = payload.get("filters")
            if isinstance(filters, dict) and resolved_range.period is not None:
                filters["period"] = resolved_range.period
            typer.echo(json.dumps(payload, indent=2))
            return
        usage_parts._print_usage_sessions(
            report,
            compact=True,
            breakdown=False,
            utc=utc,
            rich_output=rich_output,
            table=True,
            period=resolved_range.period,
        )

    @area_app.command("detect")
    def area_detect(
        ctx: typer.Context,
        json_output: JsonOption = False,
    ) -> None:
        loaded = load_resolved_toktrail_config_or_exit(ctx)
        rules = loaded.config.areas.rules
        cwd = Path.cwd()
        cwd_text = str(cwd)
        git_remote = _git_remote_origin(cwd)
        matched: list[tuple[int, str, str]] = []
        for index, rule in enumerate(rules):
            for pattern in rule.cwd_globs:
                expanded = str(Path(pattern).expanduser())
                if fnmatch.fnmatch(cwd_text, expanded):
                    matched.append((rule.priority, rule.area, f"cwd matched {pattern}"))
                    break
            else:
                for remote_pattern in rule.git_remotes:
                    if git_remote and fnmatch.fnmatch(git_remote, remote_pattern):
                        matched.append(
                            (
                                rule.priority,
                                rule.area,
                                f"git remote matched {remote_pattern}",
                            )
                        )
                        break
            if matched and matched[-1][1] == rule.area:
                matched[-1] = (
                    matched[-1][0],
                    matched[-1][1],
                    f"{matched[-1][2]} (rule {index + 1})",
                )
        detected_area: str | None = None
        reason: str | None = None
        if matched:
            matched.sort(key=lambda item: (item[0], item[1]), reverse=True)
            _, detected_area, reason = matched[0]
        conn = open_toktrail_connection(ctx)
        try:
            active = get_active_area_status(conn)
        finally:
            conn.close()
        active_path = active.area.path if active.area is not None else None
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "cwd": cwd_text,
                        "git_remote": git_remote,
                        "detected_area": detected_area,
                        "reason": reason,
                        "active_area": active_path,
                        "suggested_command": (
                            f"toktrail area use {detected_area}"
                            if detected_area and detected_area != active_path
                            else None
                        ),
                    },
                    indent=2,
                )
            )
            return
        if detected_area is None:
            typer.echo("Detected area: none")
            return
        typer.echo(f"Detected area: {detected_area}")
        if reason is not None:
            typer.echo(f"Reason: {reason}")
        typer.echo(f"Active area: {active_path or 'none'}")
        if detected_area != active_path:
            typer.echo(f"Suggestion: toktrail area use {detected_area}")

    @area_app.command("bind-cwd")
    def area_bind_cwd(
        ctx: typer.Context,
        path: Annotated[str, typer.Argument(help="Area path.")],
        recursive: Annotated[bool, typer.Option("--recursive/--no-recursive")] = True,
        git_root: Annotated[bool, typer.Option("--git-root")] = False,
        dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    ) -> None:
        base = _resolve_git_root(Path.cwd()) if git_root else Path.cwd()
        if base is None:
            exit_with_error("Could not resolve git root.")
        glob = f"{base.expanduser()}/**" if recursive else str(base.expanduser())
        config_path = resolve_config_path(ctx)
        rendered = (
            "\n[areas]\n"
            "auto_detect = true\n"
            "warn_on_mismatch = true\n\n"
            "[[areas.rules]]\n"
            f'area = "{path}"\n'
            f'cwd_globs = ["{glob}"]\n'
            "priority = 100\n"
        )
        if dry_run:
            typer.echo(rendered.strip())
            return
        existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + rendered
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(updated, encoding="utf-8")
        typer.echo(f"Added cwd rule for {path} in {config_path}")

    @area_app.command("bulk-assign")
    def area_bulk_assign(
        ctx: typer.Context,
        path: Annotated[str, typer.Argument(help="Area path.")],
        unassigned: Annotated[bool, typer.Option("--unassigned")] = True,
        harness: HarnessOption = None,
        machine: MachineOption = None,
        today: Annotated[bool, typer.Option("--today")] = False,
        yesterday: Annotated[bool, typer.Option("--yesterday")] = False,
        this_week: Annotated[bool, typer.Option("--this-week")] = False,
        last_week: Annotated[bool, typer.Option("--last-week")] = False,
        this_month: Annotated[bool, typer.Option("--this-month")] = False,
        last_month: Annotated[bool, typer.Option("--last-month")] = False,
        period: UsagePeriodOption = None,
        apply: Annotated[bool, typer.Option("--apply")] = False,
        dry_run: Annotated[bool, typer.Option("--dry-run")] = True,
        overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    ) -> None:
        if apply and dry_run:
            exit_with_error("Use either --apply or --dry-run.")
        selected_period = usage_parts._resolve_usage_session_period_or_exit(
            period=period,
            today=today,
            yesterday=yesterday,
            this_week=this_week,
            last_week=last_week,
            this_month=this_month,
            last_month=last_month,
        )
        try:
            resolved_range = resolve_time_range(period=selected_period)
        except ValueError as exc:
            exit_with_error(str(exc))
        from toktrail.db import summarize_usage_sessions
        from toktrail.reporting import UsageSessionsFilter

        conn = open_toktrail_connection(ctx)
        try:
            machine_id = resolve_machine_id_or_exit(conn, machine)
            area = ensure_area(conn, path)
            report = summarize_usage_sessions(
                conn,
                UsageSessionsFilter(
                    machine_id=machine_id,
                    harness=harness,
                    unassigned_area=unassigned,
                    since_ms=resolved_range.since_ms,
                    until_ms=resolved_range.until_ms,
                    limit=None,
                    order="desc",
                ),
                costing_config=load_costing_config_or_exit(ctx),
            )
            candidates = list(report.sessions)
            if dry_run or not apply:
                typer.echo(
                    f"Would assign {len(candidates)} source sessions to {area.path}:"
                )
                for session in candidates:
                    typer.echo(
                        f"- {session.key}  "
                        f"last={format_epoch_ms_compact(session.last_ms)}  "
                        f"total={format_int(session.tokens.total)}"
                    )
                typer.echo("Use --apply to write changes.")
                return
            assigned = 0
            skipped = 0
            for session in candidates:
                if session.origin_machine_id is None:
                    skipped += 1
                    continue
                if session.area_id is not None and not overwrite:
                    skipped += 1
                    continue
                assign_area_to_source_session(
                    conn,
                    area_id=area.id,
                    origin_machine_id=session.origin_machine_id,
                    harness=session.harness,
                    source_session_id=session.source_session_id,
                )
                assigned += 1
            conn.commit()
        finally:
            conn.close()
        typer.echo(f"Assigned {assigned} sessions to {path}; skipped {skipped}.")

    @area_app.command("archive")
    def area_archive(
        ctx: typer.Context,
        path: Annotated[str, typer.Argument(help="Area path to archive.")],
    ) -> None:
        conn = open_toktrail_connection(ctx)
        try:
            archived = archive_area_path(conn, path)
            conn.commit()
        except ValueError as exc:
            exit_with_error(str(exc))
        finally:
            conn.close()
        typer.echo(f"Archived {archived} area rows under {path}.")

    @area_app.command("move")
    def area_move(
        ctx: typer.Context,
        old_path: Annotated[str, typer.Argument(help="Current area path.")],
        new_path: Annotated[str, typer.Argument(help="New area path.")],
    ) -> None:
        conn = open_toktrail_connection(ctx)
        try:
            moved_assignments, moved_events = move_area_path(conn, old_path, new_path)
            conn.commit()
        except ValueError as exc:
            exit_with_error(str(exc))
        finally:
            conn.close()
        typer.echo(
            f"Moved {old_path} -> {new_path}; reassigned {moved_assignments} assignments "
            f"and {moved_events} events."
        )

    @area_app.command("rename")
    def area_rename(
        ctx: typer.Context,
        old_path: Annotated[str, typer.Argument(help="Current area path.")],
        new_name: Annotated[
            str, typer.Argument(help="New leaf name or replacement path.")
        ],
    ) -> None:
        normalized_old, _ = normalize_area_path(old_path)
        if "/" in new_name:
            destination = new_name
        else:
            parent = normalized_old.rsplit("/", 1)[0] if "/" in normalized_old else ""
            destination = f"{parent}/{new_name}" if parent else new_name
        area_move(ctx, old_path=normalized_old, new_path=destination)

    @area_app.command("merge")
    def area_merge(
        ctx: typer.Context,
        target_path: Annotated[str, typer.Argument(help="Target area path.")],
        source_path: Annotated[str, typer.Argument(help="Source area path to merge.")],
        dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    ) -> None:
        if dry_run:
            typer.echo(f"Would merge {source_path} into {target_path}.")
            return
        conn = open_toktrail_connection(ctx)
        try:
            moved_assignments, moved_events = merge_area_paths(
                conn,
                target_path,
                source_path,
            )
            conn.commit()
        except ValueError as exc:
            exit_with_error(str(exc))
        finally:
            conn.close()
        typer.echo(
            f"Merged {source_path} into {target_path}; reassigned {moved_assignments} "
            f"assignments and {moved_events} events."
        )


def _resolve_assignment_machine_id_or_exit(
    conn: sqlite3.Connection,
    *,
    harness: str,
    source_session_id: str,
    machine: str | None,
    resolve_machine_id_or_exit: ResolveMachineId,
    exit_with_error: ExitWithError,
) -> str:
    if machine is not None:
        resolved = resolve_machine_id_or_exit(conn, machine)
        if resolved is None:
            msg = "Machine selector did not resolve to a machine id."
            raise TypeError(msg)
        return resolved
    rows = conn.execute(
        """
        SELECT DISTINCT origin_machine_id
        FROM usage_events
        WHERE harness = ?
          AND source_session_id = ?
          AND origin_machine_id IS NOT NULL
        ORDER BY origin_machine_id
        """,
        (harness, source_session_id),
    ).fetchall()
    if not rows:
        exit_with_error(
            f"No imported source session matched {harness}/{source_session_id}."
        )
    if len(rows) > 1:
        exit_with_error("Source session matched multiple machines. Specify --machine.")
    value = rows[0]["origin_machine_id"]
    if not isinstance(value, str):
        msg = "Expected origin_machine_id to be a string."
        raise TypeError(msg)
    return value


def _resolve_session_key_or_exit(
    conn: sqlite3.Connection,
    *,
    session_key: str,
    exit_with_error: ExitWithError,
) -> tuple[str, str, str]:
    parts = session_key.split("/", 2)
    if len(parts) != 3:
        exit_with_error(
            "Session key must be machine/harness/source_session_id, "
            f"got {session_key!r}."
        )
    machine_selector, harness, source_session_id = (
        parts[0].strip(),
        parts[1].strip(),
        parts[2].strip(),
    )
    if not machine_selector or not harness or not source_session_id:
        exit_with_error(
            "Session key must be machine/harness/source_session_id "
            "with non-empty segments."
        )
    selector_candidates = [machine_selector]
    if machine_selector.startswith("machine:"):
        selector_candidates.append(machine_selector.split(":", 1)[1])
    if machine_selector.endswith(")") and "(" in machine_selector:
        selector_candidates.append(machine_selector.rsplit("(", 1)[1].rstrip(")"))
    machine_id: str | None = None
    last_error: ValueError | None = None
    for candidate in selector_candidates:
        try:
            machine_id = resolve_machine_selector(conn, candidate).machine_id
            break
        except ValueError as exc:
            last_error = exc
    if machine_id is None:
        exit_with_error(
            str(last_error)
            if last_error is not None
            else "Invalid machine selector in session key."
        )
    return machine_id, harness, source_session_id


def _parse_area_expiry_or_exit(
    *,
    ttl: str | None,
    until: str | None,
    exit_with_error: ExitWithError,
) -> int | None:
    if ttl is None and until is None:
        return None
    now_ms = int(time.time() * 1000)
    if ttl is not None:
        ttl_text = ttl.strip().lower()
        if not ttl_text:
            exit_with_error("--ttl must not be empty.")
        token_pattern = re.compile(r"(\d+)([smhd])")
        offset_ms = 0
        consumed = 0
        for match in token_pattern.finditer(ttl_text):
            value = int(match.group(1))
            unit = match.group(2)
            consumed += len(match.group(0))
            multiplier = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]
            offset_ms += value * multiplier
        if consumed != len(ttl_text) or offset_ms <= 0:
            exit_with_error("--ttl must look like 30m, 4h, 1d, or 1h30m.")
        return now_ms + offset_ms
    assert until is not None
    raw = until.strip()
    if not raw:
        exit_with_error("--until must not be empty.")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        exit_with_error("--until must be an ISO datetime, e.g. 2026-05-16T18:00.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
    expires_at_ms = int(parsed.timestamp() * 1000)
    if expires_at_ms <= now_ms:
        exit_with_error("--until must be in the future.")
    return expires_at_ms


def _resolve_git_root(cwd: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = completed.stdout.strip()
    return Path(text) if text else None


def _git_remote_origin(cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "config", "--get", "remote.origin.url"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value or None
