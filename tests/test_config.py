from __future__ import annotations

import pytest

from toktrail.config import (
    COPILOT_TEMPLATE_NAME,
    Price,
    load_costing_config,
    load_machine_config,
    load_pricing_config,
    load_resolved_costing_config,
    load_resolved_toktrail_config,
    load_runtime_config,
    load_toktrail_config,
    normalize_identity,
    parse_machine_config,
    parse_pricing_config,
    render_config_template,
    summarize_costing_config,
)


def _toml_path_value(path) -> str:
    return str(path).replace("\\", "/")


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
        "code",
        "goose",
        "harnessbridge",
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
        "zero",
        "source",
    ]


def test_load_machine_config_missing_file_returns_default(tmp_path) -> None:
    loaded = load_machine_config(tmp_path / "missing-machine.toml")

    assert loaded.exists is False
    assert loaded.config.name is None


def test_load_machine_config_parses_name(tmp_path) -> None:
    path = tmp_path / "machine.toml"
    path.write_text('[machine]\nname = "thinkpad"\n', encoding="utf-8")

    loaded = load_machine_config(path)

    assert loaded.exists is True
    assert loaded.config.name == "thinkpad"


def test_parse_machine_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unsupported keys"):
        parse_machine_config({"machine": {"name": "thinkpad", "extra": "x"}})


def test_machine_name_env_overrides_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "machine.toml"
    path.write_text('[machine]\nname = "desktop"\n', encoding="utf-8")
    monkeypatch.setenv("TOKTRAIL_MACHINE_NAME", "thinkpad")

    loaded = load_machine_config(path)

    assert loaded.config.name == "thinkpad"


def test_load_toktrail_config_parses_import_settings(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(render_config_template(), encoding="utf-8")

    config = load_toktrail_config(config_path)

    assert config.imports.harnesses == (
        "opencode",
        "pi",
        "copilot",
        "codex",
        "code",
        "goose",
        "harnessbridge",
        "droid",
        "amp",
        "claude",
        "vibe",
    )
    assert config.imports.missing_source == "warn"
    assert config.imports.include_raw_json is False
    assert config.imports.sources["opencode"].name == "opencode.db"
    assert config.imports.sources["codex"].name == "sessions"
    assert config.imports.sources["code"].name == "sessions"
    assert config.imports.sources["goose"].name == "sessions.db"
    assert config.imports.sources["harnessbridge"].name == "sessions"
    assert config.imports.sources["droid"].name == "sessions"
    assert config.imports.sources["amp"].name == "threads"
    assert config.imports.sources["claude"].name == "projects"
    assert config.imports.sources["vibe"].name == "session"


def test_load_runtime_config_accepts_harnessbridge_import_source(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[imports]
harnesses = ["harnessbridge"]

[imports.sources]
harnessbridge = "~/.harnessbridge/sessions"
""".strip(),
        encoding="utf-8",
    )

    config = load_runtime_config(config_path)

    assert config.imports.harnesses == ("harnessbridge",)
    assert config.imports.sources["harnessbridge"].name == "sessions"


def test_load_runtime_config_accepts_code_import_source(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[imports]
harnesses = ["code"]

[imports.sources]
code = "~/.code/sessions"
""".strip(),
        encoding="utf-8",
    )

    config = load_runtime_config(config_path)

    assert config.imports.harnesses == ("code",)
    assert config.imports.sources["code"].as_posix().endswith("/.code/sessions")


def test_load_runtime_config_sync_git_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    config = load_runtime_config(config_path)

    assert config.sync_git.repo is None
    assert config.sync_git.remote == "origin"
    assert config.sync_git.branch == "main"
    assert config.sync_git.state_dir == "state"
    assert config.sync_git.redact_raw_json is True
    assert config.sync_git.remote_active == "close-at-export"
    assert config.sync_git.on_conflict == "fail"
    assert config.sync_git.track == ()


def test_load_runtime_config_parses_sync_git_table(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[sync.git]
repo = "~/toktrail-state"
remote = "origin"
branch = "main"
state_dir = "state"
auto_push = false
redact_raw_json = true
remote_active = "keep"
on_conflict = "skip"
track = ["prices", "provider-prices", "subscriptions"]
""".strip(),
        encoding="utf-8",
    )

    config = load_runtime_config(config_path)

    assert config.sync_git.repo == "~/toktrail-state"
    assert config.sync_git.remote == "origin"
    assert config.sync_git.branch == "main"
    assert config.sync_git.state_dir == "state"
    assert config.sync_git.auto_push is False
    assert config.sync_git.redact_raw_json is True
    assert config.sync_git.remote_active == "keep"
    assert config.sync_git.on_conflict == "skip"
    assert config.sync_git.track == ("prices", "provider-prices", "subscriptions")


def test_load_runtime_config_rejects_sync_git_auto_import(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[sync.git]
auto_import = false
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sync.git.auto_import is no longer supported"):
        load_runtime_config(config_path)


def test_load_runtime_config_parses_sync_git_auto_export_alias(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[sync.git]
auto_export = false
""".strip(),
        encoding="utf-8",
    )

    config = load_runtime_config(config_path)
    assert config.sync_git.auto_push is False


def test_load_runtime_config_parses_sync_git_track_all(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[sync.git]
track = ["all", "prices"]
""".strip(),
        encoding="utf-8",
    )

    config = load_runtime_config(config_path)
    assert config.sync_git.track == (
        "config",
        "prices",
        "provider-prices",
        "subscriptions",
    )


def test_load_runtime_config_rejects_sync_git_invalid_track(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[sync.git]
track = ["bogus"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sync.git.track"):
        load_runtime_config(config_path)


def test_load_runtime_config_rejects_sync_git_unknown_key(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[sync.git]
bogus = 1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sync.git has unsupported keys"):
        load_runtime_config(config_path)


def test_load_runtime_config_rejects_sync_git_invalid_remote_active(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[sync.git]
remote_active = "invalid"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sync.git.remote_active"):
        load_runtime_config(config_path)


def test_load_runtime_config_rejects_sync_git_invalid_on_conflict(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[sync.git]
on_conflict = "invalid"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sync.git.on_conflict"):
        load_runtime_config(config_path)


def test_load_toktrail_config_exposes_statusline_defaults(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(render_config_template(), encoding="utf-8")

    config = load_toktrail_config(config_path)

    assert config.statusline.default_harness == "auto"
    assert config.statusline.basis == "virtual"
    assert config.statusline.refresh == "auto"
    assert config.statusline.max_width == 120
    assert config.statusline.cache.output_cache_secs == 2
    assert config.statusline.cache.stale_after_secs == 60
    assert config.statusline.elements == (
        "harness",
        "model",
        "tokens",
        "cached",
        "cost",
        "quota",
        "burn",
        "unpriced",
    )
    assert config.context_windows == ()


def test_load_runtime_config_parses_statusline_and_context_windows(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[statusline]
default_harness = "codex"
basis = "source"
refresh = "always"
session = "latest"
max_width = 90
show_emojis = true
color = "never"
empty = "message"
active_session_window_minutes = 15
elements = ["harness", "model", "tokens", "context"]

[statusline.cache]
output_cache_secs = 4
min_refresh_interval_secs = 8
stale_after_secs = 75

[statusline.thresholds]
quota_warning_percent = 70
quota_danger_percent = 95
burn_warning_percent = 75
burn_danger_percent = 110
context_warning_percent = 60
context_danger_percent = 85

[[context_window]]
provider = "openai"
model = "gpt-5.3-codex"
tokens = 272000
""".strip(),
        encoding="utf-8",
    )

    config = load_runtime_config(config_path)

    assert config.statusline.default_harness == "codex"
    assert config.statusline.basis == "source"
    assert config.statusline.refresh == "always"
    assert config.statusline.session == "latest"
    assert config.statusline.max_width == 90
    assert config.statusline.show_emojis is True
    assert config.statusline.color == "never"
    assert config.statusline.empty == "message"
    assert config.statusline.active_session_window_minutes == 15
    assert config.statusline.elements == ("harness", "model", "tokens", "context")
    assert config.statusline.cache.output_cache_secs == 4
    assert config.statusline.cache.min_refresh_interval_secs == 8
    assert config.statusline.cache.stale_after_secs == 75
    assert config.statusline.thresholds.quota_warning_percent == 70
    assert config.statusline.thresholds.quota_danger_percent == 95
    assert config.statusline.thresholds.burn_warning_percent == 75
    assert config.statusline.thresholds.burn_danger_percent == 110
    assert config.statusline.thresholds.context_warning_percent == 60
    assert config.statusline.thresholds.context_danger_percent == 85
    assert len(config.context_windows) == 1
    assert config.context_windows[0].provider == "openai"
    assert config.context_windows[0].model == "gpt-5.3-codex"
    assert config.context_windows[0].tokens == 272000


def test_load_runtime_config_rejects_invalid_statusline_element(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[statusline]
elements = ["harness", "bogus"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="statusline.elements\\[2\\]"):
        load_runtime_config(config_path)


def test_load_runtime_config_parses_areas_rules(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[areas]
auto_detect = true
warn_on_mismatch = true
unassigned_warning_threshold = 0.25

[[areas.rules]]
area = "work/odoo"
cwd_globs = ["~/work/odoo/**"]
git_remotes = ["git@github.com:company/odoo*.git"]
priority = 100
""".strip(),
        encoding="utf-8",
    )
    config = load_runtime_config(config_path)
    assert config.areas.auto_detect is True
    assert config.areas.warn_on_mismatch is True
    assert config.areas.unassigned_warning_threshold == 0.25
    assert len(config.areas.rules) == 1
    assert config.areas.rules[0].area == "work/odoo"
    assert config.areas.rules[0].cwd_globs == ("~/work/odoo/**",)
    assert config.areas.rules[0].git_remotes == ("git@github.com:company/odoo*.git",)
    assert config.areas.rules[0].priority == 100


def test_load_costing_config_parses_copilot_template(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        render_config_template(COPILOT_TEMPLATE_NAME),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)

    assert config.price_profile == "copilot-public-api-equivalent"
    assert config.virtual_prices == ()


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


def test_load_costing_config_parses_context_tier_prices(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
aliases = ["GPT 5.4"]
context_min_tokens = 0
context_max_tokens = 272000
context_label = "<= 272K"
input_usd_per_1m = 2.5
cached_input_usd_per_1m = 0.25
output_usd_per_1m = 15.0

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
aliases = ["GPT 5.4"]
context_min_tokens = 272001
context_label = "> 272K"
input_usd_per_1m = 5.0
cached_input_usd_per_1m = 0.5
output_usd_per_1m = 22.5
""".strip(),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)
    assert len(config.virtual_prices) == 2
    short = next(p for p in config.virtual_prices if p.context_max_tokens == 272000)
    long = next(p for p in config.virtual_prices if p.context_min_tokens == 272001)
    assert short.context_basis == "prompt_like"
    assert short.context_label == "<= 272K"
    assert long.context_label == "> 272K"


def test_load_costing_config_rejects_overlapping_context_ranges(tmp_path) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
context_min_tokens = 0
context_max_tokens = 272000
input_usd_per_1m = 2.5
output_usd_per_1m = 15.0

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
context_min_tokens = 200000
context_max_tokens = 300000
input_usd_per_1m = 5.0
output_usd_per_1m = 22.5
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="context range overlaps prior"):
        load_costing_config(config_path)


def test_load_costing_config_allows_same_alias_across_context_variants(
    tmp_path,
) -> None:
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
aliases = ["shared"]
context_min_tokens = 0
context_max_tokens = 272000
input_usd_per_1m = 2.5
output_usd_per_1m = 15.0

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
aliases = ["shared"]
context_min_tokens = 272001
input_usd_per_1m = 5.0
output_usd_per_1m = 22.5
""".strip(),
        encoding="utf-8",
    )

    config = load_costing_config(config_path)
    assert len(config.virtual_prices) == 2


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


def test_load_resolved_costing_config_loads_provider_price_files(tmp_path) -> None:
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_config_template(), encoding="utf-8")
    prices_dir = config_path.with_name("prices")
    prices_dir.mkdir(parents=True, exist_ok=True)
    (prices_dir / "openai.toml").write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )
    (prices_dir / "zai.toml").write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "zai"
model = "glm-5.1"
input_usd_per_1m = 1.4
output_usd_per_1m = 4.4
""".strip(),
        encoding="utf-8",
    )

    loaded = load_resolved_costing_config(config_cli_value=config_path)

    assert loaded.manual_prices_exists is False
    assert loaded.provider_prices_exists is True
    assert loaded.prices_exists is True
    assert loaded.price_paths == (prices_dir / "openai.toml", prices_dir / "zai.toml")
    assert {
        (price.provider, price.model) for price in loaded.config.virtual_prices
    } == {
        ("openai", "gpt-5.5"),
        ("zai", "glm-5.1"),
    }


def test_load_resolved_toktrail_config_uses_git_repo_for_tracked_costing_files(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.toml"
    repo = tmp_path / "toktrail-state"
    config_path.write_text(
        f"""
config_version = 1

[sync.git]
repo = "{_toml_path_value(repo)}"
track = ["prices", "provider-prices", "subscriptions"]
""".strip(),
        encoding="utf-8",
    )

    loaded = load_resolved_toktrail_config(config_cli_value=config_path)
    assert loaded.prices_path == repo / "config" / "prices.toml"
    assert loaded.prices_dir == repo / "config" / "prices"
    assert loaded.subscriptions_path == repo / "config" / "subscriptions.toml"


def test_load_resolved_toktrail_config_env_overrides_git_tracked_paths(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.toml"
    repo = tmp_path / "toktrail-state"
    env_prices = tmp_path / "env-prices.toml"
    env_prices_dir = tmp_path / "env-prices"
    env_subscriptions = tmp_path / "env-subscriptions.toml"
    config_path.write_text(
        f"""
config_version = 1

[sync.git]
repo = "{_toml_path_value(repo)}"
track = ["prices", "provider-prices", "subscriptions"]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOKTRAIL_PRICES", str(env_prices))
    monkeypatch.setenv("TOKTRAIL_PRICES_DIR", str(env_prices_dir))
    monkeypatch.setenv("TOKTRAIL_SUBSCRIPTIONS", str(env_subscriptions))

    loaded = load_resolved_toktrail_config(config_cli_value=config_path)
    assert loaded.prices_path == env_prices
    assert loaded.prices_dir == env_prices_dir
    assert loaded.subscriptions_path == env_subscriptions


def test_load_resolved_toktrail_config_cli_overrides_git_tracked_paths(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.toml"
    repo = tmp_path / "toktrail-state"
    cli_prices = tmp_path / "cli-prices.toml"
    cli_prices_dir = tmp_path / "cli-prices"
    cli_subscriptions = tmp_path / "cli-subscriptions.toml"
    config_path.write_text(
        f"""
config_version = 1

[sync.git]
repo = "{_toml_path_value(repo)}"
track = ["prices", "provider-prices", "subscriptions"]
""".strip(),
        encoding="utf-8",
    )

    loaded = load_resolved_toktrail_config(
        config_cli_value=config_path,
        prices_cli_value=cli_prices,
        prices_dir_cli_value=cli_prices_dir,
        subscriptions_cli_value=cli_subscriptions,
    )
    assert loaded.prices_path == cli_prices
    assert loaded.prices_dir == cli_prices_dir
    assert loaded.subscriptions_path == cli_subscriptions


def test_manual_prices_override_provider_prices(tmp_path) -> None:
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_config_template(), encoding="utf-8")
    prices_dir = config_path.with_name("prices")
    prices_dir.mkdir(parents=True, exist_ok=True)
    (prices_dir / "openai.toml").write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )
    config_path.with_name("prices.toml").write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 6
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )

    loaded = load_resolved_costing_config(config_cli_value=config_path)
    selected = next(
        price
        for price in loaded.config.virtual_prices
        if price.provider == "openai" and price.model == "gpt-5.5"
    )

    assert selected.input_usd_per_1m == 6.0


def test_duplicate_provider_price_files_error(tmp_path) -> None:
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_config_template(), encoding="utf-8")
    prices_dir = config_path.with_name("prices")
    prices_dir.mkdir(parents=True, exist_ok=True)
    (prices_dir / "openai.toml").write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )
    (prices_dir / "openai-copy.toml").write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 6
output_usd_per_1m = 36
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate generated price"):
        load_resolved_costing_config(config_cli_value=config_path)


def test_provider_price_files_preserve_current_single_file_loader(tmp_path) -> None:
    prices_path = tmp_path / "prices.toml"
    prices_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )

    loaded = load_pricing_config(prices_path)
    parsed_empty = parse_pricing_config({})

    assert len(loaded.virtual_prices) == 1
    assert parsed_empty.virtual_prices == ()
    assert parsed_empty.actual_prices == ()


def test_config_summary_counts_provider_and_manual_prices(tmp_path) -> None:
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_config_template(), encoding="utf-8")
    prices_dir = config_path.with_name("prices")
    prices_dir.mkdir(parents=True, exist_ok=True)
    openai_path = prices_dir / "openai.toml"
    openai_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )
    manual_path = config_path.with_name("prices.toml")
    manual_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 6
output_usd_per_1m = 30

[[pricing.virtual]]
provider = "zai"
model = "glm-5.1"
input_usd_per_1m = 1.4
output_usd_per_1m = 4.4
""".strip(),
        encoding="utf-8",
    )

    loaded = load_resolved_costing_config(config_cli_value=config_path)
    summary = summarize_costing_config(loaded.config)

    assert summary.virtual_price_count == 2
    assert loaded.price_paths == (openai_path, manual_path)
