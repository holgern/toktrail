from __future__ import annotations

from typing import cast

import pytest

from toktrail.config import ActualCostRule, CostingConfig, MissingPriceMode, Price
from toktrail.costing import (
    compute_costs,
    cost_from_price,
    resolve_actual_mode,
    resolve_price,
    resolve_price_resolution,
    uncached_tokens,
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
    cached_output_usd_per_1m: float | None = None,
    output_usd_per_1m: float = 2.0,
    reasoning_usd_per_1m: float | None = None,
    context_min_tokens: int | None = None,
    context_max_tokens: int | None = None,
    context_label: str | None = None,
) -> Price:
    return Price(
        provider=provider,
        model=model,
        aliases=aliases,
        input_usd_per_1m=input_usd_per_1m,
        cached_input_usd_per_1m=cached_input_usd_per_1m,
        cache_write_usd_per_1m=cache_write_usd_per_1m,
        cached_output_usd_per_1m=cached_output_usd_per_1m,
        output_usd_per_1m=output_usd_per_1m,
        reasoning_usd_per_1m=reasoning_usd_per_1m,
        context_min_tokens=context_min_tokens,
        context_max_tokens=context_max_tokens,
        context_label=context_label,
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

    assert float(cost_from_price(tokens, price)) == pytest.approx(0.0003595)


def test_cost_from_price_falls_back_to_output_for_reasoning() -> None:
    price = make_price(output_usd_per_1m=6.0, reasoning_usd_per_1m=None)
    tokens = TokenBreakdown(reasoning=100)

    assert float(cost_from_price(tokens, price)) == pytest.approx(0.0006)


def test_cost_from_price_falls_back_to_input_for_cache_write() -> None:
    price = make_price(input_usd_per_1m=3.0, cache_write_usd_per_1m=None)
    tokens = TokenBreakdown(cache_write=100)

    assert float(cost_from_price(tokens, price)) == pytest.approx(0.0003)


def test_uncached_tokens_moves_cache_read_to_input() -> None:
    converted = uncached_tokens(
        TokenBreakdown(
            input=100,
            output=20,
            reasoning=3,
            cache_read=40,
            cache_write=10,
            cache_output=5,
        )
    )

    assert converted.input == 150
    assert converted.output == 25
    assert converted.reasoning == 3
    assert converted.cache_read == 0
    assert converted.cache_write == 0
    assert converted.cache_output == 0


def test_uncached_scenario_shows_cache_savings() -> None:
    price = make_price(
        input_usd_per_1m=1.0,
        cached_input_usd_per_1m=0.2,
        output_usd_per_1m=3.0,
    )
    tokens = TokenBreakdown(input=20, cache_read=80)

    cached_cost = cost_from_price(tokens, price)
    uncached_cost = cost_from_price(uncached_tokens(tokens), price)

    assert uncached_cost > cached_cost


def test_cached_output_scenario_moves_cache_output_to_output() -> None:
    price = make_price(output_usd_per_1m=5.0, cached_output_usd_per_1m=1.0)
    tokens = TokenBreakdown(output=10, cache_output=40)

    uncached = uncached_tokens(tokens)

    assert uncached.output == 50
    assert uncached.cache_output == 0
    assert cost_from_price(uncached, price) > cost_from_price(tokens, price)


def test_resolve_price_prefers_exact_model_match() -> None:
    exact = make_price(model="gpt-5-mini")
    alias_only = make_price(model="gpt-5.4", aliases=("gpt-5-mini",))

    assert resolve_price("openai", "gpt-5-mini", [alias_only, exact]) == exact


def test_resolve_price_matches_alias_for_unknown_provider() -> None:
    price = make_price(
        provider="anthropic",
        model="claude-sonnet-4",
        aliases=("Claude Sonnet 4",),
    )

    assert resolve_price("unknown", "Claude Sonnet 4", [price]) == price


def test_resolve_price_keeps_explicit_provider_strict() -> None:
    price = make_price(provider="openai", model="gpt-5.4")

    assert resolve_price("github-copilot", "gpt-5.4", [price]) is None


def test_resolve_price_keeps_explicit_provider_identity_distinct() -> None:
    price = make_price(provider="openai", model="gpt-5.4")

    assert resolve_price("openai-codex", "gpt-5.4", [price]) is None


def test_resolve_price_selects_context_tier_from_prompt_like_context() -> None:
    short = make_price(
        model="gpt-5.4",
        input_usd_per_1m=2.5,
        output_usd_per_1m=15.0,
        context_min_tokens=0,
        context_max_tokens=272_000,
        context_label="<= 272K",
    )
    long = make_price(
        model="gpt-5.4",
        input_usd_per_1m=5.0,
        output_usd_per_1m=22.5,
        context_min_tokens=272_001,
        context_label="> 272K",
    )

    assert (
        resolve_price(
            "openai",
            "gpt-5.4",
            [short, long],
            context_tokens=100_000,
        )
        == short
    )
    assert (
        resolve_price(
            "openai",
            "gpt-5.4",
            [short, long],
            context_tokens=272_000,
        )
        == short
    )
    assert (
        resolve_price(
            "openai",
            "gpt-5.4",
            [short, long],
            context_tokens=272_001,
        )
        == long
    )


def test_compute_costs_selects_context_tier_from_prompt_like_tokens() -> None:
    short = make_price(
        model="gpt-5.4",
        input_usd_per_1m=2.5,
        cached_input_usd_per_1m=0.25,
        output_usd_per_1m=15.0,
        context_min_tokens=0,
        context_max_tokens=272_000,
        context_label="<= 272K",
    )
    long = make_price(
        model="gpt-5.4",
        input_usd_per_1m=5.0,
        cached_input_usd_per_1m=0.5,
        output_usd_per_1m=22.5,
        context_min_tokens=272_001,
        context_label="> 272K",
    )
    config = CostingConfig(virtual_prices=(short, long))

    below = compute_costs(
        harness="codex",
        provider_id="openai",
        model_id="gpt-5.4",
        tokens=TokenBreakdown(input=100_000, output=1_000),
        source_cost_usd=0.0,
        message_count=1,
        config=config,
    )
    above = compute_costs(
        harness="codex",
        provider_id="openai",
        model_id="gpt-5.4",
        tokens=TokenBreakdown(input=1_000, cache_read=272_000, output=1_000),
        source_cost_usd=0.0,
        message_count=1,
        config=config,
    )

    assert float(below.virtual_cost_usd) == pytest.approx(0.265)
    assert float(above.virtual_cost_usd) == pytest.approx(0.1635)


def test_resolve_price_uses_untiered_fallback_when_context_missing_match() -> None:
    untiered = make_price(model="gpt-5.4", input_usd_per_1m=3.0, output_usd_per_1m=18.0)
    long = make_price(
        model="gpt-5.4",
        input_usd_per_1m=5.0,
        output_usd_per_1m=22.5,
        context_min_tokens=272_001,
    )

    selected = resolve_price(
        "openai",
        "gpt-5.4",
        [long, untiered],
        context_tokens=1_000,
    )
    assert selected == untiered


def test_resolve_price_without_context_uses_lowest_tier_when_only_tiered() -> None:
    short = make_price(
        model="gpt-5.4",
        context_min_tokens=0,
        context_max_tokens=272_000,
    )
    long = make_price(
        model="gpt-5.4",
        input_usd_per_1m=6.0,
        output_usd_per_1m=30.0,
        context_min_tokens=272_001,
    )

    assert resolve_price("openai", "gpt-5.4", [long, short]) == short


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
    assert float(breakdown.virtual_cost_usd) == pytest.approx(0.0001)


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

    assert float(breakdown.actual_cost_usd) == pytest.approx(0.0002)


def test_resolve_price_resolution_reports_missing_virtual_price() -> None:
    resolution = resolve_price_resolution(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        config=CostingConfig(
            default_actual_mode="zero",
            default_virtual_mode="pricing",
        ),
    )

    assert resolution.actual_mode == "zero"
    assert resolution.actual_price is None
    assert resolution.virtual_price is None
    assert resolution.missing_actual_price is False
    assert resolution.missing_virtual_price is True
    assert resolution.missing_kinds == ("virtual",)


def test_price_resolution_missing_actual_only_when_actual_mode_pricing() -> None:
    pricing_resolution = resolve_price_resolution(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        config=CostingConfig(
            default_actual_mode="pricing",
            default_virtual_mode="zero",
        ),
    )
    source_resolution = resolve_price_resolution(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        config=CostingConfig(
            default_actual_mode="source",
            default_virtual_mode="zero",
        ),
    )

    assert pricing_resolution.missing_actual_price is True
    assert pricing_resolution.missing_kinds == ("actual",)
    assert source_resolution.missing_actual_price is False
    assert source_resolution.missing_kinds == ()


def test_price_resolution_keeps_explicit_provider_strict() -> None:
    price = make_price(provider="openai", model="gpt-5.4")
    resolution = resolve_price_resolution(
        harness="copilot",
        provider_id="github-copilot",
        model_id="gpt-5.4",
        config=CostingConfig(
            default_actual_mode="pricing",
            default_virtual_mode="pricing",
            actual_prices=(price,),
            virtual_prices=(price,),
        ),
    )

    assert resolution.actual_price is None
    assert resolution.virtual_price is None
    assert resolution.missing_kinds == ("actual", "virtual")


def test_compute_costs_keeps_existing_unpriced_count_behavior() -> None:
    config = CostingConfig(
        default_actual_mode="pricing",
        default_virtual_mode="pricing",
    )

    missing_with_messages = compute_costs(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        tokens=TokenBreakdown(input=100),
        source_cost_usd=0.0,
        message_count=1,
        config=config,
    )
    missing_without_messages = compute_costs(
        harness="copilot",
        provider_id="openai",
        model_id="gpt-5-mini",
        tokens=TokenBreakdown(input=100),
        source_cost_usd=0.0,
        message_count=0,
        config=config,
    )

    assert missing_with_messages.unpriced_count == 1
    assert missing_without_messages.unpriced_count == 0


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

    assert float(breakdown.actual_cost_usd) == pytest.approx(0.0001)
    assert float(breakdown.virtual_cost_usd) == pytest.approx(0.0003)
    assert float(breakdown.savings_usd) == pytest.approx(0.0002)
