from __future__ import annotations

import typer

from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.reporting import ModelSummaryRow, UnconfiguredModelRow


def _render_table(
    rows: list[dict[str, str]],
    columns: list[str],
    headers: dict[str, str],
    *,
    numeric_columns: set[str] | None = None,
) -> str:
    numeric = numeric_columns or set()
    widths = {
        column: max([len(headers[column]), *(len(row.get(column, "")) for row in rows)])
        for column in columns
    }

    def _cell(column: str, value: str) -> str:
        width = widths[column]
        if column in numeric:
            return value.rjust(width)
        return value.ljust(width)

    lines = ["  ".join(_cell(column, headers[column]) for column in columns)]
    for row in rows:
        lines.append("  ".join(_cell(column, row.get(column, "")) for column in columns))
    return "\n".join(lines)


def _print_table(
    rows: list[dict[str, str]],
    columns: list[str],
    headers: dict[str, str],
    *,
    rich_output: bool,
    numeric_columns: set[str] | None = None,
    wrap_columns: set[str] | None = None,
    max_widths: dict[str, int] | None = None,
) -> None:
    numeric = numeric_columns or set()
    wrap = wrap_columns or set()
    widths = max_widths or {}
    if not rich_output:
        typer.echo(_render_table(rows, columns, headers, numeric_columns=numeric))
        return

    try:
        from rich import box
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        typer.secho(
            "Rich output requires installing toktrail[rich].",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from None

    table = Table(
        show_header=True,
        header_style="bold",
        box=box.ROUNDED,
        show_lines=False,
        expand=False,
    )
    for column in columns:
        table.add_column(
            headers[column],
            justify="right" if column in numeric else "left",
            no_wrap=column not in wrap,
            overflow="fold" if column in wrap else "ellipsis",
            max_width=widths.get(column),
        )
    for row in rows:
        table.add_row(*(row.get(column, "") for column in columns))
    Console().print(table)


def _print_model_table(
    rows: list[ModelSummaryRow],
    *,
    rich_output: bool,
) -> None:
    headers = {
        "provider_model": "provider/model",
        "thinking": "thinking",
        "msgs": "msgs",
        "input": "input",
        "output": "output",
        "reasoning": "reasoning",
        "cache_r": "cache_r",
        "cache_w": "cache_w",
        "cache_o": "cache_o",
        "total": "total",
        "actual": "actual",
        "virtual": "virtual",
        "savings": "savings",
    }
    include_thinking = any(row.thinking_level is not None for row in rows)
    payload_rows = [
        {
            "provider_model": f"{row.provider_id}/{row.model_id}",
            "thinking": row.thinking_level or "-",
            "msgs": _format_int(row.message_count),
            "input": _format_int(row.tokens.input),
            "output": _format_int(row.tokens.output),
            "reasoning": _format_int(row.tokens.reasoning),
            "cache_r": _format_int(row.tokens.cache_read),
            "cache_w": _format_int(row.tokens.cache_write),
            "cache_o": _format_int(row.tokens.cache_output),
            "total": _format_int(row.total_tokens),
            "actual": _format_cost(row.actual_cost_usd),
            "virtual": _format_cost(row.virtual_cost_usd),
            "savings": _format_cost(row.savings_usd),
        }
        for row in rows
    ]
    columns = ["provider_model"]
    if include_thinking:
        columns.append("thinking")
    columns.extend(
        [
            "msgs",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "cache_o",
            "total",
            "actual",
            "virtual",
            "savings",
        ]
    )
    _print_table(
        payload_rows,
        columns,
        headers,
        rich_output=rich_output,
        numeric_columns={
            "msgs",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "cache_o",
            "total",
            "actual",
            "virtual",
            "savings",
        },
    )


def _print_unconfigured_model_table(
    rows: list[UnconfiguredModelRow],
    *,
    rich_output: bool,
) -> None:
    headers = {
        "required": "required",
        "provider_model": "provider/model",
        "harness": "harness",
        "thinking": "thinking",
        "msgs": "msgs",
        "input": "input",
        "output": "output",
        "reasoning": "reasoning",
        "cache_r": "cache_r",
        "cache_w": "cache_w",
        "cache_o": "cache_o",
        "total": "total",
    }
    include_thinking = any(row.thinking_level is not None for row in rows)
    payload_rows = [
        {
            "required": "+".join(row.required),
            "provider_model": f"{row.provider_id}/{row.model_id}",
            "harness": row.harness,
            "thinking": row.thinking_level or "-",
            "msgs": _format_int(row.message_count),
            "input": _format_int(row.tokens.input),
            "output": _format_int(row.tokens.output),
            "reasoning": _format_int(row.tokens.reasoning),
            "cache_r": _format_int(row.tokens.cache_read),
            "cache_w": _format_int(row.tokens.cache_write),
            "cache_o": _format_int(row.tokens.cache_output),
            "total": _format_int(row.total_tokens),
        }
        for row in rows
    ]
    columns = ["required", "provider_model", "harness"]
    if include_thinking:
        columns.append("thinking")
    columns.extend(
        [
            "msgs",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "cache_o",
            "total",
        ]
    )
    _print_table(
        payload_rows,
        columns,
        headers,
        rich_output=rich_output,
        numeric_columns={
            "msgs",
            "input",
            "output",
            "reasoning",
            "cache_r",
            "cache_w",
            "cache_o",
            "total",
        },
    )

