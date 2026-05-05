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
    selected_context_tokens: int | None = None
    actual_context_label: str | None = None
    virtual_context_label: str | None = None

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


@dataclass(frozen=True)
class CompiledPrice:
    price: Price
    provider_key: str
    model_key: str
    alias_keys: tuple[str, ...]
    input_per_token: Decimal
    cached_input_per_token: Decimal
    cache_write_per_token: Decimal
    cached_output_per_token: Decimal
    output_per_token: Decimal
    reasoning_per_token: Decimal
    context_min_tokens: int | None
    context_max_tokens: int | None


@dataclass
class CostingRuntime:
    config: CostingConfig
    actual_by_provider_model: dict[tuple[str, str], list[CompiledPrice]]
    actual_by_alias: dict[tuple[str, str], list[CompiledPrice]]
    virtual_by_provider_model: dict[tuple[str, str], list[CompiledPrice]]
    virtual_by_alias: dict[tuple[str, str], list[CompiledPrice]]
    actual_mode_cache: dict[tuple[str, str, str], ActualCostMode]

    def resolve_actual_mode(
        self,
        *,
        harness: str,
        provider_id: str,
        model_id: str,
    ) -> ActualCostMode:
        key = (
            normalize_price_key(harness),
            normalize_price_key(provider_id),
            normalize_price_key(model_id),
        )
        cached = self.actual_mode_cache.get(key)
        if cached is not None:
            return cached
        mode = self.config.resolve_actual_cost_mode(
            harness=harness,
            provider=provider_id,
            model=model_id,
        )
        self.actual_mode_cache[key] = mode
        return mode

    def resolve_price(
        self,
        provider_id: str,
        model_id: str,
        *,
        context_tokens: int | None = None,
        use_actual_prices: bool,
    ) -> CompiledPrice | None:
        normalized_model = normalize_price_key(model_id)
        provider_candidates = _provider_candidates(provider_id, model_id)
        by_provider_model = (
            self.actual_by_provider_model
            if use_actual_prices
            else self.virtual_by_provider_model
        )
        by_alias = self.actual_by_alias if use_actual_prices else self.virtual_by_alias

        for normalized_provider in provider_candidates:
            exact_matches = by_provider_model.get(
                (normalized_provider, normalized_model),
                (),
            )
            alias_matches = by_alias.get((normalized_provider, normalized_model), ())
            for candidate_group in (exact_matches, alias_matches):
                if not candidate_group:
                    continue
                selected = _select_compiled_price_for_context(
                    candidate_group,
                    context_tokens=context_tokens,
                )
                if selected is not None:
                    return selected
        return None

    def resolve_price_resolution(
        self,
        *,
        harness: str,
        provider_id: str,
        model_id: str,
        tokens: TokenBreakdown | None = None,
        context_tokens: int | None = None,
    ) -> PriceResolution:
        effective_context_tokens = (
            context_tokens
            if context_tokens is not None
            else context_tokens_for_price(tokens)
            if tokens is not None
            else None
        )
        actual_mode = self.resolve_actual_mode(
            harness=harness,
            provider_id=provider_id,
            model_id=model_id,
        )
        actual_price: Price | None = None
        missing_actual_price = False
        if actual_mode == "pricing":
            compiled_actual = self.resolve_price(
                provider_id,
                model_id,
                context_tokens=effective_context_tokens,
                use_actual_prices=True,
            )
            actual_price = (
                compiled_actual.price if compiled_actual is not None else None
            )
            missing_actual_price = actual_price is None

        virtual_price: Price | None = None
        missing_virtual_price = False
        if self.config.default_virtual_mode == "pricing":
            compiled_virtual = self.resolve_price(
                provider_id,
                model_id,
                context_tokens=effective_context_tokens,
                use_actual_prices=False,
            )
            virtual_price = (
                compiled_virtual.price if compiled_virtual is not None else None
            )
            missing_virtual_price = virtual_price is None

        return PriceResolution(
            actual_mode=actual_mode,
            actual_price=actual_price,
            virtual_price=virtual_price,
            missing_actual_price=missing_actual_price,
            missing_virtual_price=missing_virtual_price,
            selected_context_tokens=effective_context_tokens,
            actual_context_label=(
                actual_price.context_label if actual_price is not None else None
            ),
            virtual_context_label=(
                virtual_price.context_label if virtual_price is not None else None
            ),
        )

    def compute_costs(
        self,
        *,
        harness: str,
        provider_id: str,
        model_id: str,
        tokens: TokenBreakdown,
        source_cost_usd: Decimal,
        message_count: int,
    ) -> CostBreakdown:
        resolution = self.resolve_price_resolution(
            harness=harness,
            provider_id=provider_id,
            model_id=model_id,
            tokens=tokens,
        )
        actual_cost_usd = source_cost_usd
        virtual_cost_usd = Decimal(0)
        context_tokens = context_tokens_for_price(tokens)

        if resolution.actual_mode == "zero":
            actual_cost_usd = Decimal(0)
        elif resolution.actual_mode == "pricing":
            compiled_actual = self.resolve_price(
                provider_id,
                model_id,
                context_tokens=context_tokens,
                use_actual_prices=True,
            )
            if compiled_actual is None:
                actual_cost_usd = Decimal(0)
            else:
                actual_cost_usd = cost_from_compiled_price(tokens, compiled_actual)

        compiled_virtual = self.resolve_price(
            provider_id,
            model_id,
            context_tokens=context_tokens,
            use_actual_prices=False,
        )
        if compiled_virtual is not None:
            virtual_cost_usd = cost_from_compiled_price(tokens, compiled_virtual)

        return CostBreakdown(
            source_cost_usd=source_cost_usd,
            actual_cost_usd=actual_cost_usd,
            virtual_cost_usd=virtual_cost_usd,
            unpriced_count=1 if resolution.missing_kinds and message_count > 0 else 0,
        )


def compile_costing_config(config: CostingConfig) -> CostingRuntime:
    actual_by_provider_model: dict[tuple[str, str], list[CompiledPrice]] = {}
    actual_by_alias: dict[tuple[str, str], list[CompiledPrice]] = {}
    virtual_by_provider_model: dict[tuple[str, str], list[CompiledPrice]] = {}
    virtual_by_alias: dict[tuple[str, str], list[CompiledPrice]] = {}

    for compiled in (_compile_price(price) for price in config.actual_prices):
        actual_by_provider_model.setdefault(
            (compiled.provider_key, compiled.model_key),
            [],
        ).append(compiled)
        for alias_key in compiled.alias_keys:
            actual_by_alias.setdefault((compiled.provider_key, alias_key), []).append(
                compiled
            )

    for compiled in (_compile_price(price) for price in config.virtual_prices):
        virtual_by_provider_model.setdefault(
            (compiled.provider_key, compiled.model_key),
            [],
        ).append(compiled)
        for alias_key in compiled.alias_keys:
            virtual_by_alias.setdefault((compiled.provider_key, alias_key), []).append(
                compiled
            )

    return CostingRuntime(
        config=config,
        actual_by_provider_model=actual_by_provider_model,
        actual_by_alias=actual_by_alias,
        virtual_by_provider_model=virtual_by_provider_model,
        virtual_by_alias=virtual_by_alias,
        actual_mode_cache={},
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


def cost_from_compiled_price(tokens: TokenBreakdown, price: CompiledPrice) -> Decimal:
    return (
        Decimal(tokens.input) * price.input_per_token
        + Decimal(tokens.cache_read) * price.cached_input_per_token
        + Decimal(tokens.cache_write) * price.cache_write_per_token
        + Decimal(tokens.output) * price.output_per_token
        + Decimal(tokens.cache_output) * price.cached_output_per_token
        + Decimal(tokens.reasoning) * price.reasoning_per_token
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


def context_tokens_for_price(tokens: TokenBreakdown) -> int:
    return tokens.input + tokens.cache_read + tokens.cache_write


def price_matches_context(price: Price, context_tokens: int | None) -> bool:
    if price.context_min_tokens is None and price.context_max_tokens is None:
        return True
    if context_tokens is None:
        return False
    minimum = price.context_min_tokens if price.context_min_tokens is not None else 0
    maximum = price.context_max_tokens
    if context_tokens < minimum:
        return False
    if maximum is not None and context_tokens > maximum:
        return False
    return True


def resolve_price(
    provider_id: str,
    model_id: str,
    prices: Sequence[Price],
    *,
    context_tokens: int | None = None,
) -> Price | None:
    normalized_model = normalize_price_key(model_id)
    provider_candidates = _provider_candidates(provider_id, model_id)

    for normalized_provider in provider_candidates:
        exact_matches: list[Price] = []
        alias_matches: list[Price] = []
        for price in prices:
            if normalize_price_key(price.provider) != normalized_provider:
                continue
            if normalize_price_key(price.model) == normalized_model:
                exact_matches.append(price)
        for price in prices:
            if normalize_price_key(price.provider) != normalized_provider:
                continue
            if normalized_model in {
                normalize_price_key(alias) for alias in price.aliases
            }:
                alias_matches.append(price)
        for candidate_group in (exact_matches, alias_matches):
            if not candidate_group:
                continue
            selected = _select_price_for_context(
                candidate_group,
                context_tokens=context_tokens,
            )
            if selected is not None:
                return selected
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
    tokens: TokenBreakdown | None = None,
    context_tokens: int | None = None,
    runtime: CostingRuntime | None = None,
) -> PriceResolution:
    if runtime is not None:
        return runtime.resolve_price_resolution(
            harness=harness,
            provider_id=provider_id,
            model_id=model_id,
            tokens=tokens,
            context_tokens=context_tokens,
        )
    effective_context_tokens = (
        context_tokens
        if context_tokens is not None
        else context_tokens_for_price(tokens)
        if tokens is not None
        else None
    )
    actual_mode = resolve_actual_mode(harness, provider_id, model_id, config)
    actual_price = None
    missing_actual_price = False
    if actual_mode == "pricing":
        actual_price = resolve_price(
            provider_id,
            model_id,
            config.actual_prices,
            context_tokens=effective_context_tokens,
        )
        missing_actual_price = actual_price is None

    virtual_price = None
    missing_virtual_price = False
    if config.default_virtual_mode == "pricing":
        virtual_price = resolve_price(
            provider_id,
            model_id,
            config.virtual_prices,
            context_tokens=effective_context_tokens,
        )
        missing_virtual_price = virtual_price is None

    return PriceResolution(
        actual_mode=actual_mode,
        actual_price=actual_price,
        virtual_price=virtual_price,
        missing_actual_price=missing_actual_price,
        missing_virtual_price=missing_virtual_price,
        selected_context_tokens=effective_context_tokens,
        actual_context_label=(
            actual_price.context_label if actual_price is not None else None
        ),
        virtual_context_label=(
            virtual_price.context_label if virtual_price is not None else None
        ),
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
    runtime: CostingRuntime | None = None,
) -> CostBreakdown:
    if runtime is not None:
        return runtime.compute_costs(
            harness=harness,
            provider_id=provider_id,
            model_id=model_id,
            tokens=tokens,
            source_cost_usd=source_cost_usd,
            message_count=message_count,
        )
    resolution = resolve_price_resolution(
        harness=harness,
        provider_id=provider_id,
        model_id=model_id,
        config=config,
        tokens=tokens,
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
    price = resolve_price(
        target.provider,
        target.model,
        config.virtual_prices,
        context_tokens=context_tokens_for_price(tokens),
    )
    missing_kinds: list[str] = []
    if price is None:
        price = resolve_price(
            target.provider,
            target.model,
            config.actual_prices,
            context_tokens=context_tokens_for_price(tokens),
        )
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


def _select_price_for_context(
    candidates: Sequence[Price],
    *,
    context_tokens: int | None,
) -> Price | None:
    tiered = [
        price
        for price in candidates
        if price.context_min_tokens is not None or price.context_max_tokens is not None
    ]
    untiered = [
        price
        for price in candidates
        if price.context_min_tokens is None and price.context_max_tokens is None
    ]
    tiered = sorted(
        tiered,
        key=lambda price: (
            price.context_min_tokens if price.context_min_tokens is not None else 0,
            price.context_max_tokens
            if price.context_max_tokens is not None
            else 2**63 - 1,
        ),
    )
    if context_tokens is not None:
        matching = [
            price for price in tiered if price_matches_context(price, context_tokens)
        ]
        if matching:
            return min(matching, key=_context_range_width)
        if untiered:
            return untiered[0]
        return None

    if untiered:
        return untiered[0]
    if tiered:
        return tiered[0]
    return None


def _compile_price(price: Price) -> CompiledPrice:
    input_per_token = Decimal(str(price.input_usd_per_1m)) / Decimal(1_000_000)
    cached_input_usd_per_1m = (
        price.cached_input_usd_per_1m
        if price.cached_input_usd_per_1m is not None
        else price.input_usd_per_1m
    )
    cache_write_usd_per_1m = (
        price.cache_write_usd_per_1m
        if price.cache_write_usd_per_1m is not None
        else price.input_usd_per_1m
    )
    cached_output_usd_per_1m = (
        price.cached_output_usd_per_1m
        if price.cached_output_usd_per_1m is not None
        else price.output_usd_per_1m
    )
    reasoning_usd_per_1m = (
        price.reasoning_usd_per_1m
        if price.reasoning_usd_per_1m is not None
        else price.output_usd_per_1m
    )
    return CompiledPrice(
        price=price,
        provider_key=normalize_price_key(price.provider),
        model_key=normalize_price_key(price.model),
        alias_keys=tuple(normalize_price_key(alias) for alias in price.aliases),
        input_per_token=input_per_token,
        cached_input_per_token=Decimal(str(cached_input_usd_per_1m))
        / Decimal(1_000_000),
        cache_write_per_token=Decimal(str(cache_write_usd_per_1m)) / Decimal(1_000_000),
        cached_output_per_token=Decimal(str(cached_output_usd_per_1m))
        / Decimal(1_000_000),
        output_per_token=Decimal(str(price.output_usd_per_1m)) / Decimal(1_000_000),
        reasoning_per_token=Decimal(str(reasoning_usd_per_1m)) / Decimal(1_000_000),
        context_min_tokens=price.context_min_tokens,
        context_max_tokens=price.context_max_tokens,
    )


def _select_compiled_price_for_context(
    candidates: Sequence[CompiledPrice],
    *,
    context_tokens: int | None,
) -> CompiledPrice | None:
    tiered = [
        price
        for price in candidates
        if price.context_min_tokens is not None or price.context_max_tokens is not None
    ]
    untiered = [
        price
        for price in candidates
        if price.context_min_tokens is None and price.context_max_tokens is None
    ]
    tiered = sorted(
        tiered,
        key=lambda price: (
            price.context_min_tokens if price.context_min_tokens is not None else 0,
            price.context_max_tokens
            if price.context_max_tokens is not None
            else 2**63 - 1,
        ),
    )
    if context_tokens is not None:
        matching = [
            price
            for price in tiered
            if _compiled_price_matches_context(price, context_tokens)
        ]
        if matching:
            return min(matching, key=_compiled_context_range_width)
        if untiered:
            return untiered[0]
        return None

    if untiered:
        return untiered[0]
    if tiered:
        return tiered[0]
    return None


def _compiled_price_matches_context(price: CompiledPrice, context_tokens: int) -> bool:
    minimum = price.context_min_tokens if price.context_min_tokens is not None else 0
    maximum = price.context_max_tokens
    if context_tokens < minimum:
        return False
    if maximum is not None and context_tokens > maximum:
        return False
    return True


def _compiled_context_range_width(price: CompiledPrice) -> int:
    minimum = price.context_min_tokens if price.context_min_tokens is not None else 0
    maximum = (
        price.context_max_tokens if price.context_max_tokens is not None else 2**63 - 1
    )
    return maximum - minimum


def _context_range_width(price: Price) -> int:
    minimum = price.context_min_tokens if price.context_min_tokens is not None else 0
    maximum = (
        price.context_max_tokens if price.context_max_tokens is not None else 2**63 - 1
    )
    return maximum - minimum
