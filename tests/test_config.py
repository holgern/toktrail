from __future__ import annotations

import pytest

from toktrail.config import (
    COPILOT_TEMPLATE_NAME,
    Price,
    load_costing_config,
    load_toktrail_config,
    normalize_identity,
    render_config_template,
)


def test_load_costing_config_missing_file_returns_default_config(tmp_path) -> None:
    config = load_costing_config(tmp_path / "missing.toml")

    assert config.default_actual_mode == "source"
    assert config.default_virtual_mode == "pricing"
    assert config.missing_price == "warn"
    assert config.price_profile is None
    assert [rule.harness for rule in config.actual_rules] == [
        "opencode",
        "pi",
        "copilot",
        "codex",
        "goose",
        "droid",
    ]
    assert config.virtual_prices == ()
    assert config.actual_prices == ()


def test_load_costing_config_parses_minimal_config(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(render_config_template(), encoding="utf-8")

    config = load_costing_config(config_path)

    assert config.default_actual_mode == "source"
    assert config.default_virtual_mode == "pricing"
    assert config.missing_price == "warn"
    assert [rule.mode for rule in config.actual_rules] == [
        "source",
        "zero",
        "zero",
        "zero",
        "zero",
        "zero",
    ]


def test_load_toktrail_config_parses_import_settings(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(render_config_template(), encoding="utf-8")

    config = load_toktrail_config(config_path)

    assert config.imports.harnesses == (
        "opencode",
        "pi",
        "copilot",
        "codex",
        "goose",
        "droid",
    )
    assert config.imports.missing_source == "warn"
    assert config.imports.include_raw_json is False
    assert config.imports.sources["opencode"].name == "opencode.db"
    assert config.imports.sources["codex"].name == "sessions"
    assert config.imports.sources["goose"].name == "sessions.db"
    assert config.imports.sources["droid"].name == "sessions"


def test_load_costing_config_parses_copilot_template(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        render_config_template(COPILOT_TEMPLATE_NAME),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)

    assert config.price_profile == "copilot-public-api-equivalent"
    assert len(config.virtual_prices) >= 10
    assert any(
        price.provider == "anthropic" and price.model == "claude-sonnet-4"
        for price in config.virtual_prices
    )


def test_load_costing_config_rejects_duplicate_aliases(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5-mini"
aliases = ["shared"]
input_usd_per_1m = 0.25
output_usd_per_1m = 2.0

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4-mini"
aliases = ["shared"]
input_usd_per_1m = 0.75
output_usd_per_1m = 4.5
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reuses alias"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_negative_prices(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5-mini"
input_usd_per_1m = -0.25
output_usd_per_1m = 2.0
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be non-negative"):
        load_costing_config(config_path)


def test_actual_cost_rule_precedence_prefers_more_specific_matches(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[costing]
default_actual_mode = "source"

[[actual_cost]]
harness = "copilot"
mode = "zero"

[[actual_cost]]
harness = "copilot"
provider = "openai"
mode = "pricing"

[[actual_cost]]
harness = "copilot"
provider = "openai"
model = "gpt-5-mini"
mode = "source"
""".strip(),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)

    assert (
        config.resolve_actual_cost_mode(
            harness="copilot",
            provider="openai",
            model="GPT 5 mini",
        )
        == "source"
    )
    assert (
        config.resolve_actual_cost_mode(
            harness="copilot",
            provider="openai",
            model="gpt-5.4",
        )
        == "pricing"
    )
    assert (
        config.resolve_actual_cost_mode(
            harness="copilot",
            provider="anthropic",
            model="claude-sonnet-4",
        )
        == "zero"
    )
    assert (
        config.resolve_actual_cost_mode(
            harness="opencode",
            provider="openai",
            model="gpt-5-mini",
        )
        == "source"
    )


def test_normalize_identity_normalizes_model_alias_forms() -> None:
    assert normalize_identity(" GPT_5 / mini ") == "gpt-5-mini"


def test_price_dataclass_keeps_optional_fields() -> None:
    price = Price(
        provider="openai",
        model="gpt-5-mini",
        aliases=("GPT-5 mini",),
        input_usd_per_1m=0.25,
        output_usd_per_1m=2.0,
    )

    assert price.cached_input_usd_per_1m is None
    assert price.cache_write_usd_per_1m is None
