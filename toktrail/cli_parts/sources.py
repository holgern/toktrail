from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import typer

from toktrail.adapters.registry import get_harness
from toktrail.api.sources import capture_source_snapshot
from toktrail.errors import ToktrailError


def sources_list_impl(
    *,
    ctx: typer.Context,
    harnesses: list[str] | None,
    source_path: Path | None,
    json_output: bool,
    load_resolved_toktrail_config_or_exit_fn: Callable[[typer.Context], object],
    exit_with_error_fn: Callable[[str], None],
    format_int_fn: Callable[[int], str],
    print_table_fn: Callable[..., None],
) -> None:
    loaded = load_resolved_toktrail_config_or_exit_fn(ctx)
    selected_harnesses = tuple(harnesses or loaded.config.imports.harnesses)
    configured_sources = loaded.config.imports.sources or {}
    if source_path is not None and len(selected_harnesses) != 1:
        exit_with_error_fn("--source can only be used with exactly one --harness.")

    rows: list[dict[str, object]] = []
    for harness in sorted(selected_harnesses):
        try:
            configured_source = configured_sources.get(harness)
            selected_source = (
                source_path if source_path is not None else configured_source
            )
            if isinstance(selected_source, list):
                selected_source = selected_source[0] if selected_source else None
            snapshot = capture_source_snapshot(
                harness,
                source_path=selected_source,
                config_path=loaded.config_path,
            )
        except (OSError, ValueError, ToktrailError) as exc:
            rows.append(
                {
                    "harness": harness,
                    "source_path": str(source_path or ""),
                    "exists": False,
                    "sessions": 0,
                    "messages": 0,
                    "tokens": 0,
                    "warning": str(exc),
                }
            )
            continue
        resolved = snapshot.source_path
        exists = bool(resolved is not None and resolved.exists())
        rows.append(
            {
                "harness": harness,
                "source_path": str(resolved) if resolved is not None else "",
                "exists": exists,
                "sessions": len(snapshot.sessions),
                "messages": sum(
                    summary.assistant_message_count for summary in snapshot.sessions
                ),
                "tokens": sum(summary.tokens.total for summary in snapshot.sessions),
                "warning": "" if exists else "source not found",
                "config_key": get_harness(harness).config_key,
                "id_prefix": get_harness(harness).id_prefix,
                "watch_subdirs": list(get_harness(harness).watch_subdirs),
                "file_based": get_harness(harness).file_based,
                "effective_roots": [str(resolved)] if resolved is not None else [],
            }
        )

    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return

    payload_rows = [
        {
            "harness": str(row["harness"]),
            "exists": "yes" if bool(row["exists"]) else "no",
            "sessions": format_int_fn(cast(int, row["sessions"])),
            "messages": format_int_fn(cast(int, row["messages"])),
            "tokens": format_int_fn(cast(int, row["tokens"])),
            "source_path": str(row["source_path"]),
            "warning": str(row["warning"]),
            "config_key": str(row.get("config_key") or ""),
            "id_prefix": str(row.get("id_prefix") or ""),
        }
        for row in rows
    ]
    print_table_fn(
        payload_rows,
        [
            "harness",
            "exists",
            "sessions",
            "messages",
            "tokens",
            "source_path",
            "config_key",
            "id_prefix",
            "warning",
        ],
        {
            "harness": "harness",
            "exists": "exists",
            "sessions": "sessions",
            "messages": "messages",
            "tokens": "tokens",
            "source_path": "source_path",
            "config_key": "config_key",
            "id_prefix": "id_prefix",
            "warning": "warning",
        },
        rich_output=False,
    )
