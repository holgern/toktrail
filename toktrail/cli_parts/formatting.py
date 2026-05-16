from __future__ import annotations

from decimal import Decimal

from toktrail.models import TokenBreakdown


def _format_percent(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value.quantize(Decimal('0.1'))}%"


def _format_signed_int(value: int) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{_format_int(abs(value))}"


def _format_token_delta(tokens: TokenBreakdown) -> str:
    return (
        f"{_format_signed_int(tokens.total)} tokens "
        f"in={_format_int(tokens.input)} "
        f"out={_format_int(tokens.output)} "
        f"reasoning={_format_int(tokens.reasoning)} "
        f"cache_r={_format_int(tokens.cache_read)} "
        f"cache_w={_format_int(tokens.cache_write)} "
        f"cache_o={_format_int(tokens.cache_output)}"
    )


def _format_ratio_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_cost_precise(value: Decimal | float) -> str:
    return f"${float(value):.4f}"


def _format_cost_or_dash(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return _format_cost_precise(value)


def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_cost(value: Decimal | float) -> str:
    return f"${float(value):.2f}"


def _format_price(value: float | None, *, fallback: str | None = None) -> str:
    if value is None:
        return f"={fallback}" if fallback is not None else "-"
    return f"${value:.2f}"
