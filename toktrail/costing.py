from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from toktrail.config import ActualCostMode, CostingConfig, Price, normalize_identity
from toktrail.models import TokenBreakdown
from toktrail.provider_identity import inferred_provider_from_model


@dataclass(frozen=True)
class CostBreakdown:
    source_cost_usd: Decimal
    actual_cost_usd: Decimal
    virtual_cost_usd: Decimal
    unpriced_count: int = 0

    @property
    def savings_usd(self) -> Decimal:
        return self.virtual_cost_usd - self.actual_cost_usd


@dataclass(frozen=True)
class PriceResolution:
    actual_mode: ActualCostMode
    actual_price: Price | None
    virtual_price: Price | None
    missing_actual_price: bool
    missing_virtual_price: bool

    @property
    def missing_kinds(self) -> tuple[str, ...]:
        kinds: list[str] = []
        if self.missing_actual_price:
            kinds.append("actual")
        if self.missing_virtual_price:
            kinds.append("virtual")
        return tuple(kinds)


@dataclass(frozen=True)
class UsageCostAtom:
    harness: str
    provider_id: str
    model_id: str
    thinking_level: str | None
    agent: str | None
    message_count: int
    tokens: TokenBreakdown
    source_cost_usd: Decimal

    def compute_costs(self, config: CostingConfig) -> CostBreakdown:
        return compute_costs(
            harness=self.harness,
            provider_id=self.provider_id,
            model_id=self.model_id,
            tokens=self.tokens,
            source_cost_usd=self.source_cost_usd,
            message_count=self.message_count,
            config=config,
        )


def normalize_price_key(value: str) -> str:
    return normalize_identity(value)


def cost_from_price(tokens: TokenBreakdown, price: Price) -> Decimal:
    cached_input_price = (
        price.cached_input_usd_per_1m
        if price.cached_input_usd_per_1m is not None
        else price.input_usd_per_1m
    )
    cache_write_price = (
        price.cache_write_usd_per_1m
        if price.cache_write_usd_per_1m is not None
        else price.input_usd_per_1m
    )
    cached_output_price = (
        price.cached_output_usd_per_1m
        if price.cached_output_usd_per_1m is not None
        else price.output_usd_per_1m
    )
    reasoning_price = (
        price.reasoning_usd_per_1m
        if price.reasoning_usd_per_1m is not None
        else price.output_usd_per_1m
    )
    million = Decimal(1_000_000)
    return (
        Decimal(tokens.input) * Decimal(str(price.input_usd_per_1m)) / million
        + Decimal(tokens.cache_read) * Decimal(str(cached_input_price)) / million
        + Decimal(tokens.cache_write) * Decimal(str(cache_write_price)) / million
        + Decimal(tokens.output) * Decimal(str(price.output_usd_per_1m)) / million
        + Decimal(tokens.cache_output) * Decimal(str(cached_output_price)) / million
        + Decimal(tokens.reasoning) * Decimal(str(reasoning_price)) / million
    )


def uncached_tokens(tokens: TokenBreakdown) -> TokenBreakdown:
    return TokenBreakdown(
        input=tokens.input + tokens.cache_read + tokens.cache_write,
        output=tokens.output + tokens.cache_output,
        reasoning=tokens.reasoning,
        cache_read=0,
        cache_write=0,
        cache_output=0,
    )


def resolve_price(
    provider_id: str,
    model_id: str,
    prices: Sequence[Price],
) -> Price | None:
    normalized_model = normalize_price_key(model_id)
    provider_candidates = _provider_candidates(provider_id, model_id)

    for normalized_provider in provider_candidates:
        for price in prices:
            if normalize_price_key(price.provider) != normalized_provider:
                continue
            if normalize_price_key(price.model) == normalized_model:
                return price
        for price in prices:
            if normalize_price_key(price.provider) != normalized_provider:
                continue
            if normalized_model in {
                normalize_price_key(alias) for alias in price.aliases
            }:
                return price
    return None


def resolve_actual_mode(
    harness: str,
    provider_id: str,
    model_id: str,
    config: CostingConfig,
) -> ActualCostMode:
    return config.resolve_actual_cost_mode(
        harness=harness,
        provider=provider_id,
        model=model_id,
    )


def resolve_price_resolution(
    *,
    harness: str,
    provider_id: str,
    model_id: str,
    config: CostingConfig,
) -> PriceResolution:
    actual_mode = resolve_actual_mode(harness, provider_id, model_id, config)
    actual_price = None
    missing_actual_price = False
    if actual_mode == "pricing":
        actual_price = resolve_price(provider_id, model_id, config.actual_prices)
        missing_actual_price = actual_price is None

    virtual_price = None
    missing_virtual_price = False
    if config.default_virtual_mode == "pricing":
        virtual_price = resolve_price(provider_id, model_id, config.virtual_prices)
        missing_virtual_price = virtual_price is None

    return PriceResolution(
        actual_mode=actual_mode,
        actual_price=actual_price,
        virtual_price=virtual_price,
        missing_actual_price=missing_actual_price,
        missing_virtual_price=missing_virtual_price,
    )


def compute_costs(
    *,
    harness: str,
    provider_id: str,
    model_id: str,
    tokens: TokenBreakdown,
    source_cost_usd: Decimal,
    message_count: int,
    config: CostingConfig,
) -> CostBreakdown:
    resolution = resolve_price_resolution(
        harness=harness,
        provider_id=provider_id,
        model_id=model_id,
        config=config,
    )
    actual_cost_usd = source_cost_usd
    virtual_cost_usd = Decimal(0)

    if resolution.actual_mode == "zero":
        actual_cost_usd = Decimal(0)
    elif resolution.actual_mode == "pricing":
        if resolution.actual_price is None:
            actual_cost_usd = Decimal(0)
        else:
            actual_cost_usd = cost_from_price(tokens, resolution.actual_price)

    if resolution.virtual_price is not None:
        virtual_cost_usd = cost_from_price(tokens, resolution.virtual_price)

    return CostBreakdown(
        source_cost_usd=source_cost_usd,
        actual_cost_usd=actual_cost_usd,
        virtual_cost_usd=virtual_cost_usd,
        unpriced_count=1 if resolution.missing_kinds and message_count > 0 else 0,
    )


def _provider_candidates(provider_id: str, model_id: str) -> tuple[str, ...]:
    normalized_provider = provider_id.strip().lower()
    if normalized_provider and normalized_provider != "unknown":
        return (normalize_price_key(provider_id),)

    candidates: list[str] = []
    inferred_provider = inferred_provider_from_model(model_id)
    if inferred_provider is not None:
        normalized_inferred = normalize_price_key(inferred_provider)
        if normalized_inferred not in candidates:
            candidates.append(normalized_inferred)
    return tuple(candidates)


@dataclass(frozen=True)
class SimulationTarget:
    provider: str
    model: str


@dataclass(frozen=True)
class SimulationResult:
    target_provider: str
    target_model: str
    tokens: TokenBreakdown
    cost_usd: Decimal
    baseline_actual_usd: Decimal
    baseline_virtual_usd: Decimal
    delta_vs_actual_usd: Decimal
    delta_vs_virtual_usd: Decimal
    missing_price_kinds: tuple[str, ...]


def simulate_cost(
    *,
    tokens: TokenBreakdown,
    target: SimulationTarget,
    config: CostingConfig,
    baseline_actual_usd: Decimal,
    baseline_virtual_usd: Decimal,
) -> SimulationResult:
    price = resolve_price(target.provider, target.model, config.virtual_prices)
    missing_kinds: list[str] = []
    if price is None:
        price = resolve_price(target.provider, target.model, config.actual_prices)
    if price is None:
        missing_kinds.append("target")
        cost = Decimal(0)
    else:
        cost = cost_from_price(tokens, price)
    return SimulationResult(
        target_provider=target.provider,
        target_model=target.model,
        tokens=tokens,
        cost_usd=cost,
        baseline_actual_usd=baseline_actual_usd,
        baseline_virtual_usd=baseline_virtual_usd,
        delta_vs_actual_usd=cost - baseline_actual_usd,
        delta_vs_virtual_usd=cost - baseline_virtual_usd,
        missing_price_kinds=tuple(missing_kinds),
    )
