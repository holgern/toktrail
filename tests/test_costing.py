from __future__ import annotations

from typing import cast

import pytest

from toktrail.config import ActualCostRule, CostingConfig, MissingPriceMode, Price
from toktrail.costing import (
    compute_costs,
    cost_from_price,
    resolve_actual_mode,
    resolve_price,
)
from toktrail.models import TokenBreakdown


def make_price(
    *,
    provider: str = "openai",
    model: str = "gpt-5-mini",
    aliases: tuple[str, ...] = (),
    input_usd_per_1m: float = 1.0,
    cached_input_usd_per_1m: float | None = None,
    cache_write_usd_per_1m: float | None = None,
    output_usd_per_1m: float = 2.0,
    reasoning_usd_per_1m: float | None = None,
) -> Price:
    return Price(
        provider=provider,
        model=model,
        aliases=aliases,
        input_usd_per_1m=input_usd_per_1m,
        cached_input_usd_per_1m=cached_input_usd_per_1m,
        cache_write_usd_per_1m=cache_write_usd_per_1m,
        output_usd_per_1m=output_usd_per_1m,
        reasoning_usd_per_1m=reasoning_usd_per_1m,
    )


def test_cost_from_price_uses_all_token_categories() -> None:
    price = make_price(
        input_usd_per_1m=1.0,
        cached_input_usd_per_1m=0.1,
        cache_write_usd_per_1m=1.5,
        output_usd_per_1m=4.0,
        reasoning_usd_per_1m=5.0,
    )
    tokens = TokenBreakdown(
        input=100,
        output=50,
        reasoning=10,
        cache_read=20,
        cache_write=5,
    )

    assert cost_from_price(tokens, price) == pytest.approx(0.0003595)


def test_cost_from_price_falls_back_to_output_for_reasoning() -> None:
    price = make_price(output_usd_per_1m=6.0, reasoning_usd_per_1m=None)
    tokens = TokenBreakdown(reasoning=100)

    assert cost_from_price(tokens, price) == pytest.approx(0.0006)


def test_cost_from_price_falls_back_to_input_for_cache_write() -> None:
    price = make_price(input_usd_per_1m=3.0, cache_write_usd_per_1m=None)
    tokens = TokenBreakdown(cache_write=100)

    assert cost_from_price(tokens, price) == pytest.approx(0.0003)


def test_resolve_price_prefers_exact_model_match() -> None:
    exact = make_price(model="gpt-5-mini")
    alias_only = make_price(model="gpt-5.4", aliases=("gpt-5-mini",))

    assert resolve_price("openai", "gpt-5-mini", [alias_only, exact]) == exact


def test_resolve_price_matches_alias_and_inferred_provider() -> None:
    price = make_price(
        provider="anthropic",
        model="claude-sonnet-4",
        aliases=("Claude Sonnet 4",),
    )

    assert resolve_price("github-copilot", "Claude Sonnet 4", [price]) == price


@pytest.mark.parametrize("missing_price_mode", ["warn", "zero"])
def test_compute_costs_missing_price_returns_zero_and_tracks_unpriced(
    missing_price_mode: str,
) -> None:
    config = CostingConfig(
        default_actual_mode="zero",
        default_virtual_mode="pricing",
        missing_price=cast(MissingPriceMode, missing_price_mode),
    )

    breakdown = compute_costs(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        tokens=TokenBreakdown(input=100),
        source_cost_usd=0.0,
        message_count=1,
        config=config,
    )

    assert breakdown.actual_cost_usd == 0.0
    assert breakdown.virtual_cost_usd == 0.0
    assert breakdown.unpriced_count == 1


def test_compute_costs_uses_source_actual_mode() -> None:
    virtual_price = make_price(input_usd_per_1m=1.0)
    config = CostingConfig(
        default_actual_mode="source",
        default_virtual_mode="pricing",
        virtual_prices=(virtual_price,),
    )

    breakdown = compute_costs(
        harness="opencode",
        provider_id="openai",
        model_id="gpt-5-mini",
        tokens=TokenBreakdown(input=100),
        source_cost_usd=2.5,
        message_count=1,
        config=config,
    )

    assert breakdown.source_cost_usd == 2.5
    assert breakdown.actual_cost_usd == 2.5
    assert breakdown.virtual_cost_usd == pytest.approx(0.0001)


def test_compute_costs_uses_zero_actual_mode() -> None:
    virtual_price = make_price(input_usd_per_1m=1.0)
    config = CostingConfig(
        default_actual_mode="zero",
        default_virtual_mode="pricing",
        virtual_prices=(virtual_price,),
    )

    breakdown = compute_costs(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        tokens=TokenBreakdown(input=100),
        source_cost_usd=4.0,
        message_count=1,
        config=config,
    )

    assert breakdown.actual_cost_usd == 0.0


def test_compute_costs_uses_pricing_actual_mode() -> None:
    actual_price = make_price(input_usd_per_1m=2.0)
    breakdown = compute_costs(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        tokens=TokenBreakdown(input=100),
        source_cost_usd=4.0,
        message_count=1,
        config=CostingConfig(
            default_actual_mode="pricing",
            default_virtual_mode="zero",
            actual_prices=(actual_price,),
        ),
    )

    assert breakdown.actual_cost_usd == pytest.approx(0.0002)


def test_resolve_actual_mode_uses_rule_specificity() -> None:
    config = CostingConfig(
        default_actual_mode="source",
        actual_rules=(
            ActualCostRule(
                harness="copilot",
                provider=None,
                model=None,
                mode="zero",
            ),
            ActualCostRule(
                harness="copilot",
                provider="openai",
                model=None,
                mode="pricing",
            ),
            ActualCostRule(
                harness="copilot",
                provider="openai",
                model="gpt-5-mini",
                mode="source",
            ),
        ),
    )

    assert resolve_actual_mode("copilot", "openai", "gpt-5-mini", config) == "source"
    assert resolve_actual_mode("copilot", "openai", "gpt-5.4", config) == "pricing"
    assert (
        resolve_actual_mode("copilot", "anthropic", "claude-sonnet-4", config) == "zero"
    )


def test_compute_costs_exposes_savings() -> None:
    actual_price = make_price(input_usd_per_1m=1.0)
    virtual_price = make_price(input_usd_per_1m=3.0)
    config = CostingConfig(
        default_actual_mode="pricing",
        default_virtual_mode="pricing",
        actual_prices=(actual_price,),
        virtual_prices=(virtual_price,),
    )

    breakdown = compute_costs(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        tokens=TokenBreakdown(input=100),
        source_cost_usd=0.0,
        message_count=1,
        config=config,
    )

    assert breakdown.actual_cost_usd == pytest.approx(0.0001)
    assert breakdown.virtual_cost_usd == pytest.approx(0.0003)
    assert breakdown.savings_usd == pytest.approx(0.0002)
