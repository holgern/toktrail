from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

from toktrail import config as config_module
from toktrail.api._files import atomic_write_text
from toktrail.api.model_parts.core_models import PriceRow, UnconfiguredModelRow
from toktrail.api.paths import (
    resolve_toktrail_config_path,
    resolve_toktrail_db_path,
    resolve_toktrail_prices_dir,
    resolve_toktrail_prices_path,
)
from toktrail.api.reports import usage_report
from toktrail.config import Price
from toktrail.errors import ConfigurationError, InvalidAPIUsageError
from toktrail.price_parser import render_prices_toml


def _resolve_costing_paths(
    *,
    config_path: Path | None,
    prices_path: Path | None,
    prices_dir: Path | None,
) -> tuple[Path, Path, Path, Path]:
    resolved_config = resolve_toktrail_config_path(config_path)
    resolved_prices = (
        resolve_toktrail_prices_path(prices_path)
        if prices_path is not None
        else resolved_config.with_name("prices.toml")
    )
    resolved_prices_dir = (
        resolve_toktrail_prices_dir(prices_dir)
        if prices_dir is not None
        else resolved_config.with_name("prices")
    )
    resolved_subscriptions = resolved_config.with_name("subscriptions.toml")
    return resolved_config, resolved_prices, resolved_prices_dir, resolved_subscriptions


def _price_row_source_kind(
    include_provider_prices: bool,
) -> Literal["manual", "effective"]:
    return "effective" if include_provider_prices else "manual"


def _to_price_row(
    *,
    table: Literal["actual", "virtual"],
    source_kind: Literal["manual", "provider", "effective"],
    source_path: Path | None,
    price: Price,
) -> PriceRow:
    return PriceRow(
        table=table,
        provider=price.provider,
        model=price.model,
        input_usd_per_1m=price.input_usd_per_1m,
        output_usd_per_1m=price.output_usd_per_1m,
        cached_input_usd_per_1m=price.cached_input_usd_per_1m,
        cache_write_usd_per_1m=price.cache_write_usd_per_1m,
        cached_output_usd_per_1m=price.cached_output_usd_per_1m,
        reasoning_usd_per_1m=price.reasoning_usd_per_1m,
        context_min_tokens=price.context_min_tokens,
        context_max_tokens=price.context_max_tokens,
        context_label=price.context_label,
        context_basis=price.context_basis,
        category=price.category,
        release_status=price.release_status,
        aliases=price.aliases,
        source_path=str(source_path) if source_path is not None else None,
        source_kind=source_kind,
    )


def _variant_key(
    *,
    provider: str,
    model: str,
    context_min_tokens: int | None,
    context_max_tokens: int | None,
    context_basis: str,
) -> tuple[str, str, int | None, int | None, str]:
    return (
        config_module.normalize_identity(provider),
        config_module.normalize_identity(model),
        context_min_tokens,
        context_max_tokens,
        context_basis,
    )


def _validate_non_negative(name: str, value: float | None) -> None:
    if value is not None and value < 0:
        msg = f"{name} must be non-negative, got {value!r}"
        raise InvalidAPIUsageError(msg)


def _validate_price_row(row: PriceRow) -> None:
    _validate_non_negative("input_usd_per_1m", row.input_usd_per_1m)
    _validate_non_negative("output_usd_per_1m", row.output_usd_per_1m)
    _validate_non_negative("cached_input_usd_per_1m", row.cached_input_usd_per_1m)
    _validate_non_negative("cache_write_usd_per_1m", row.cache_write_usd_per_1m)
    _validate_non_negative("cached_output_usd_per_1m", row.cached_output_usd_per_1m)
    _validate_non_negative("reasoning_usd_per_1m", row.reasoning_usd_per_1m)


def _to_config_price(row: PriceRow) -> Price:
    return Price(
        provider=row.provider,
        model=row.model,
        aliases=tuple(row.aliases),
        input_usd_per_1m=row.input_usd_per_1m,
        cached_input_usd_per_1m=row.cached_input_usd_per_1m,
        cache_write_usd_per_1m=row.cache_write_usd_per_1m,
        cached_output_usd_per_1m=row.cached_output_usd_per_1m,
        output_usd_per_1m=row.output_usd_per_1m,
        reasoning_usd_per_1m=row.reasoning_usd_per_1m,
        category=row.category,
        release_status=row.release_status,
        context_min_tokens=row.context_min_tokens,
        context_max_tokens=row.context_max_tokens,
        context_label=row.context_label,
        context_basis=row.context_basis,
    )


def _render_validated_prices_file(
    *,
    virtual_prices: tuple[Price, ...],
    actual_prices: tuple[Price, ...],
) -> str:
    rendered = render_prices_toml(
        virtual_prices=virtual_prices,
        actual_prices=actual_prices,
    )
    parsed = tomllib.loads(rendered)
    config_module.parse_pricing_config(parsed)
    return rendered


def list_prices(
    *,
    config_path: Path | None = None,
    prices_path: Path | None = None,
    prices_dir: Path | None = None,
    include_provider_prices: bool = True,
) -> tuple[PriceRow, ...]:
    resolved_config, resolved_prices, resolved_prices_dir, resolved_subscriptions = (
        _resolve_costing_paths(
            config_path=config_path,
            prices_path=prices_path,
            prices_dir=prices_dir,
        )
    )
    try:
        if include_provider_prices:
            loaded = config_module.load_resolved_costing_config(
                config_cli_value=resolved_config,
                prices_cli_value=resolved_prices,
                prices_dir_cli_value=resolved_prices_dir,
                subscriptions_cli_value=resolved_subscriptions,
            ).config
            source_kind = _price_row_source_kind(include_provider_prices)
            source_path = None
        else:
            manual = config_module.load_pricing_config(resolved_prices)
            loaded = config_module.CostingConfig(
                virtual_prices=manual.virtual_prices,
                actual_prices=manual.actual_prices,
            )
            source_kind = _price_row_source_kind(include_provider_prices)
            source_path = resolved_prices
    except ValueError as exc:
        msg = f"Invalid pricing config: {exc}"
        raise ConfigurationError(msg) from exc

    rows: list[PriceRow] = []
    for price in loaded.virtual_prices:
        rows.append(
            _to_price_row(
                table="virtual",
                source_kind=source_kind,
                source_path=source_path,
                price=price,
            )
        )
    for price in loaded.actual_prices:
        rows.append(
            _to_price_row(
                table="actual",
                source_kind=source_kind,
                source_path=source_path,
                price=price,
            )
        )
    return tuple(rows)


def list_unconfigured_models(
    db_path: Path | None = None,
    *,
    period: str = "today",
    config_path: Path | None = None,
) -> tuple[UnconfiguredModelRow, ...]:
    report = usage_report(
        resolve_toktrail_db_path(db_path),
        period=period,
        config_path=config_path,
    )
    return report.unconfigured_models


def upsert_manual_price(
    price: PriceRow,
    *,
    prices_path: Path | None = None,
    config_path: Path | None = None,
    create_missing: bool = True,
) -> Path:
    _validate_price_row(price)
    if price.table not in {"actual", "virtual"}:
        msg = f"Unsupported price table: {price.table!r}"
        raise InvalidAPIUsageError(msg)
    resolved_prices = (
        resolve_toktrail_prices_path(prices_path)
        if prices_path is not None
        else resolve_toktrail_config_path(config_path).with_name("prices.toml")
    )
    if not create_missing and not resolved_prices.exists():
        msg = f"Manual prices file does not exist: {resolved_prices}"
        raise InvalidAPIUsageError(msg)
    try:
        existing = config_module.load_pricing_config(resolved_prices)
    except ValueError as exc:
        msg = f"Invalid pricing config at {resolved_prices}: {exc}"
        raise ConfigurationError(msg) from exc

    key = _variant_key(
        provider=price.provider,
        model=price.model,
        context_min_tokens=price.context_min_tokens,
        context_max_tokens=price.context_max_tokens,
        context_basis=price.context_basis,
    )
    updated = _to_config_price(price)
    if price.table == "virtual":
        table_prices = list(existing.virtual_prices)
        other_prices = tuple(existing.actual_prices)
    else:
        table_prices = list(existing.actual_prices)
        other_prices = tuple(existing.virtual_prices)

    replaced = False
    for idx, current in enumerate(table_prices):
        current_key = _variant_key(
            provider=current.provider,
            model=current.model,
            context_min_tokens=current.context_min_tokens,
            context_max_tokens=current.context_max_tokens,
            context_basis=current.context_basis,
        )
        if current_key == key:
            table_prices[idx] = updated
            replaced = True
            break
    if not replaced:
        table_prices.append(updated)

    virtual_prices = tuple(table_prices) if price.table == "virtual" else other_prices
    actual_prices = tuple(table_prices) if price.table == "actual" else other_prices

    try:
        rendered = _render_validated_prices_file(
            virtual_prices=virtual_prices,
            actual_prices=actual_prices,
        )
    except ValueError as exc:
        msg = f"Updated pricing configuration is invalid: {exc}"
        raise ConfigurationError(msg) from exc

    atomic_write_text(resolved_prices, rendered, create_backup=True)
    try:
        config_module.load_pricing_config(resolved_prices)
    except ValueError as exc:
        msg = f"Could not reload pricing config from {resolved_prices}: {exc}"
        raise ConfigurationError(msg) from exc
    return resolved_prices


def delete_manual_price(
    *,
    table: Literal["actual", "virtual"],
    provider: str,
    model: str,
    context_min_tokens: int | None = None,
    context_max_tokens: int | None = None,
    context_basis: str = "prompt_like",
    prices_path: Path | None = None,
    config_path: Path | None = None,
) -> Path:
    resolved_prices = (
        resolve_toktrail_prices_path(prices_path)
        if prices_path is not None
        else resolve_toktrail_config_path(config_path).with_name("prices.toml")
    )
    try:
        existing = config_module.load_pricing_config(resolved_prices)
    except ValueError as exc:
        msg = f"Invalid pricing config at {resolved_prices}: {exc}"
        raise ConfigurationError(msg) from exc

    target_key = _variant_key(
        provider=provider,
        model=model,
        context_min_tokens=context_min_tokens,
        context_max_tokens=context_max_tokens,
        context_basis=context_basis,
    )
    if table == "virtual":
        table_prices = list(existing.virtual_prices)
        other_prices = tuple(existing.actual_prices)
    else:
        table_prices = list(existing.actual_prices)
        other_prices = tuple(existing.virtual_prices)

    filtered = []
    removed = False
    for current in table_prices:
        current_key = _variant_key(
            provider=current.provider,
            model=current.model,
            context_min_tokens=current.context_min_tokens,
            context_max_tokens=current.context_max_tokens,
            context_basis=current.context_basis,
        )
        if current_key == target_key:
            removed = True
            continue
        filtered.append(current)

    if not removed:
        msg = (
            "Manual price not found for "
            f"{provider}/{model} ("
            f"{table}, context={context_min_tokens}-{context_max_tokens}, "
            f"basis={context_basis})."
        )
        raise InvalidAPIUsageError(msg)

    virtual_prices = tuple(filtered) if table == "virtual" else other_prices
    actual_prices = tuple(filtered) if table == "actual" else other_prices
    try:
        rendered = _render_validated_prices_file(
            virtual_prices=virtual_prices,
            actual_prices=actual_prices,
        )
    except ValueError as exc:
        msg = f"Updated pricing configuration is invalid: {exc}"
        raise ConfigurationError(msg) from exc

    atomic_write_text(resolved_prices, rendered, create_backup=True)
    try:
        config_module.load_pricing_config(resolved_prices)
    except ValueError as exc:
        msg = f"Could not reload pricing config from {resolved_prices}: {exc}"
        raise ConfigurationError(msg) from exc
    return resolved_prices


__all__ = [
    "delete_manual_price",
    "list_prices",
    "list_unconfigured_models",
    "upsert_manual_price",
]
