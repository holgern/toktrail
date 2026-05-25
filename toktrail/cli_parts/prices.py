from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn, cast

import typer

from toktrail.cli_parts.filters import (
    _filter_model_rows,
    _filter_price_rows,
    _filter_unconfigured_models,
    _normalize_price_display_filter,
)
from toktrail.cli_parts.table import (
    _print_model_table,
    _print_unconfigured_model_table,
)
from toktrail.config import LoadedCostingConfig, normalize_identity
from toktrail.db import summarize_usage
from toktrail.price_parser import (
    merge_prices_document,
    parse_price_document,
    render_prices_toml,
)
from toktrail.reporting import UsageReportFilter


@dataclass(frozen=True)
class PricesRuntime:
    refresh_before_report: Callable[..., list[object]]
    load_costing_config_or_exit: Callable[[typer.Context], object]
    open_toktrail_connection: Callable[[typer.Context], sqlite3.Connection]
    wrap_refresh_json_payload: Callable[..., dict[str, object]]
    load_resolved_costing_config_or_exit: Callable[[typer.Context], LoadedCostingConfig]
    resolve_prices_dir: Callable[[typer.Context], Path]
    exit_with_error: Callable[[str], NoReturn]


_RUNTIME: PricesRuntime | None = None


def configure_prices_runtime(runtime: PricesRuntime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def _runtime() -> PricesRuntime:
    if _RUNTIME is None:
        msg = "prices runtime is not configured"
        raise RuntimeError(msg)
    return _RUNTIME


def _refresh_before_report(*args: object, **kwargs: object) -> list[object]:
    return _runtime().refresh_before_report(*args, **kwargs)


def _load_costing_config_or_exit(ctx: typer.Context) -> object:
    return _runtime().load_costing_config_or_exit(ctx)


def _open_toktrail_connection(ctx: typer.Context) -> sqlite3.Connection:
    return _runtime().open_toktrail_connection(ctx)


def _wrap_refresh_json_payload(
    payload: object,
    *,
    refresh_results: list[object],
    include_refresh: bool,
) -> dict[str, object]:
    return _runtime().wrap_refresh_json_payload(
        payload,
        refresh_results=refresh_results,
        include_refresh=include_refresh,
    )


def _load_resolved_costing_config_or_exit(ctx: typer.Context) -> LoadedCostingConfig:
    return _runtime().load_resolved_costing_config_or_exit(ctx)


def _resolve_prices_dir(ctx: typer.Context) -> Path:
    return _runtime().resolve_prices_dir(ctx)


def _exit_with_error(message: str) -> NoReturn:
    _runtime().exit_with_error(message)


def pricing_list(
    ctx: typer.Context,
    *,
    used_only: bool,
    missing_only: bool,
    table: str,
    provider: str | None,
    model: str | None,
    query: str | None,
    category: str | None,
    release_status: str | None,
    sort: str,
    limit: int | None,
    aliases: bool,
    json_output: bool,
    rich_output: bool,
    refresh: bool,
    refresh_details: bool,
    raw: bool | None,
    price_rows_fn: Callable[[object, str], list[dict[str, object]]],
    print_price_table_fn: Callable[..., None],
) -> None:
    if used_only and missing_only:
        _exit_with_error("Use either --used-only or --missing-only, not both.")
    if used_only or missing_only:
        refresh_results = _refresh_before_report(
            ctx,
            enabled=refresh,
            details=refresh_details,
            json_output=json_output,
            include_raw_json=raw,
        )
        costing_config = _load_costing_config_or_exit(ctx)
        conn = _open_toktrail_connection(ctx)
        try:
            report = summarize_usage(
                conn,
                UsageReportFilter(tracking_session_id=None),
                costing_config=costing_config,
            )
        finally:
            conn.close()
        if missing_only:
            rows = _filter_unconfigured_models(
                report.unconfigured_models,
                price_state="unpriced",
                min_messages=None,
                min_tokens=None,
            )
            if json_output:
                typer.echo(
                    json.dumps(
                        _wrap_refresh_json_payload(
                            [row.as_dict() for row in rows],
                            refresh_results=refresh_results,
                            include_refresh=refresh_details,
                        ),
                        indent=2,
                    )
                )
                return
            _print_unconfigured_model_table(rows, rich_output=rich_output)
            return
        model_rows = _filter_model_rows(
            report.by_model,
            price_state="all",
            min_messages=None,
            min_tokens=None,
            sort="provider",
            limit=limit,
        )
        if json_output:
            typer.echo(
                json.dumps(
                    _wrap_refresh_json_payload(
                        [row.as_dict() for row in model_rows],
                        refresh_results=refresh_results,
                        include_refresh=refresh_details,
                    ),
                    indent=2,
                )
            )
            return
        _print_model_table(model_rows, rich_output=rich_output)
        return

    loaded = _load_resolved_costing_config_or_exit(ctx)
    try:
        filters = _normalize_price_display_filter(
            table=table,
            provider=provider,
            model=model,
            query=query,
            category=category,
            release_status=release_status,
            sort=sort,
            limit=limit,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))
    price_rows = _filter_price_rows(
        price_rows_fn(loaded.config, filters.table), filters
    )
    if json_output:
        typer.echo(json.dumps(price_rows, indent=2))
        return
    print_price_table_fn(price_rows, aliases=aliases, rich_output=rich_output)


def _default_pricing_parse_output_path(ctx: typer.Context, provider: str) -> Path:
    return _resolve_prices_dir(ctx) / f"{normalize_identity(provider)}.toml"


def _is_provider_price_file(ctx: typer.Context, target: Path, provider: str) -> bool:
    expected = _default_pricing_parse_output_path(ctx, provider)
    try:
        return target.resolve() == expected.resolve()
    except OSError:
        return target.absolute() == expected.absolute()


def pricing_parse(
    ctx: typer.Context,
    *,
    provider: str,
    table: str,
    tier: str,
    input_path: Path | None,
    output_path: str | None,
    merge: bool,
    replace_provider: bool,
    dry_run: bool,
    json_output: bool,
) -> None:
    if table not in {"virtual", "actual"}:
        _exit_with_error("--table must be one of: virtual, actual.")
    if merge and replace_provider:
        _exit_with_error("Use either --merge or --replace-provider, not both.")
    if input_path is None:
        text = typer.get_text_stream("stdin").read()
    else:
        text = input_path.read_text(encoding="utf-8")

    try:
        parsed = parse_price_document(
            text,
            provider=provider,
            table=cast(Literal["virtual", "actual"], table),
            tier=tier,
        )
    except ValueError as exc:
        _exit_with_error(str(exc))

    if output_path is None:
        target = _default_pricing_parse_output_path(ctx, provider)
    elif output_path == "-":
        target = None
    else:
        raw = Path(output_path).expanduser()
        target = (
            raw / f"{normalize_identity(provider)}.toml"
            if raw.exists() and raw.is_dir()
            else raw
        )

    if target is None and (merge or replace_provider):
        _exit_with_error("--merge and --replace-provider require file output.")

    write_mode = "stdout" if target is None else "render"
    if target is not None and target.exists():
        if merge:
            write_mode = "merge"
        elif (
            replace_provider
            or output_path is None
            or _is_provider_price_file(ctx, target, provider)
        ):
            write_mode = "replace-provider"
        else:
            _exit_with_error(
                f"Refusing to overwrite existing {target}; pass --merge, "
                "--replace-provider, or --output - for stdout."
            )
    elif target is not None:
        if merge:
            write_mode = "merge"
        elif replace_provider:
            write_mode = "replace-provider"

    include_metadata = target is not None and _is_provider_price_file(
        ctx, target, provider
    )
    source_label = str(input_path) if input_path is not None else "stdin"
    metadata = (
        {
            "generated_by": "toktrail prices parse",
            "provider": normalize_identity(provider),
            "source": source_label,
            "tier": tier,
        }
        if include_metadata
        else None
    )

    if write_mode in {"merge", "replace-provider"}:
        existing_text = (
            target.read_text(encoding="utf-8")
            if target is not None and target.exists()
            else None
        )
        output_text = merge_prices_document(
            existing_text=existing_text,
            parsed=parsed,
            replace_provider=(write_mode == "replace-provider"),
            metadata=metadata,
        )
    else:
        output_text = render_prices_toml(
            virtual_prices=parsed.prices if parsed.table == "virtual" else (),
            actual_prices=parsed.prices if parsed.table == "actual" else (),
            metadata=metadata,
        )

    wrote = False
    if target is not None and not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output_text, encoding="utf-8")
        wrote = True

    if json_output:
        payload = {
            "provider": parsed.provider,
            "table": parsed.table,
            "price_count": len(parsed.prices),
            "warnings": list(parsed.warnings),
            "output": str(target) if target is not None else "-",
            "out": str(target) if target is not None else "-",
            "wrote": wrote,
            "dry_run": dry_run,
            "mode": write_mode,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    if target is None or dry_run:
        typer.echo(output_text)
    else:
        typer.echo(f"Wrote prices TOML: {target}")
    for warning in parsed.warnings:
        typer.echo(f"warning: {warning}", err=True)
