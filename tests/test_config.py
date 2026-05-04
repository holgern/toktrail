from __future__ import annotations

import pytest

from toktrail.config import (
    COPILOT_TEMPLATE_NAME,
    Price,
    load_costing_config,
    load_toktrail_config,
    normalize_identity,
    render_config_template,
    summarize_costing_config,
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
        "amp",
        "claude",
        "vibe",
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
        "source",
        "zero",
        "source",
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
        "amp",
        "claude",
        "vibe",
    )
    assert config.imports.missing_source == "warn"
    assert config.imports.include_raw_json is False
    assert config.imports.sources["opencode"].name == "opencode.db"
    assert config.imports.sources["codex"].name == "sessions"
    assert config.imports.sources["goose"].name == "sessions.db"
    assert config.imports.sources["droid"].name == "sessions"
    assert config.imports.sources["amp"].name == "threads"
    assert config.imports.sources["claude"].name == "projects"
    assert config.imports.sources["vibe"].name == "session"


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


def test_load_costing_config_parses_subscription_windows(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "OpenCode Go"
usage_providers = ["OpenCode Go"]
display_name = "OpenCode Go"
timezone = "Europe/Berlin"
quota_cost_basis = "source"
enabled = true

[[subscriptions.windows]]
period = "5h"
limit_usd = 10.0
reset_mode = "fixed"
reset_at = "2026-05-01T00:00:00+02:00"
enabled = true

[[subscriptions.windows]]
period = "weekly"
limit_usd = 50.0
reset_mode = "fixed"
reset_at = "2026-05-01T00:00:00+02:00"
enabled = true

[[subscriptions.windows]]
period = "monthly"
limit_usd = 200.0
reset_mode = "fixed"
reset_at = "2026-05-01T00:00:00+02:00"
enabled = false
""".strip(),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)

    assert len(config.subscriptions) == 1
    subscription = config.subscriptions[0]
    assert subscription.id == "opencode-go"
    assert subscription.usage_providers == ("opencode-go",)
    assert subscription.display_name == "OpenCode Go"
    assert subscription.timezone == "Europe/Berlin"
    assert subscription.quota_cost_basis == "source"
    assert [window.period for window in subscription.windows] == [
        "5h",
        "weekly",
        "monthly",
    ]
    assert subscription.windows[0].limit_usd == 10.0
    assert subscription.windows[0].reset_mode == "fixed"
    assert subscription.windows[0].enabled is True
    assert subscription.windows[2].enabled is False
    assert subscription.enabled is True


def test_load_costing_config_subscription_defaults_quota_basis_and_enabled(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)

    subscription = config.subscriptions[0]
    assert subscription.quota_cost_basis == "virtual"
    assert subscription.fixed_cost_period == "monthly"
    assert subscription.fixed_cost_basis is None
    assert subscription.fixed_cost_usd is None
    assert subscription.fixed_cost_reset_at == "2026-05-01"
    assert subscription.enabled is True
    assert subscription.windows[0].reset_mode == "fixed"
    assert subscription.windows[0].enabled is True


def test_load_costing_config_parses_fixed_subscription_billing_fields(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
quota_cost_basis = "virtual"
fixed_cost_usd = 10
fixed_cost_period = "monthly"
fixed_cost_reset_at = "2026-05-01T00:00:00+02:00"
fixed_cost_basis = "source"

[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01T00:00:00+02:00"
""".strip(),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)
    subscription = config.subscriptions[0]
    assert subscription.fixed_cost_usd == 10.0
    assert subscription.fixed_cost_period == "monthly"
    assert subscription.fixed_cost_reset_at == "2026-05-01T00:00:00+02:00"
    assert subscription.fixed_cost_basis == "source"


def test_load_costing_config_rejects_negative_fixed_cost_usd(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
fixed_cost_usd = -1
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fixed_cost_usd"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_invalid_fixed_cost_period(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
fixed_cost_usd = 10
fixed_cost_period = "5h"
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fixed_cost_period"):
        load_costing_config(config_path)


def test_load_costing_config_parses_yearly_fixed_cost_period(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
fixed_cost_usd = 120
fixed_cost_period = "yearly"
fixed_cost_reset_at = "2026-01-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-01-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)
    subscription = config.subscriptions[0]
    assert subscription.fixed_cost_period == "yearly"


def test_load_costing_config_rejects_fixed_cost_without_reset_anchor(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
fixed_cost_usd = 10
fixed_cost_period = "monthly"
[[subscriptions.windows]]
period = "weekly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fixed_cost_reset_at"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_subscription_without_windows(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="windows must be an array"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_invalid_subscription_quota_cost_basis(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
quota_cost_basis = "other"
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="quota_cost_basis"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_subscription_provider_key_pre_release(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
provider = "OpenCode Go"
usage_providers = ["opencode-go"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported keys"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_empty_usage_providers(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = []
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must contain at least one provider"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_duplicate_enabled_subscription_id(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "OpenCode Go"
usage_providers = ["opencode-go"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go-proxy"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 200
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicates enabled subscription id"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_overlapping_enabled_usage_provider(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"

[[subscriptions]]
id = "zai-enterprise-plan"
usage_providers = ["zai", "zai-enterprise"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 200
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="overlaps enabled usage provider"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_non_positive_subscription_window_limits(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
[[subscriptions.windows]]
period = "daily"
limit_usd = 0
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be positive"):
        load_costing_config(config_path)

    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
[[subscriptions.windows]]
period = "daily"
limit_usd = -1
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be non-negative"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_duplicate_enabled_subscription_window_period(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]

[[subscriptions.windows]]
period = "weekly"
limit_usd = 100
reset_at = "2026-05-01"

[[subscriptions.windows]]
period = "weekly"
limit_usd = 200
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicates enabled period"):
        load_costing_config(config_path)


def test_load_costing_config_allows_disabled_duplicate_subscription_window_period(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]

[[subscriptions.windows]]
period = "weekly"
limit_usd = 100
reset_at = "2026-05-01"
enabled = true

[[subscriptions.windows]]
period = "weekly"
limit_usd = 200
reset_at = "2026-05-01"
enabled = false
""".strip(),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)
    assert len(config.subscriptions[0].windows) == 2


def test_load_costing_config_rejects_invalid_subscription_window_period(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
[[subscriptions.windows]]
period = "hourly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="period"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_invalid_subscription_window_reset_mode(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_mode = "rolling"
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reset_mode"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_invalid_subscription_window_reset_at(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "not-a-date"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reset_at"):
        load_costing_config(config_path)


def test_load_costing_config_rejects_old_flat_subscription_limit_fields(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
cycle_start = "2026-05-01"
monthly_limit_usd = 100
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported keys"):
        load_costing_config(config_path)


def test_summarize_costing_config_includes_subscription_count(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "anthropic-pro-plan"
usage_providers = ["anthropic"]
[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01"
""".strip(),
        encoding="utf-8",
    )

    summary = summarize_costing_config(load_costing_config(config_path))

    assert summary.subscription_count == 1


def test_load_costing_config_rejects_unknown_root_keys(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1
unknown_root_key = true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="root has unsupported keys"):
        load_costing_config(config_path)
