from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, cast

import typer

from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.cli_parts.table import _print_table
from toktrail.config import LoadedMachineConfig
from toktrail.db import (
    apply_local_machine_config,
    get_local_machine_id,
    get_machine,
    list_machines,
    set_local_machine_name,
    summarize_usage,
)
from toktrail.formatting import format_epoch_ms_compact
from toktrail.models import TokenBreakdown
from toktrail.reporting import CostTotals, UsageReportFilter

LoadMachineConfig = Callable[[typer.Context], LoadedMachineConfig]
OpenConnection = Callable[[typer.Context], sqlite3.Connection]
ResolveMachineConfigPath = Callable[[typer.Context], Path]
ExitWithError = Callable[[str], None]


def register_machine_commands(
    machine_app: typer.Typer,
    *,
    load_machine_config_or_exit: LoadMachineConfig,
    open_toktrail_connection: OpenConnection,
    resolve_machine_config_path: ResolveMachineConfigPath,
    exit_with_error: ExitWithError,
) -> None:
    @machine_app.command("status")
    def machine_status(
        ctx: typer.Context,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        loaded_machine = load_machine_config_or_exit(ctx)
        conn = open_toktrail_connection(ctx)
        try:
            local_machine_id = get_local_machine_id(conn)
            machine = get_machine(conn, local_machine_id)
        finally:
            conn.close()
        if machine is None:
            exit_with_error("Local machine record not found.")
        if json_output:
            payload = {
                "machine_id": machine.machine_id,
                "name": machine.name,
                "name_key": machine.name_key,
                "first_seen_ms": machine.first_seen_ms,
                "last_seen_ms": machine.last_seen_ms,
                "is_local": machine.is_local,
                "created_at_ms": machine.created_at_ms,
                "updated_at_ms": machine.updated_at_ms,
                "imported_at_ms": machine.imported_at_ms,
                "label": machine.label,
                "config_path": str(loaded_machine.path),
                "config_exists": loaded_machine.exists,
            }
            typer.echo(json.dumps(payload, indent=2))
            return
        typer.echo(f"id:    {machine.machine_id}")
        typer.echo(f"name:  {machine.name or machine.label}")
        typer.echo("local: yes")

    @machine_app.command("list")
    def machine_list(
        ctx: typer.Context,
        json_output: Annotated[bool, typer.Option("--json")] = False,
        utc: Annotated[bool, typer.Option("--utc")] = False,
        rich_output: Annotated[
            bool,
            typer.Option(
                "--rich",
                help="Render tables with Rich formatting. Default is borderless.",
            ),
        ] = False,
    ) -> None:
        conn = open_toktrail_connection(ctx)
        try:
            machines = list_machines(conn)
            report = summarize_usage(conn, UsageReportFilter())
            usage_by_machine = {row.machine_id: row for row in report.by_machine}
        finally:
            conn.close()
        rows_payload = []
        for machine in machines:
            usage = usage_by_machine.get(machine.machine_id)
            rows_payload.append(
                {
                    "machine": machine.label,
                    "machine_id": machine.machine_id,
                    "local": machine.is_local,
                    "first_seen_ms": machine.first_seen_ms,
                    "last_seen_ms": machine.last_seen_ms,
                    "message_count": 0 if usage is None else usage.message_count,
                    "tokens": TokenBreakdown() if usage is None else usage.tokens,
                    "costs": CostTotals() if usage is None else usage.costs,
                }
            )
        if json_output:
            typer.echo(
                json.dumps(
                    [
                        {
                            "machine": row["machine"],
                            "machine_id": row["machine_id"],
                            "local": row["local"],
                            "first_seen_ms": row["first_seen_ms"],
                            "last_seen_ms": row["last_seen_ms"],
                            "message_count": row["message_count"],
                            "tokens": cast(TokenBreakdown, row["tokens"]).as_dict(),
                            "costs": cast(CostTotals, row["costs"]).as_dict(),
                        }
                        for row in rows_payload
                    ],
                    indent=2,
                )
            )
            return
        _print_table(
            [
                {
                    "machine": str(row["machine"]),
                    "id": str(row["machine_id"])[:8],
                    "local": "yes" if bool(row["local"]) else "no",
                    "first_seen": format_epoch_ms_compact(
                        cast(int, row["first_seen_ms"]), utc=utc
                    ),
                    "last_seen": format_epoch_ms_compact(
                        cast(int, row["last_seen_ms"]), utc=utc
                    ),
                    "msgs": _format_int(cast(int, row["message_count"])),
                    "total": _format_int(cast(TokenBreakdown, row["tokens"]).total),
                    "actual": _format_cost(
                        cast(CostTotals, row["costs"]).actual_cost_usd
                    ),
                    "virtual": _format_cost(
                        cast(CostTotals, row["costs"]).virtual_cost_usd
                    ),
                }
                for row in rows_payload
            ],
            [
                "machine",
                "id",
                "local",
                "first_seen",
                "last_seen",
                "msgs",
                "total",
                "actual",
                "virtual",
            ],
            {
                "machine": "machine",
                "id": "id",
                "local": "local",
                "first_seen": "first seen",
                "last_seen": "last seen",
                "msgs": "msgs",
                "total": "total",
                "actual": "actual",
                "virtual": "virtual",
            },
            rich_output=rich_output,
            numeric_columns={"msgs", "total", "actual", "virtual"},
        )

    @machine_app.command("set-name")
    def machine_set_name(ctx: typer.Context, name: str) -> None:
        cleaned_name = name.strip()
        if not cleaned_name:
            exit_with_error("Machine name must not be empty.")
        path = resolve_machine_config_path(ctx)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[machine]\nname = " + json.dumps(cleaned_name) + "\n", encoding="utf-8"
        )
        conn = open_toktrail_connection(ctx)
        try:
            set_local_machine_name(conn, cleaned_name)
            conn.commit()
        finally:
            conn.close()
        typer.echo(f"Updated machine name: {cleaned_name}")

    @machine_app.command("clear-name")
    def machine_clear_name(ctx: typer.Context) -> None:
        path = resolve_machine_config_path(ctx)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[machine]\n", encoding="utf-8")
        conn = open_toktrail_connection(ctx)
        try:
            loaded_machine = load_machine_config_or_exit(ctx)
            apply_local_machine_config(conn, loaded_machine.config)
            conn.commit()
        finally:
            conn.close()
        typer.echo("Cleared machine name.")
