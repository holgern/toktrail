from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

from toktrail.paths import (
    resolve_toktrail_config_path,
    resolve_toktrail_prices_dir,
    resolve_toktrail_prices_path,
    resolve_toktrail_subscriptions_path,
    toktrail_prices_dir_env_is_set,
    toktrail_prices_env_is_set,
    toktrail_subscriptions_env_is_set,
)

ActualCostMode = Literal["source", "zero", "pricing"]
VirtualCostMode = Literal["zero", "pricing"]
MissingPriceMode = Literal["zero", "warn"]
ImportMissingSourceMode = Literal["warn", "error", "skip"]
GitSyncConflictMode = Literal["fail", "skip"]
GitSyncRemoteActiveMode = Literal["fail", "close-at-export", "keep"]
GitSyncTrackedFile = Literal[
    "config",
    "prices",
    "provider-prices",
    "subscriptions",
]
SubscriptionCostBasis = Literal["source", "actual", "virtual"]
SubscriptionWindowPeriod = Literal["5h", "daily", "weekly", "monthly", "yearly"]
SubscriptionWindowResetMode = Literal["fixed", "first_use"]
SubscriptionFixedCostPeriod = Literal["daily", "weekly", "monthly", "yearly"]
PriceContextBasis = Literal["prompt_like"]
StatuslineRefreshMode = Literal["never", "auto", "always"]
StatuslineSessionMode = Literal["auto", "latest", "none"]
StatuslineColorMode = Literal["auto", "always", "never"]
StatuslineEmptyMode = Literal["silent", "message"]

CONFIG_VERSION = 1
DEFAULT_TEMPLATE_NAME = "default"
COPILOT_TEMPLATE_NAME = "copilot"
_DEFAULT_STATUSLINE_ELEMENTS = (
    "harness",
    "model",
    "tokens",
    "cached",
    "cost",
    "quota",
    "burn",
    "unpriced",
)
_VALID_ACTUAL_COST_MODES = {"source", "zero", "pricing"}
_VALID_VIRTUAL_COST_MODES = {"zero", "pricing"}
_VALID_MISSING_PRICE_MODES = {"zero", "warn"}
_VALID_IMPORT_MISSING_SOURCE_MODES = {"warn", "error", "skip"}
_VALID_GIT_SYNC_CONFLICT_MODES = {"fail", "skip"}
_VALID_GIT_SYNC_REMOTE_ACTIVE_MODES = {"fail", "close-at-export", "keep"}
_VALID_GIT_SYNC_TRACKED_FILES = {
    "config",
    "prices",
    "provider-prices",
    "subscriptions",
}
_ALL_GIT_SYNC_TRACKED_FILES: tuple[GitSyncTrackedFile, ...] = (
    "config",
    "prices",
    "provider-prices",
    "subscriptions",
)
_VALID_SUBSCRIPTION_COST_BASES = {"source", "actual", "virtual"}
_VALID_SUBSCRIPTION_WINDOW_PERIODS = {"5h", "daily", "weekly", "monthly", "yearly"}
_VALID_SUBSCRIPTION_WINDOW_RESET_MODES = {"fixed", "first_use"}
_VALID_SUBSCRIPTION_FIXED_COST_PERIODS = {"daily", "weekly", "monthly", "yearly"}
_VALID_PRICE_CONTEXT_BASES = {"prompt_like"}
_VALID_STATUSLINE_REFRESH_MODES = {"never", "auto", "always"}
_VALID_STATUSLINE_SESSION_MODES = {"auto", "latest", "none"}
_VALID_STATUSLINE_COLOR_MODES = {"auto", "always", "never"}
_VALID_STATUSLINE_EMPTY_MODES = {"silent", "message"}
_VALID_STATUSLINE_ELEMENTS = {
    "harness",
    "agent",
    "provider",
    "model",
    "session",
    "tokens",
    "cached",
    "reasoning",
    "cost",
    "savings",
    "quota",
    "reset",
    "burn",
    "context",
    "cache_ratio",
    "unpriced",
    "stale",
    "cwd",
}
_PRICE_FIELDS = {
    "provider",
    "model",
    "aliases",
    "input_usd_per_1m",
    "cached_input_usd_per_1m",
    "cache_write_usd_per_1m",
    "cached_output_usd_per_1m",
    "output_usd_per_1m",
    "reasoning_usd_per_1m",
    "category",
    "release_status",
    "context_min_tokens",
    "context_max_tokens",
    "context_label",
    "context_basis",
}
_ACTUAL_COST_RULE_FIELDS = {"harness", "provider", "model", "mode"}
_IMPORT_FIELDS = {"harnesses", "sources", "missing_source", "include_raw_json"}
_SYNC_FIELDS = {"git"}
_SYNC_GIT_FIELDS = {
    "repo",
    "remote",
    "branch",
    "archive_dir",
    "auto_pull",
    "auto_push",
    "auto_import",
    "auto_export",
    "redact_raw_json",
    "include_config",
    "remote_active",
    "on_conflict",
    "track",
}
_COSTING_FIELDS = {
    "default_actual_mode",
    "default_virtual_mode",
    "missing_price",
    "price_profile",
}
_PRICING_FIELDS = {"virtual", "actual"}
_STATUSLINE_FIELDS = {
    "default_harness",
    "basis",
    "refresh",
    "session",
    "max_width",
    "show_emojis",
    "color",
    "empty",
    "active_session_window_minutes",
    "elements",
    "cache",
    "thresholds",
}
_STATUSLINE_CACHE_FIELDS = {
    "output_cache_secs",
    "min_refresh_interval_secs",
    "stale_after_secs",
}
_STATUSLINE_THRESHOLDS_FIELDS = {
    "quota_warning_percent",
    "quota_danger_percent",
    "burn_warning_percent",
    "burn_danger_percent",
    "context_warning_percent",
    "context_danger_percent",
}
_CONTEXT_WINDOW_FIELDS = {"provider", "model", "tokens"}
_SUBSCRIPTION_FIELDS = {
    "id",
    "display_name",
    "timezone",
    "usage_providers",
    "quota_cost_basis",
    "fixed_cost_usd",
    "fixed_cost_period",
    "fixed_cost_reset_at",
    "fixed_cost_basis",
    "windows",
    "enabled",
}
_SUBSCRIPTION_WINDOW_FIELDS = {
    "period",
    "limit_usd",
    "reset_mode",
    "reset_at",
    "enabled",
}
_ROOT_FIELDS = {
    "config_version",
    "imports",
    "sync",
    "costing",
    "actual_cost",
    "pricing",
    "subscriptions",
    "statusline",
    "context_window",
}
_RUNTIME_CONFIG_ROOT_FIELDS = {
    "config_version",
    "imports",
    "sync",
    "costing",
    "actual_cost",
    "statusline",
    "context_window",
}
_PRICE_CONFIG_ROOT_FIELDS = {
    "config_version",
    "pricing",
    "metadata",
}
_SUBSCRIPTION_CONFIG_ROOT_FIELDS = {
    "config_version",
    "subscriptions",
    "metadata",
}
_SUPPORTED_HARNESSES = {
    "opencode",
    "pi",
    "copilot",
    "code",
    "codex",
    "goose",
    "harnessbridge",
    "droid",
    "amp",
    "claude",
    "vibe",
}
_SEPARATOR_RE = re.compile(r"[/_\s]+")
_INVALID_IDENTITY_CHARS_RE = re.compile(r"[^a-z0-9.-]+")
_DASH_RE = re.compile(r"-+")
_GIT_CONFIG_DIR = "config"
_GIT_CONFIG_FILE = "config.toml"
_GIT_PRICES_FILE = "prices.toml"
_GIT_PRICES_DIR = "prices"
_GIT_SUBSCRIPTIONS_FILE = "subscriptions.toml"

DEFAULT_CONFIG_TEXT = """\
config_version = 1

[imports]
harnesses = [
  "opencode", "pi", "copilot", "codex", "code", "goose",
  "harnessbridge", "droid", "amp", "claude", "vibe"
]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "~/.local/share/opencode/opencode.db"
pi = "~/.pi/agent/sessions"
copilot = "~/.copilot/otel"
codex = "~/.codex/sessions"
code = "~/.code/sessions"
goose = "~/.local/share/goose/sessions/sessions.db"
harnessbridge = "~/.harnessbridge/sessions"
droid = "~/.factory/sessions"
amp = "~/.local/share/amp/threads"
claude = "~/.claude/projects"
vibe = "~/.vibe/logs/session"

[sync.git]
# repo = "~/.local/share/toktrail/git-sync"
# remote = "origin"
# branch = "main"
# archive_dir = "archives"
# auto_import = true  # alias: auto_pull
# auto_export = true  # alias: auto_push
# redact_raw_json = true
# include_config = false
# remote_active = "close-at-export"
# on_conflict = "fail"
# track = ["prices", "provider-prices", "subscriptions"]

[costing]
default_actual_mode = "source"
default_virtual_mode = "pricing"
missing_price = "warn"

# [[subscriptions]]
# id = "opencode-go"
# display_name = "OpenCode Go"
# timezone = "Europe/Berlin"
# usage_providers = ["opencode-go"]
# quota_cost_basis = "virtual"
# fixed_cost_usd = 10.00
# fixed_cost_period = "monthly"
# fixed_cost_reset_at = "2026-05-01T00:00:00+02:00"
# fixed_cost_basis = "virtual"
#
# [[subscriptions.windows]]
# period = "5h"
# limit_usd = 10.00
# reset_mode = "fixed"
# reset_at = "2026-05-01T00:00:00+02:00"
#
# [[subscriptions.windows]]
# period = "weekly"
# limit_usd = 50.00
# reset_mode = "fixed"
# reset_at = "2026-05-01T00:00:00+02:00"
#
# [[subscriptions.windows]]
# period = "monthly"
# limit_usd = 200.00
# reset_mode = "fixed"
# reset_at = "2026-05-01T00:00:00+02:00"

[[actual_cost]]
harness = "opencode"
mode = "source"

[[actual_cost]]
harness = "pi"
mode = "zero"

[[actual_cost]]
harness = "copilot"
mode = "zero"

[[actual_cost]]
harness = "codex"
mode = "zero"

[[actual_cost]]
harness = "code"
mode = "zero"

[[actual_cost]]
harness = "goose"
mode = "zero"

[[actual_cost]]
harness = "harnessbridge"
mode = "source"

[[actual_cost]]
harness = "droid"
mode = "zero"

[[actual_cost]]
harness = "amp"
mode = "source"

[[actual_cost]]
harness = "claude"
mode = "zero"

[[actual_cost]]
harness = "vibe"
mode = "source"
"""

DEFAULT_PRICES_TEXT = """\
config_version = 1

[metadata]
# generated_by = "toktrail pricing parse"
# updated_at = "2026-05-05"
"""

DEFAULT_SUBSCRIPTIONS_TEXT = """\
config_version = 1

[metadata]
# updated_at = "2026-05-05"
"""

COPILOT_CONFIG_TEXT = """\
config_version = 1

[imports]
harnesses = [
  "opencode", "pi", "copilot", "codex", "code", "goose",
  "harnessbridge", "droid", "amp", "claude", "vibe"
]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "~/.local/share/opencode/opencode.db"
pi = "~/.pi/agent/sessions"
copilot = "~/.copilot/otel"
codex = "~/.codex/sessions"
code = "~/.code/sessions"
goose = "~/.local/share/goose/sessions/sessions.db"
harnessbridge = "~/.harnessbridge/sessions"
droid = "~/.factory/sessions"
amp = "~/.local/share/amp/threads"
claude = "~/.claude/projects"
vibe = "~/.vibe/logs/session"

[costing]
default_actual_mode = "source"
default_virtual_mode = "pricing"
missing_price = "warn"
price_profile = "copilot-public-api-equivalent"

[[actual_cost]]
harness = "copilot"
mode = "zero"

[[actual_cost]]
harness = "pi"
mode = "zero"

[[actual_cost]]
harness = "opencode"
mode = "source"

[[actual_cost]]
harness = "codex"
mode = "zero"

[[actual_cost]]
harness = "code"
mode = "zero"

[[actual_cost]]
harness = "goose"
mode = "zero"

[[actual_cost]]
harness = "harnessbridge"
mode = "source"

[[actual_cost]]
harness = "droid"
mode = "zero"

[[actual_cost]]
harness = "amp"
mode = "source"

[[actual_cost]]
harness = "claude"
mode = "zero"

[[actual_cost]]
harness = "vibe"
mode = "source"
"""

COPILOT_TEMPLATE_TEXT = """\
config_version = 1

[imports]
harnesses = [
  "opencode", "pi", "copilot", "codex", "code", "goose",
  "harnessbridge", "droid", "amp", "claude", "vibe"
]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "~/.local/share/opencode/opencode.db"
pi = "~/.pi/agent/sessions"
copilot = "~/.copilot/otel"
codex = "~/.codex/sessions"
code = "~/.code/sessions"
goose = "~/.local/share/goose/sessions/sessions.db"
harnessbridge = "~/.harnessbridge/sessions"
droid = "~/.factory/sessions"
amp = "~/.local/share/amp/threads"
claude = "~/.claude/projects"
vibe = "~/.vibe/logs/session"

[costing]
default_actual_mode = "source"
default_virtual_mode = "pricing"
missing_price = "warn"
price_profile = "copilot-public-api-equivalent"

# [[subscriptions]]
# id = "opencode-go"
# display_name = "OpenCode Go"
# timezone = "Europe/Berlin"
# usage_providers = ["opencode-go"]
# quota_cost_basis = "virtual"
# fixed_cost_usd = 10.00
# fixed_cost_period = "monthly"
# fixed_cost_reset_at = "2026-05-01T00:00:00+02:00"
# fixed_cost_basis = "virtual"
#
# [[subscriptions.windows]]
# period = "5h"
# limit_usd = 10.00
# reset_mode = "fixed"
# reset_at = "2026-05-01T00:00:00+02:00"
#
# [[subscriptions.windows]]
# period = "weekly"
# limit_usd = 50.00
# reset_mode = "fixed"
# reset_at = "2026-05-01T00:00:00+02:00"
#
# [[subscriptions.windows]]
# period = "monthly"
# limit_usd = 200.00
# reset_mode = "fixed"
# reset_at = "2026-05-01T00:00:00+02:00"

[[actual_cost]]
harness = "copilot"
mode = "zero"

[[actual_cost]]
harness = "pi"
mode = "zero"

[[actual_cost]]
harness = "opencode"
mode = "source"

[[actual_cost]]
harness = "codex"
mode = "zero"

[[actual_cost]]
harness = "code"
mode = "zero"

[[actual_cost]]
harness = "goose"
mode = "zero"

[[actual_cost]]
harness = "harnessbridge"
mode = "source"

[[actual_cost]]
harness = "droid"
mode = "zero"

[[actual_cost]]
harness = "amp"
mode = "source"

[[actual_cost]]
harness = "claude"
mode = "zero"

[[actual_cost]]
harness = "vibe"
mode = "source"
# OpenAI

[[pricing.virtual]]
provider = "openai"
model = "gpt-4.11"
aliases = ["GPT-4.11", "gpt-4.11"]
release_status = "GA"
category = "Versatile"
input_usd_per_1m = 2.00
cached_input_usd_per_1m = 0.50
output_usd_per_1m = 8.00

[[pricing.virtual]]
provider = "openai"
model = "gpt-5-mini"
aliases = ["GPT-5 mini", "gpt-5-mini", "gpt-5 mini"]
release_status = "GA"
category = "Lightweight"
input_usd_per_1m = 0.25
cached_input_usd_per_1m = 0.025
output_usd_per_1m = 2.00

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.2"
aliases = ["GPT-5.2", "gpt-5.2"]
release_status = "GA"
category = "Versatile"
input_usd_per_1m = 1.75
cached_input_usd_per_1m = 0.175
output_usd_per_1m = 14.00

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.2-codex"
aliases = ["GPT-5.2-Codex", "gpt-5.2-codex"]
release_status = "GA"
category = "Powerful"
input_usd_per_1m = 1.75
cached_input_usd_per_1m = 0.175
output_usd_per_1m = 14.00

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.3-codex"
aliases = ["GPT-5.3-Codex", "gpt-5.3-codex"]
release_status = "GA"
category = "Powerful"
input_usd_per_1m = 1.75
cached_input_usd_per_1m = 0.175
output_usd_per_1m = 14.00

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
aliases = ["GPT-5.4", "gpt-5.4"]
release_status = "GA"
category = "Versatile"
input_usd_per_1m = 2.50
cached_input_usd_per_1m = 0.25
output_usd_per_1m = 15.00

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4-mini"
aliases = ["GPT-5.4 mini", "gpt-5.4-mini", "gpt-5.4 mini"]
release_status = "GA"
category = "Lightweight"
input_usd_per_1m = 0.75
cached_input_usd_per_1m = 0.075
output_usd_per_1m = 4.50

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4-nano"
aliases = ["GPT-5.4 nano", "gpt-5.4-nano", "gpt-5.4 nano"]
release_status = "GA"
category = "Lightweight"
input_usd_per_1m = 0.20
cached_input_usd_per_1m = 0.02
output_usd_per_1m = 1.25

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
aliases = ["GPT-5.5", "gpt-5.5"]
release_status = "GA"
category = "Powerful"
input_usd_per_1m = 5.00
cached_input_usd_per_1m = 0.50
output_usd_per_1m = 30.00

# Anthropic

[[pricing.virtual]]
provider = "anthropic"
model = "claude-haiku-4.5"
aliases = ["Claude Haiku 4.5", "claude-haiku-4.5", "claude haiku 4.5"]
release_status = "GA"
category = "Versatile"
input_usd_per_1m = 1.00
cached_input_usd_per_1m = 0.10
cache_write_usd_per_1m = 1.25
output_usd_per_1m = 5.00

[[pricing.virtual]]
provider = "anthropic"
model = "claude-sonnet-4"
aliases = ["Claude Sonnet 4", "claude-sonnet-4", "claude sonnet 4"]
release_status = "GA"
category = "Versatile"
input_usd_per_1m = 3.00
cached_input_usd_per_1m = 0.30
cache_write_usd_per_1m = 3.75
output_usd_per_1m = 15.00

[[pricing.virtual]]
provider = "anthropic"
model = "claude-sonnet-4.5"
aliases = ["Claude Sonnet 4.5", "claude-sonnet-4.5", "claude sonnet 4.5"]
release_status = "GA"
category = "Versatile"
input_usd_per_1m = 3.00
cached_input_usd_per_1m = 0.30
cache_write_usd_per_1m = 3.75
output_usd_per_1m = 15.00

[[pricing.virtual]]
provider = "anthropic"
model = "claude-sonnet-4.6"
aliases = ["Claude Sonnet 4.6", "claude-sonnet-4.6", "claude sonnet 4.6"]
release_status = "GA"
category = "Versatile"
input_usd_per_1m = 3.00
cached_input_usd_per_1m = 0.30
cache_write_usd_per_1m = 3.75
output_usd_per_1m = 15.00

[[pricing.virtual]]
provider = "anthropic"
model = "claude-opus-4.5"
aliases = ["Claude Opus 4.5", "claude-opus-4.5", "claude opus 4.5"]
release_status = "GA"
category = "Powerful"
input_usd_per_1m = 5.00
cached_input_usd_per_1m = 0.50
cache_write_usd_per_1m = 6.25
output_usd_per_1m = 25.00

[[pricing.virtual]]
provider = "anthropic"
model = "claude-opus-4.6"
aliases = ["Claude Opus 4.6", "claude-opus-4.6", "claude opus 4.6"]
release_status = "GA"
category = "Powerful"
input_usd_per_1m = 5.00
cached_input_usd_per_1m = 0.50
cache_write_usd_per_1m = 6.25
output_usd_per_1m = 25.00

[[pricing.virtual]]
provider = "anthropic"
model = "claude-opus-4.7"
aliases = ["Claude Opus 4.7", "claude-opus-4.7", "claude opus 4.7"]
release_status = "GA"
category = "Powerful"
input_usd_per_1m = 5.00
cached_input_usd_per_1m = 0.50
cache_write_usd_per_1m = 6.25
output_usd_per_1m = 25.00

# Google

[[pricing.virtual]]
provider = "google"
model = "gemini-2.5-pro"
aliases = ["Gemini 2.5 Pro", "gemini-2.5-pro", "gemini 2.5 pro"]
release_status = "GA"
category = "Powerful"
input_usd_per_1m = 1.25
cached_input_usd_per_1m = 0.125
output_usd_per_1m = 10.00

[[pricing.virtual]]
provider = "google"
model = "gemini-3-flash"
aliases = ["Gemini 3 Flash", "gemini-3-flash", "gemini 3 flash"]
release_status = "Public preview"
category = "Lightweight"
input_usd_per_1m = 0.50
cached_input_usd_per_1m = 0.05
output_usd_per_1m = 3.00

[[pricing.virtual]]
provider = "google"
model = "gemini-3.1-pro"
aliases = ["Gemini 3.1 Pro", "gemini-3.1-pro", "gemini 3.1 pro"]
release_status = "Public preview"
category = "Powerful"
input_usd_per_1m = 2.00
cached_input_usd_per_1m = 0.20
output_usd_per_1m = 12.00

# xAI

[[pricing.virtual]]
provider = "xai"
model = "grok-code-fast-1"
aliases = ["Grok Code Fast 1", "grok-code-fast-1", "grok code fast 1"]
release_status = "GA"
category = "Lightweight"
input_usd_per_1m = 0.20
cached_input_usd_per_1m = 0.02
output_usd_per_1m = 1.50

# Fine-tuned GitHub

[[pricing.virtual]]
provider = "github"
model = "raptor-mini"
aliases = ["Raptor mini", "raptor-mini", "raptor mini"]
release_status = "Public preview"
category = "Versatile"
input_usd_per_1m = 0.25
cached_input_usd_per_1m = 0.025
output_usd_per_1m = 2.00

[[pricing.virtual]]
provider = "github"
model = "goldeneye"
aliases = ["Goldeneye", "goldeneye"]
release_status = "Public preview"
category = "Powerful"
input_usd_per_1m = 1.25
cached_input_usd_per_1m = 0.125
output_usd_per_1m = 10.00
"""


@dataclass(frozen=True)
class Price:
    provider: str
    model: str
    aliases: tuple[str, ...]
    input_usd_per_1m: float
    cached_input_usd_per_1m: float | None = None
    cache_write_usd_per_1m: float | None = None
    cached_output_usd_per_1m: float | None = None
    output_usd_per_1m: float = 0.0
    reasoning_usd_per_1m: float | None = None
    category: str | None = None
    release_status: str | None = None
    context_min_tokens: int | None = None
    context_max_tokens: int | None = None
    context_label: str | None = None
    context_basis: PriceContextBasis = "prompt_like"


@dataclass(frozen=True)
class ActualCostRule:
    harness: str | None
    provider: str | None
    model: str | None
    mode: ActualCostMode

    def matches(
        self,
        *,
        harness: str,
        provider: str | None,
        model: str | None,
    ) -> bool:
        return (
            _matches_rule_value(self.harness, harness)
            and _matches_rule_value(self.provider, provider)
            and _matches_rule_value(self.model, model)
        )

    def specificity(self) -> tuple[int, int, int]:
        return (
            _specificity_score(self.harness),
            _specificity_score(self.provider),
            _specificity_score(self.model),
        )


@dataclass(frozen=True)
class SubscriptionWindowConfig:
    period: SubscriptionWindowPeriod
    limit_usd: float
    reset_at: str
    reset_mode: SubscriptionWindowResetMode = "fixed"
    enabled: bool = True


@dataclass(frozen=True)
class SubscriptionConfig:
    id: str
    usage_providers: tuple[str, ...]
    display_name: str | None = None
    timezone: str | None = None
    quota_cost_basis: SubscriptionCostBasis = "virtual"
    fixed_cost_usd: float | None = None
    fixed_cost_period: SubscriptionFixedCostPeriod = "monthly"
    fixed_cost_reset_at: str | None = None
    fixed_cost_basis: SubscriptionCostBasis | None = None
    windows: tuple[SubscriptionWindowConfig, ...] = ()
    enabled: bool = True

    @property
    def label(self) -> str:
        return self.display_name or self.id


@dataclass(frozen=True)
class CostingConfig:
    config_version: int = CONFIG_VERSION
    default_actual_mode: ActualCostMode = "source"
    default_virtual_mode: VirtualCostMode = "pricing"
    missing_price: MissingPriceMode = "warn"
    price_profile: str | None = None
    actual_rules: tuple[ActualCostRule, ...] = ()
    virtual_prices: tuple[Price, ...] = ()
    actual_prices: tuple[Price, ...] = ()
    subscriptions: tuple[SubscriptionConfig, ...] = ()

    def resolve_actual_cost_mode(
        self,
        *,
        harness: str,
        provider: str | None,
        model: str | None,
    ) -> ActualCostMode:
        selected_mode = self.default_actual_mode
        best_specificity = (-1, -1, -1)
        for rule in self.actual_rules:
            if not rule.matches(harness=harness, provider=provider, model=model):
                continue
            specificity = rule.specificity()
            if specificity > best_specificity:
                selected_mode = rule.mode
                best_specificity = specificity
        return selected_mode


@dataclass(frozen=True)
class ImportConfig:
    harnesses: tuple[str, ...] = (
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
    sources: dict[str, Path | list[Path]] | None = None
    missing_source: ImportMissingSourceMode = "warn"
    include_raw_json: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", dict(self.sources or {}))


@dataclass(frozen=True)
class GitSyncConfig:
    repo: str | None = None
    remote: str = "origin"
    branch: str = "main"
    archive_dir: str = "archives"
    auto_pull: bool = True
    auto_push: bool = True
    redact_raw_json: bool = True
    include_config: bool = False
    remote_active: GitSyncRemoteActiveMode = "close-at-export"
    on_conflict: GitSyncConflictMode = "fail"
    track: tuple[GitSyncTrackedFile, ...] = ()


@dataclass(frozen=True)
class StatuslineCacheConfig:
    output_cache_secs: int = 2
    min_refresh_interval_secs: int = 5
    stale_after_secs: int = 60


@dataclass(frozen=True)
class StatuslineThresholdsConfig:
    quota_warning_percent: int = 80
    quota_danger_percent: int = 100
    burn_warning_percent: int = 80
    burn_danger_percent: int = 100
    context_warning_percent: int = 70
    context_danger_percent: int = 90


@dataclass(frozen=True)
class StatuslineConfig:
    default_harness: str = "auto"
    basis: SubscriptionCostBasis = "virtual"
    refresh: StatuslineRefreshMode = "auto"
    session: StatuslineSessionMode = "auto"
    max_width: int = 120
    show_emojis: bool = False
    color: StatuslineColorMode = "auto"
    empty: StatuslineEmptyMode = "silent"
    active_session_window_minutes: int = 30
    elements: tuple[str, ...] = _DEFAULT_STATUSLINE_ELEMENTS
    cache: StatuslineCacheConfig = field(default_factory=StatuslineCacheConfig)
    thresholds: StatuslineThresholdsConfig = field(
        default_factory=StatuslineThresholdsConfig
    )


@dataclass(frozen=True)
class ContextWindowConfig:
    provider: str
    model: str
    tokens: int


@dataclass(frozen=True)
class ToktrailConfig:
    costing: CostingConfig
    imports: ImportConfig
    statusline: StatuslineConfig = field(default_factory=StatuslineConfig)
    context_windows: tuple[ContextWindowConfig, ...] = ()


@dataclass(frozen=True)
class RuntimeConfig:
    config_version: int = CONFIG_VERSION
    imports: ImportConfig = field(default_factory=ImportConfig)
    sync_git: GitSyncConfig = field(default_factory=GitSyncConfig)
    default_actual_mode: ActualCostMode = "source"
    default_virtual_mode: VirtualCostMode = "pricing"
    missing_price: MissingPriceMode = "warn"
    price_profile: str | None = None
    actual_rules: tuple[ActualCostRule, ...] = ()
    statusline: StatuslineConfig = field(default_factory=StatuslineConfig)
    context_windows: tuple[ContextWindowConfig, ...] = ()


@dataclass(frozen=True)
class PricingConfig:
    config_version: int = CONFIG_VERSION
    virtual_prices: tuple[Price, ...] = ()
    actual_prices: tuple[Price, ...] = ()


@dataclass(frozen=True)
class SubscriptionsConfig:
    config_version: int = CONFIG_VERSION
    subscriptions: tuple[SubscriptionConfig, ...] = ()


@dataclass(frozen=True)
class LoadedRuntimeConfig:
    path: Path
    exists: bool
    config: RuntimeConfig


@dataclass(frozen=True)
class LoadedPricingConfig:
    manual_path: Path
    provider_dir: Path
    paths: tuple[Path, ...]
    manual_exists: bool
    provider_dir_exists: bool
    config: PricingConfig

    @property
    def path(self) -> Path:
        return self.manual_path

    @property
    def exists(self) -> bool:
        return self.manual_exists or bool(self.paths)


@dataclass(frozen=True)
class LoadedSubscriptionsConfig:
    path: Path
    exists: bool
    config: SubscriptionsConfig


@dataclass(frozen=True)
class LoadedToktrailConfig:
    config_path: Path
    prices_path: Path
    prices_dir: Path
    price_paths: tuple[Path, ...]
    subscriptions_path: Path
    config_exists: bool
    prices_exists: bool
    manual_prices_exists: bool
    provider_prices_exists: bool
    subscriptions_exists: bool
    runtime: RuntimeConfig
    config: ToktrailConfig


@dataclass(frozen=True)
class LoadedCostingConfig:
    config_path: Path
    prices_path: Path
    prices_dir: Path
    price_paths: tuple[Path, ...]
    subscriptions_path: Path
    config_exists: bool
    prices_exists: bool
    manual_prices_exists: bool
    provider_prices_exists: bool
    subscriptions_exists: bool
    config: CostingConfig

    @property
    def path(self) -> Path:
        return self.config_path

    @property
    def exists(self) -> bool:
        return self.config_exists


@dataclass(frozen=True)
class CostingConfigSummary:
    config_version: int
    default_actual_mode: ActualCostMode
    default_virtual_mode: VirtualCostMode
    missing_price: MissingPriceMode
    price_profile: str | None
    actual_rule_count: int
    actual_price_count: int
    virtual_price_count: int
    subscription_count: int


def default_costing_config() -> CostingConfig:
    return CostingConfig(
        actual_rules=(
            ActualCostRule(
                harness="opencode",
                provider=None,
                model=None,
                mode="source",
            ),
            ActualCostRule(harness="pi", provider=None, model=None, mode="zero"),
            ActualCostRule(
                harness="copilot",
                provider=None,
                model=None,
                mode="zero",
            ),
            ActualCostRule(
                harness="codex",
                provider=None,
                model=None,
                mode="zero",
            ),
            ActualCostRule(
                harness="code",
                provider=None,
                model=None,
                mode="zero",
            ),
            ActualCostRule(
                harness="goose",
                provider=None,
                model=None,
                mode="zero",
            ),
            ActualCostRule(
                harness="harnessbridge",
                provider=None,
                model=None,
                mode="source",
            ),
            ActualCostRule(
                harness="droid",
                provider=None,
                model=None,
                mode="zero",
            ),
            ActualCostRule(
                harness="amp",
                provider=None,
                model=None,
                mode="source",
            ),
            ActualCostRule(
                harness="claude",
                provider=None,
                model=None,
                mode="zero",
            ),
            ActualCostRule(
                harness="vibe",
                provider=None,
                model=None,
                mode="source",
            ),
        )
    )


def default_import_config() -> ImportConfig:
    return ImportConfig()


def default_runtime_config() -> RuntimeConfig:
    costing = default_costing_config()
    return RuntimeConfig(
        config_version=costing.config_version,
        imports=default_import_config(),
        sync_git=GitSyncConfig(),
        default_actual_mode=costing.default_actual_mode,
        default_virtual_mode=costing.default_virtual_mode,
        missing_price=costing.missing_price,
        price_profile=costing.price_profile,
        actual_rules=costing.actual_rules,
        statusline=StatuslineConfig(),
        context_windows=(),
    )


def default_pricing_config() -> PricingConfig:
    return PricingConfig()


def default_subscriptions_config() -> SubscriptionsConfig:
    return SubscriptionsConfig()


def default_toktrail_config() -> ToktrailConfig:
    return merge_configs(
        default_runtime_config(),
        default_pricing_config(),
        default_subscriptions_config(),
    )


def render_config_template(template: str = DEFAULT_TEMPLATE_NAME) -> str:
    if template == DEFAULT_TEMPLATE_NAME:
        return DEFAULT_CONFIG_TEXT
    if template == COPILOT_TEMPLATE_NAME:
        return COPILOT_CONFIG_TEXT
    msg = f"Unsupported config template: {template}"
    raise ValueError(msg)


def render_prices_template(template: str = DEFAULT_TEMPLATE_NAME) -> str:
    if template == DEFAULT_TEMPLATE_NAME:
        return DEFAULT_PRICES_TEXT
    if template == COPILOT_TEMPLATE_NAME:
        marker = "# OpenAI"
        marker_index = COPILOT_TEMPLATE_TEXT.find(marker)
        pricing_body = (
            COPILOT_TEMPLATE_TEXT[marker_index:].strip() if marker_index >= 0 else ""
        )
        if pricing_body:
            return f"config_version = 1\n\n{pricing_body}\n"
        return DEFAULT_PRICES_TEXT
    msg = f"Unsupported prices template: {template}"
    raise ValueError(msg)


def render_subscriptions_template(template: str = DEFAULT_TEMPLATE_NAME) -> str:
    if template in {DEFAULT_TEMPLATE_NAME, COPILOT_TEMPLATE_NAME}:
        return DEFAULT_SUBSCRIPTIONS_TEXT
    msg = f"Unsupported subscriptions template: {template}"
    raise ValueError(msg)


def load_costing_config(path: Path) -> CostingConfig:
    if path.name == "config.toml":
        try:
            return load_toktrail_config(path).costing
        except ValueError:
            data = _load_optional_toml(path, context="toktrail config")
            if data is None:
                return default_costing_config()
            if "pricing" in data or "subscriptions" in data:
                return _parse_legacy_costing_config(data)
            raise
    data = _load_optional_toml(path, context="toktrail config")
    if data is None:
        return default_costing_config()
    return _parse_legacy_costing_config(data)


def load_toktrail_config(path: Path) -> ToktrailConfig:
    return merge_configs(
        load_runtime_config(path),
        default_pricing_config(),
        default_subscriptions_config(),
    )


def load_resolved_costing_config(
    config_cli_value: Path | None = None,
    prices_cli_value: Path | None = None,
    prices_dir_cli_value: Path | None = None,
    subscriptions_cli_value: Path | None = None,
) -> LoadedCostingConfig:
    loaded = load_resolved_toktrail_config(
        config_cli_value=config_cli_value,
        prices_cli_value=prices_cli_value,
        prices_dir_cli_value=prices_dir_cli_value,
        subscriptions_cli_value=subscriptions_cli_value,
    )
    return LoadedCostingConfig(
        config_path=loaded.config_path,
        prices_path=loaded.prices_path,
        prices_dir=loaded.prices_dir,
        price_paths=loaded.price_paths,
        subscriptions_path=loaded.subscriptions_path,
        config_exists=loaded.config_exists,
        prices_exists=loaded.prices_exists,
        manual_prices_exists=loaded.manual_prices_exists,
        provider_prices_exists=loaded.provider_prices_exists,
        subscriptions_exists=loaded.subscriptions_exists,
        config=loaded.config.costing,
    )


def _git_sync_repo_path(sync_git: GitSyncConfig) -> Path | None:
    if not sync_git.repo:
        return None
    return Path(sync_git.repo).expanduser()


def _resolve_effective_prices_path(
    *,
    config_path: Path,
    config_cli_value: Path | None,
    cli_value: Path | None,
    repo_path: Path | None,
    tracked: set[GitSyncTrackedFile],
) -> Path:
    if cli_value is not None:
        return resolve_toktrail_prices_path(cli_value)
    if toktrail_prices_env_is_set():
        return resolve_toktrail_prices_path(None)
    if repo_path is not None and "prices" in tracked:
        return repo_path / _GIT_CONFIG_DIR / _GIT_PRICES_FILE
    if config_cli_value is not None:
        return config_path.with_name("prices.toml")
    return resolve_toktrail_prices_path(None)


def _resolve_effective_prices_dir(
    *,
    config_path: Path,
    config_cli_value: Path | None,
    cli_value: Path | None,
    repo_path: Path | None,
    tracked: set[GitSyncTrackedFile],
) -> Path:
    if cli_value is not None:
        return resolve_toktrail_prices_dir(cli_value)
    if toktrail_prices_dir_env_is_set():
        return resolve_toktrail_prices_dir(None)
    if repo_path is not None and "provider-prices" in tracked:
        return repo_path / _GIT_CONFIG_DIR / _GIT_PRICES_DIR
    if config_cli_value is not None:
        return config_path.with_name("prices")
    return resolve_toktrail_prices_dir(None)


def _resolve_effective_subscriptions_path(
    *,
    config_path: Path,
    config_cli_value: Path | None,
    cli_value: Path | None,
    repo_path: Path | None,
    tracked: set[GitSyncTrackedFile],
) -> Path:
    if cli_value is not None:
        return resolve_toktrail_subscriptions_path(cli_value)
    if toktrail_subscriptions_env_is_set():
        return resolve_toktrail_subscriptions_path(None)
    if repo_path is not None and "subscriptions" in tracked:
        return repo_path / _GIT_CONFIG_DIR / _GIT_SUBSCRIPTIONS_FILE
    if config_cli_value is not None:
        return config_path.with_name("subscriptions.toml")
    return resolve_toktrail_subscriptions_path(None)


def load_resolved_toktrail_config(
    config_cli_value: Path | None = None,
    prices_cli_value: Path | None = None,
    prices_dir_cli_value: Path | None = None,
    subscriptions_cli_value: Path | None = None,
) -> LoadedToktrailConfig:
    config_path = resolve_toktrail_config_path(config_cli_value)
    legacy_data = _load_optional_toml(config_path, context="toktrail config")
    use_legacy_monolithic = (
        config_path.name != "config.toml"
        and prices_cli_value is None
        and prices_dir_cli_value is None
        and subscriptions_cli_value is None
        and legacy_data is not None
        and ("pricing" in legacy_data or "subscriptions" in legacy_data)
    )
    if use_legacy_monolithic:
        assert legacy_data is not None
        runtime_defaults = default_runtime_config()
        imports = _parse_import_config(
            legacy_data.get("imports"),
            runtime_defaults.imports,
        )
        runtime_config = RuntimeConfig(imports=imports)
        prices_path = (
            config_path.with_name("prices.toml")
            if config_cli_value is not None and prices_cli_value is None
            else resolve_toktrail_prices_path(prices_cli_value)
        )
        prices_dir = (
            config_path.with_name("prices")
            if config_cli_value is not None and prices_dir_cli_value is None
            else resolve_toktrail_prices_dir(prices_dir_cli_value)
        )
        subscriptions_path = (
            config_path.with_name("subscriptions.toml")
            if config_cli_value is not None and subscriptions_cli_value is None
            else resolve_toktrail_subscriptions_path(subscriptions_cli_value)
        )
        provider_paths = (
            tuple(sorted(prices_dir.glob("*.toml"))) if prices_dir.is_dir() else ()
        )
        manual_prices_exists = prices_path.exists()
        price_paths: tuple[Path, ...] = provider_paths + (
            (prices_path,) if manual_prices_exists else ()
        )
        return LoadedToktrailConfig(
            config_path=config_path,
            prices_path=prices_path,
            prices_dir=prices_dir,
            price_paths=price_paths,
            subscriptions_path=subscriptions_path,
            config_exists=config_path.exists(),
            prices_exists=manual_prices_exists or bool(price_paths),
            manual_prices_exists=manual_prices_exists,
            provider_prices_exists=prices_dir.exists(),
            subscriptions_exists=subscriptions_path.exists(),
            runtime=runtime_config,
            config=ToktrailConfig(
                costing=_parse_legacy_costing_config(legacy_data),
                imports=imports,
            ),
        )
    runtime_config = load_runtime_config(config_path)
    repo_path = _git_sync_repo_path(runtime_config.sync_git)
    tracked = set(runtime_config.sync_git.track)
    prices_path = _resolve_effective_prices_path(
        config_path=config_path,
        config_cli_value=config_cli_value,
        cli_value=prices_cli_value,
        repo_path=repo_path,
        tracked=tracked,
    )
    prices_dir = _resolve_effective_prices_dir(
        config_path=config_path,
        config_cli_value=config_cli_value,
        cli_value=prices_dir_cli_value,
        repo_path=repo_path,
        tracked=tracked,
    )
    subscriptions_path = _resolve_effective_subscriptions_path(
        config_path=config_path,
        config_cli_value=config_cli_value,
        cli_value=subscriptions_cli_value,
        repo_path=repo_path,
        tracked=tracked,
    )
    loaded_pricing = load_pricing_configs(
        manual_path=prices_path,
        provider_dir=prices_dir,
    )
    return LoadedToktrailConfig(
        config_path=config_path,
        prices_path=prices_path,
        prices_dir=prices_dir,
        price_paths=loaded_pricing.paths,
        subscriptions_path=subscriptions_path,
        config_exists=config_path.exists(),
        prices_exists=loaded_pricing.exists,
        manual_prices_exists=loaded_pricing.manual_exists,
        provider_prices_exists=loaded_pricing.provider_dir_exists,
        subscriptions_exists=subscriptions_path.exists(),
        runtime=runtime_config,
        config=merge_configs(
            runtime_config,
            loaded_pricing.config,
            load_subscriptions_config(subscriptions_path),
        ),
    )


def parse_costing_config(data: object) -> CostingConfig:
    return merge_configs(
        parse_runtime_config(data),
        default_pricing_config(),
        default_subscriptions_config(),
    ).costing


def parse_toktrail_config(
    runtime_data: object,
    pricing_data: object | None = None,
    subscriptions_data: object | None = None,
) -> ToktrailConfig:
    return merge_configs(
        parse_runtime_config(runtime_data),
        parse_pricing_config(pricing_data or {}),
        parse_subscriptions_config(subscriptions_data or {}),
    )


def load_runtime_config(path: Path) -> RuntimeConfig:
    data = _load_optional_toml(path, context="config.toml")
    if data is None:
        return default_runtime_config()
    return parse_runtime_config(data)


def load_pricing_config(path: Path) -> PricingConfig:
    data = _load_optional_toml(path, context="prices.toml")
    if data is None:
        return default_pricing_config()
    return parse_pricing_config(data)


def load_pricing_configs(
    *,
    manual_path: Path,
    provider_dir: Path,
) -> LoadedPricingConfig:
    provider_paths = (
        tuple(sorted(provider_dir.glob("*.toml"))) if provider_dir.is_dir() else ()
    )
    sources: list[tuple[Path, PricingConfig, Literal["provider", "manual"]]] = []
    for provider_path in provider_paths:
        sources.append((provider_path, load_pricing_config(provider_path), "provider"))
    manual_exists = manual_path.exists()
    if manual_exists:
        sources.append((manual_path, load_pricing_config(manual_path), "manual"))
    return LoadedPricingConfig(
        manual_path=manual_path,
        provider_dir=provider_dir,
        paths=tuple(path for path, _config, _kind in sources),
        manual_exists=manual_exists,
        provider_dir_exists=provider_dir.exists(),
        config=merge_pricing_configs(sources),
    )


def load_subscriptions_config(path: Path) -> SubscriptionsConfig:
    data = _load_optional_toml(path, context="subscriptions.toml")
    if data is None:
        return default_subscriptions_config()
    return parse_subscriptions_config(data)


def merge_pricing_configs(
    sources: list[tuple[Path, PricingConfig, Literal["provider", "manual"]]],
) -> PricingConfig:
    virtual_prices = _merge_price_table(sources, table="virtual")
    actual_prices = _merge_price_table(sources, table="actual")
    _validate_aliases_across_prices(virtual_prices, context="pricing.virtual")
    _validate_aliases_across_prices(actual_prices, context="pricing.actual")
    _validate_price_context_ranges(virtual_prices, context="pricing.virtual")
    _validate_price_context_ranges(actual_prices, context="pricing.actual")
    return PricingConfig(
        config_version=CONFIG_VERSION,
        virtual_prices=virtual_prices,
        actual_prices=actual_prices,
    )


def _price_variant_key(price: Price) -> tuple[str, str, int | None, int | None, str]:
    return (
        normalize_identity(price.provider),
        normalize_identity(price.model),
        price.context_min_tokens,
        price.context_max_tokens,
        price.context_basis,
    )


def _merge_price_table(
    sources: list[tuple[Path, PricingConfig, Literal["provider", "manual"]]],
    *,
    table: Literal["virtual", "actual"],
) -> tuple[Price, ...]:
    merged: dict[
        tuple[str, str, int | None, int | None, str],
        tuple[Price, Path, Literal["provider", "manual"]],
    ] = {}
    for path, config, kind in sources:
        table_prices = (
            config.virtual_prices if table == "virtual" else config.actual_prices
        )
        for price in table_prices:
            key = _price_variant_key(price)
            existing = merged.get(key)
            if existing is None:
                merged[key] = (price, path, kind)
                continue
            existing_price, existing_path, existing_kind = existing
            if kind == "manual":
                merged[key] = (price, path, kind)
                continue
            if existing_kind == "manual":
                continue
            if existing_price == price:
                continue
            msg = (
                f"pricing.{table} duplicate generated price for {key[0]}/{key[1]} in "
                f"{existing_path} and {path}; keep only one provider file or move the "
                "override to prices.toml"
            )
            raise ValueError(msg)
    merged_prices = [entry[0] for entry in merged.values()]
    merged_prices.sort(
        key=lambda price: (
            normalize_identity(price.provider),
            normalize_identity(price.model),
            price.context_min_tokens if price.context_min_tokens is not None else 0,
            price.context_max_tokens
            if price.context_max_tokens is not None
            else 2**63 - 1,
            price.context_basis,
        )
    )
    return tuple(merged_prices)


def _validate_aliases_across_prices(
    prices: tuple[Price, ...],
    *,
    context: str,
) -> None:
    lookup_keys: dict[tuple[str, str], tuple[str, str]] = {}
    for price in prices:
        canonical_key = (
            normalize_identity(price.provider),
            normalize_identity(price.model),
        )
        for lookup in (price.model, *price.aliases):
            lookup_key = (
                normalize_identity(price.provider),
                normalize_identity(lookup),
            )
            previous = lookup_keys.get(lookup_key)
            if previous is not None and previous != canonical_key:
                msg = (
                    f"{context} reuses alias {lookup!r} for "
                    f"{price.provider}/{price.model}."
                )
                raise ValueError(msg)
            lookup_keys[lookup_key] = canonical_key


def _validate_price_context_ranges(prices: tuple[Price, ...], *, context: str) -> None:
    grouped: dict[tuple[str, str, str], list[Price]] = {}
    for price in prices:
        grouped.setdefault(
            (
                normalize_identity(price.provider),
                normalize_identity(price.model),
                price.context_basis,
            ),
            [],
        ).append(price)

    for provider, model, basis in sorted(grouped):
        rows = grouped[(provider, model, basis)]
        tiered_rows = [
            row
            for row in rows
            if row.context_min_tokens is not None or row.context_max_tokens is not None
        ]
        tiered_rows.sort(
            key=lambda row: (
                row.context_min_tokens if row.context_min_tokens is not None else 0,
                row.context_max_tokens
                if row.context_max_tokens is not None
                else 2**63 - 1,
            )
        )
        previous: Price | None = None
        for row in tiered_rows:
            if previous is None:
                previous = row
                continue
            if not _context_ranges_overlap(previous, row):
                previous = row
                continue
            if previous == row:
                continue
            prev_min = previous.context_min_tokens if previous.context_min_tokens else 0
            prev_max = (
                previous.context_max_tokens if previous.context_max_tokens else "∞"
            )
            msg = (
                f"{context} context range overlaps prior "
                f"{provider}/{model}/{basis} range {prev_min}..{prev_max}."
            )
            raise ValueError(msg)


def _context_ranges_overlap(left: Price, right: Price) -> bool:
    left_min = left.context_min_tokens if left.context_min_tokens is not None else 0
    left_max = (
        left.context_max_tokens if left.context_max_tokens is not None else 2**63 - 1
    )
    right_min = right.context_min_tokens if right.context_min_tokens is not None else 0
    right_max = (
        right.context_max_tokens if right.context_max_tokens is not None else 2**63 - 1
    )
    return left_min <= right_max and right_min <= left_max


def parse_runtime_config(data: object) -> RuntimeConfig:
    if not isinstance(data, dict):
        msg = "Toktrail config must be a TOML table."
        raise ValueError(msg)

    _reject_misplaced_keys(
        data,
        invalid={"pricing"},
        context="config.toml",
        destination_hint="Move token prices to ~/.config/toktrail/prices.toml.",
    )
    _reject_misplaced_keys(
        data,
        invalid={"subscriptions"},
        context="config.toml",
        destination_hint=(
            "Move subscription plans to ~/.config/toktrail/subscriptions.toml."
        ),
    )
    _validate_allowed_keys(data, _RUNTIME_CONFIG_ROOT_FIELDS, context="config.toml")

    default_config = default_runtime_config()
    config_version = _parse_config_version(data.get("config_version", CONFIG_VERSION))
    costing_table = _parse_optional_table(data.get("costing"), context="costing")
    _validate_allowed_keys(costing_table, _COSTING_FIELDS, context="costing")
    default_actual_mode = _parse_choice(
        costing_table.get("default_actual_mode", default_config.default_actual_mode),
        valid=_VALID_ACTUAL_COST_MODES,
        context="costing.default_actual_mode",
    )
    default_virtual_mode = _parse_choice(
        costing_table.get(
            "default_virtual_mode",
            default_config.default_virtual_mode,
        ),
        valid=_VALID_VIRTUAL_COST_MODES,
        context="costing.default_virtual_mode",
    )
    missing_price = _parse_choice(
        costing_table.get("missing_price", default_config.missing_price),
        valid=_VALID_MISSING_PRICE_MODES,
        context="costing.missing_price",
    )
    price_profile = _parse_optional_string(
        costing_table.get("price_profile"),
        context="costing.price_profile",
    )
    actual_rules = _parse_actual_cost_rules(
        data.get("actual_cost"),
        default_config.actual_rules,
    )

    return RuntimeConfig(
        config_version=config_version,
        imports=_parse_import_config(data.get("imports"), default_config.imports),
        sync_git=_parse_sync_config(data.get("sync"), default_config.sync_git),
        default_actual_mode=cast(ActualCostMode, default_actual_mode),
        default_virtual_mode=cast(VirtualCostMode, default_virtual_mode),
        missing_price=cast(MissingPriceMode, missing_price),
        price_profile=price_profile,
        actual_rules=actual_rules,
        statusline=_parse_statusline_config(
            data.get("statusline"),
            default_config.statusline,
        ),
        context_windows=_parse_context_windows(data.get("context_window")),
    )


def parse_pricing_config(data: object) -> PricingConfig:
    if not isinstance(data, dict):
        msg = "prices.toml must be a TOML table."
        raise ValueError(msg)

    _reject_misplaced_keys(
        data,
        invalid={"imports"},
        context="prices.toml",
        destination_hint="Import settings belong in ~/.config/toktrail/config.toml.",
    )
    _reject_misplaced_keys(
        data,
        invalid={"actual_cost", "costing"},
        context="prices.toml",
        destination_hint="Costing policy belongs in ~/.config/toktrail/config.toml.",
    )
    _reject_misplaced_keys(
        data,
        invalid={"subscriptions"},
        context="prices.toml",
        destination_hint=(
            "Subscription plans belong in ~/.config/toktrail/subscriptions.toml."
        ),
    )
    _validate_allowed_keys(data, _PRICE_CONFIG_ROOT_FIELDS, context="prices.toml")

    config_version = _parse_config_version(data.get("config_version", CONFIG_VERSION))
    pricing_table = _parse_optional_table(data.get("pricing"), context="pricing")
    _validate_allowed_keys(pricing_table, _PRICING_FIELDS, context="pricing")
    return PricingConfig(
        config_version=config_version,
        virtual_prices=_parse_prices(
            pricing_table.get("virtual"),
            context="pricing.virtual",
        ),
        actual_prices=_parse_prices(
            pricing_table.get("actual"),
            context="pricing.actual",
        ),
    )


def parse_subscriptions_config(data: object) -> SubscriptionsConfig:
    if not isinstance(data, dict):
        msg = "subscriptions.toml must be a TOML table."
        raise ValueError(msg)

    _reject_misplaced_keys(
        data,
        invalid={"imports", "actual_cost", "costing"},
        context="subscriptions.toml",
        destination_hint=(
            "Import and costing settings belong in ~/.config/toktrail/config.toml."
        ),
    )
    _reject_misplaced_keys(
        data,
        invalid={"pricing"},
        context="subscriptions.toml",
        destination_hint="Token prices belong in ~/.config/toktrail/prices.toml.",
    )
    _validate_allowed_keys(
        data,
        _SUBSCRIPTION_CONFIG_ROOT_FIELDS,
        context="subscriptions.toml",
    )

    config_version = _parse_config_version(data.get("config_version", CONFIG_VERSION))
    return SubscriptionsConfig(
        config_version=config_version,
        subscriptions=_parse_subscriptions(data.get("subscriptions")),
    )


def merge_configs(
    runtime: RuntimeConfig,
    pricing: PricingConfig,
    subscriptions: SubscriptionsConfig,
) -> ToktrailConfig:
    return ToktrailConfig(
        costing=CostingConfig(
            config_version=runtime.config_version,
            default_actual_mode=runtime.default_actual_mode,
            default_virtual_mode=runtime.default_virtual_mode,
            missing_price=runtime.missing_price,
            price_profile=runtime.price_profile,
            actual_rules=runtime.actual_rules,
            virtual_prices=pricing.virtual_prices,
            actual_prices=pricing.actual_prices,
            subscriptions=subscriptions.subscriptions,
        ),
        imports=runtime.imports,
        statusline=runtime.statusline,
        context_windows=runtime.context_windows,
    )


def _load_optional_toml(path: Path, *, context: str) -> dict[str, object] | None:
    if not path.exists():
        return None
    if not path.is_file():
        msg = f"{context} path is not a file: {path}"
        raise ValueError(msg)
    try:
        with path.open("rb") as handle:
            raw_data: object = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        msg = f"Invalid TOML in {context} {path}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(raw_data, dict):
        msg = f"{context} must contain a TOML table at the root."
        raise ValueError(msg)
    return cast(dict[str, object], raw_data)


def _parse_legacy_costing_config(data: dict[str, object]) -> CostingConfig:
    _validate_allowed_keys(data, _ROOT_FIELDS, context="root")
    default_runtime = default_runtime_config()
    config_version = _parse_config_version(data.get("config_version", CONFIG_VERSION))
    costing_table = _parse_optional_table(data.get("costing"), context="costing")
    _validate_allowed_keys(costing_table, _COSTING_FIELDS, context="costing")
    default_actual_mode = _parse_choice(
        costing_table.get("default_actual_mode", default_runtime.default_actual_mode),
        valid=_VALID_ACTUAL_COST_MODES,
        context="costing.default_actual_mode",
    )
    default_virtual_mode = _parse_choice(
        costing_table.get(
            "default_virtual_mode",
            default_runtime.default_virtual_mode,
        ),
        valid=_VALID_VIRTUAL_COST_MODES,
        context="costing.default_virtual_mode",
    )
    missing_price = _parse_choice(
        costing_table.get("missing_price", default_runtime.missing_price),
        valid=_VALID_MISSING_PRICE_MODES,
        context="costing.missing_price",
    )
    price_profile = _parse_optional_string(
        costing_table.get("price_profile"),
        context="costing.price_profile",
    )
    actual_rules = _parse_actual_cost_rules(
        data.get("actual_cost"),
        default_runtime.actual_rules,
    )
    pricing_table = _parse_optional_table(data.get("pricing"), context="pricing")
    _validate_allowed_keys(pricing_table, _PRICING_FIELDS, context="pricing")
    virtual_prices = _parse_prices(
        pricing_table.get("virtual"),
        context="pricing.virtual",
    )
    actual_prices = _parse_prices(
        pricing_table.get("actual"),
        context="pricing.actual",
    )
    subscriptions = _parse_subscriptions(data.get("subscriptions"))
    return CostingConfig(
        config_version=config_version,
        default_actual_mode=cast(ActualCostMode, default_actual_mode),
        default_virtual_mode=cast(VirtualCostMode, default_virtual_mode),
        missing_price=cast(MissingPriceMode, missing_price),
        price_profile=price_profile,
        actual_rules=actual_rules,
        virtual_prices=virtual_prices,
        actual_prices=actual_prices,
        subscriptions=subscriptions,
    )


def summarize_costing_config(config: CostingConfig) -> CostingConfigSummary:
    return CostingConfigSummary(
        config_version=config.config_version,
        default_actual_mode=config.default_actual_mode,
        default_virtual_mode=config.default_virtual_mode,
        missing_price=config.missing_price,
        price_profile=config.price_profile,
        actual_rule_count=len(config.actual_rules),
        actual_price_count=len(config.actual_prices),
        virtual_price_count=len(config.virtual_prices),
        subscription_count=len(config.subscriptions),
    )


def _parse_subscriptions(value: object) -> tuple[SubscriptionConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = "subscriptions must be an array of tables."
        raise ValueError(msg)

    subscriptions: list[SubscriptionConfig] = []
    enabled_subscription_ids: set[str] = set()
    enabled_usage_provider_owner: dict[str, str] = {}
    for index, raw_subscription in enumerate(value, start=1):
        if not isinstance(raw_subscription, dict):
            msg = f"subscriptions[{index}] must be a TOML table."
            raise ValueError(msg)
        _validate_allowed_keys(
            raw_subscription,
            _SUBSCRIPTION_FIELDS,
            context=f"subscriptions[{index}]",
        )
        subscription_id_text = _parse_required_identity(
            raw_subscription.get("id"),
            context=f"subscriptions[{index}].id",
        )
        subscription_id = normalize_identity(subscription_id_text)
        usage_providers = _parse_required_identity_list(
            raw_subscription.get("usage_providers"),
            context=f"subscriptions[{index}].usage_providers",
        )
        display_name = _parse_optional_string(
            raw_subscription.get("display_name"),
            context=f"subscriptions[{index}].display_name",
        )
        timezone_name = _parse_optional_string(
            raw_subscription.get("timezone"),
            context=f"subscriptions[{index}].timezone",
        )
        quota_cost_basis = cast(
            SubscriptionCostBasis,
            _parse_choice(
                raw_subscription.get("quota_cost_basis", "virtual"),
                valid=_VALID_SUBSCRIPTION_COST_BASES,
                context=f"subscriptions[{index}].quota_cost_basis",
            ),
        )
        fixed_cost_usd = _parse_non_negative_float(
            raw_subscription.get("fixed_cost_usd"),
            context=f"subscriptions[{index}].fixed_cost_usd",
            required=False,
        )
        fixed_cost_period = cast(
            SubscriptionFixedCostPeriod,
            _parse_choice(
                raw_subscription.get("fixed_cost_period", "monthly"),
                valid=_VALID_SUBSCRIPTION_FIXED_COST_PERIODS,
                context=f"subscriptions[{index}].fixed_cost_period",
            ),
        )
        fixed_cost_reset_at = _parse_optional_string(
            raw_subscription.get("fixed_cost_reset_at"),
            context=f"subscriptions[{index}].fixed_cost_reset_at",
        )
        fixed_cost_basis = cast(
            SubscriptionCostBasis | None,
            (
                _parse_choice(
                    raw_subscription.get("fixed_cost_basis"),
                    valid=_VALID_SUBSCRIPTION_COST_BASES,
                    context=f"subscriptions[{index}].fixed_cost_basis",
                )
                if raw_subscription.get("fixed_cost_basis") is not None
                else None
            ),
        )
        windows = _parse_subscription_windows(
            raw_subscription.get("windows"),
            context=f"subscriptions[{index}].windows",
            timezone_name=timezone_name,
        )
        if fixed_cost_reset_at is None:
            fixed_cost_reset_at = _subscription_window_reset_at(
                windows,
                period=fixed_cost_period,
            )
        if fixed_cost_reset_at is not None:
            _validate_subscription_reset_at(
                reset_at=fixed_cost_reset_at,
                timezone_name=timezone_name,
                context=f"subscriptions[{index}].fixed_cost_reset_at",
            )
        if fixed_cost_usd is not None and fixed_cost_usd > 0:
            if fixed_cost_reset_at is None:
                msg = (
                    f"subscriptions[{index}].fixed_cost_reset_at is required when "
                    "fixed_cost_usd is set and no matching "
                    "fixed_cost_period window exists."
                )
                raise ValueError(msg)
        enabled = _parse_bool(
            raw_subscription.get("enabled", True),
            context=f"subscriptions[{index}].enabled",
        )

        if enabled and subscription_id in enabled_subscription_ids:
            msg = (
                f"subscriptions[{index}].id duplicates enabled subscription id "
                f"{subscription_id!r}."
            )
            raise ValueError(msg)
        if enabled:
            enabled_subscription_ids.add(subscription_id)
            for usage_provider in usage_providers:
                existing_owner = enabled_usage_provider_owner.get(usage_provider)
                if existing_owner is not None and existing_owner != subscription_id:
                    msg = (
                        f"subscriptions[{index}].usage_providers overlaps enabled "
                        f"usage provider {usage_provider!r} already owned by "
                        f"subscription {existing_owner!r}."
                    )
                    raise ValueError(msg)
                enabled_usage_provider_owner[usage_provider] = subscription_id

        subscriptions.append(
            SubscriptionConfig(
                id=subscription_id,
                usage_providers=usage_providers,
                display_name=display_name,
                timezone=timezone_name,
                quota_cost_basis=quota_cost_basis,
                fixed_cost_usd=fixed_cost_usd,
                fixed_cost_period=fixed_cost_period,
                fixed_cost_reset_at=fixed_cost_reset_at,
                fixed_cost_basis=fixed_cost_basis,
                windows=windows,
                enabled=enabled,
            )
        )

    return tuple(subscriptions)


def normalize_identity(value: str) -> str:
    normalized = value.strip().lower()
    normalized = _SEPARATOR_RE.sub("-", normalized)
    normalized = _INVALID_IDENTITY_CHARS_RE.sub("", normalized)
    normalized = _DASH_RE.sub("-", normalized).strip("-")
    if not normalized:
        msg = "Identity value is empty after normalization."
        raise ValueError(msg)
    return normalized


def _parse_config_version(value: object) -> int:
    if not isinstance(value, int):
        msg = "config_version must be an integer."
        raise ValueError(msg)
    if value != CONFIG_VERSION:
        msg = f"Unsupported config_version: {value}"
        raise ValueError(msg)
    return value


def _parse_optional_table(
    value: object,
    *,
    context: str,
) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = f"{context} must be a TOML table."
        raise ValueError(msg)
    return value


def _parse_import_config(
    value: object,
    default_config: ImportConfig,
) -> ImportConfig:
    imports_table = _parse_optional_table(value, context="imports")
    _validate_allowed_keys(imports_table, _IMPORT_FIELDS, context="imports")
    return ImportConfig(
        harnesses=_parse_import_harnesses(
            imports_table.get("harnesses", list(default_config.harnesses)),
            context="imports.harnesses",
        ),
        sources=_parse_import_sources(
            imports_table.get("sources"),
            context="imports.sources",
        ),
        missing_source=cast(
            ImportMissingSourceMode,
            _parse_choice(
                imports_table.get("missing_source", default_config.missing_source),
                valid=_VALID_IMPORT_MISSING_SOURCE_MODES,
                context="imports.missing_source",
            ),
        ),
        include_raw_json=_parse_bool(
            imports_table.get("include_raw_json", default_config.include_raw_json),
            context="imports.include_raw_json",
        ),
    )


def _parse_sync_config(value: object, default_config: GitSyncConfig) -> GitSyncConfig:
    sync_table = _parse_optional_table(value, context="sync")
    _validate_allowed_keys(sync_table, _SYNC_FIELDS, context="sync")
    git_table = _parse_optional_table(sync_table.get("git"), context="sync.git")
    _validate_allowed_keys(git_table, _SYNC_GIT_FIELDS, context="sync.git")

    auto_pull_configured = "auto_pull" in git_table
    auto_import_configured = "auto_import" in git_table
    auto_push_configured = "auto_push" in git_table
    auto_export_configured = "auto_export" in git_table

    auto_pull = _parse_bool(
        git_table.get("auto_pull", default_config.auto_pull),
        context="sync.git.auto_pull",
    )
    auto_import = _parse_bool(
        git_table.get("auto_import", auto_pull),
        context="sync.git.auto_import",
    )
    if auto_pull_configured and auto_import_configured and auto_pull != auto_import:
        msg = (
            "sync.git.auto_pull and sync.git.auto_import conflict; "
            "set only one or use the same value."
        )
        raise ValueError(msg)

    auto_push = _parse_bool(
        git_table.get("auto_push", default_config.auto_push),
        context="sync.git.auto_push",
    )
    auto_export = _parse_bool(
        git_table.get("auto_export", auto_push),
        context="sync.git.auto_export",
    )
    if auto_push_configured and auto_export_configured and auto_push != auto_export:
        msg = (
            "sync.git.auto_push and sync.git.auto_export conflict; "
            "set only one or use the same value."
        )
        raise ValueError(msg)

    return GitSyncConfig(
        repo=_parse_optional_string(git_table.get("repo"), context="sync.git.repo"),
        remote=_parse_string(
            git_table.get("remote", default_config.remote),
            context="sync.git.remote",
        ),
        branch=_parse_string(
            git_table.get("branch", default_config.branch),
            context="sync.git.branch",
        ),
        archive_dir=_parse_string(
            git_table.get("archive_dir", default_config.archive_dir),
            context="sync.git.archive_dir",
        ),
        auto_pull=auto_import,
        auto_push=auto_export,
        redact_raw_json=_parse_bool(
            git_table.get("redact_raw_json", default_config.redact_raw_json),
            context="sync.git.redact_raw_json",
        ),
        include_config=_parse_bool(
            git_table.get("include_config", default_config.include_config),
            context="sync.git.include_config",
        ),
        remote_active=cast(
            GitSyncRemoteActiveMode,
            _parse_choice(
                git_table.get("remote_active", default_config.remote_active),
                valid=_VALID_GIT_SYNC_REMOTE_ACTIVE_MODES,
                context="sync.git.remote_active",
            ),
        ),
        on_conflict=cast(
            GitSyncConflictMode,
            _parse_choice(
                git_table.get("on_conflict", default_config.on_conflict),
                valid=_VALID_GIT_SYNC_CONFLICT_MODES,
                context="sync.git.on_conflict",
            ),
        ),
        track=_parse_sync_git_track(
            git_table.get("track"),
            context="sync.git.track",
        ),
    )


def _parse_sync_git_track(
    value: object,
    *,
    context: str,
) -> tuple[GitSyncTrackedFile, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = f"{context} must be a list."
        raise ValueError(msg)

    result: list[GitSyncTrackedFile] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        text = _parse_string(item, context=f"{context}[{index}]").strip().lower()
        values = _ALL_GIT_SYNC_TRACKED_FILES if text == "all" else (text,)
        for value_text in values:
            if value_text not in _VALID_GIT_SYNC_TRACKED_FILES:
                allowed = ", ".join(sorted(_VALID_GIT_SYNC_TRACKED_FILES | {"all"}))
                msg = f"{context}[{index}] must be one of: {allowed}."
                raise ValueError(msg)
            if value_text in seen:
                continue
            seen.add(value_text)
            result.append(cast(GitSyncTrackedFile, value_text))
    return tuple(result)


def _parse_statusline_config(
    value: object,
    default_config: StatuslineConfig,
) -> StatuslineConfig:
    table = _parse_optional_table(value, context="statusline")
    _validate_allowed_keys(table, _STATUSLINE_FIELDS, context="statusline")
    return StatuslineConfig(
        default_harness=_parse_statusline_default_harness(
            table.get("default_harness", default_config.default_harness),
            context="statusline.default_harness",
        ),
        basis=cast(
            SubscriptionCostBasis,
            _parse_choice(
                table.get("basis", default_config.basis),
                valid=_VALID_SUBSCRIPTION_COST_BASES,
                context="statusline.basis",
            ),
        ),
        refresh=cast(
            StatuslineRefreshMode,
            _parse_choice(
                table.get("refresh", default_config.refresh),
                valid=_VALID_STATUSLINE_REFRESH_MODES,
                context="statusline.refresh",
            ),
        ),
        session=cast(
            StatuslineSessionMode,
            _parse_choice(
                table.get("session", default_config.session),
                valid=_VALID_STATUSLINE_SESSION_MODES,
                context="statusline.session",
            ),
        ),
        max_width=_parse_positive_int(
            table.get("max_width", default_config.max_width),
            context="statusline.max_width",
        ),
        show_emojis=_parse_bool(
            table.get("show_emojis", default_config.show_emojis),
            context="statusline.show_emojis",
        ),
        color=cast(
            StatuslineColorMode,
            _parse_choice(
                table.get("color", default_config.color),
                valid=_VALID_STATUSLINE_COLOR_MODES,
                context="statusline.color",
            ),
        ),
        empty=cast(
            StatuslineEmptyMode,
            _parse_choice(
                table.get("empty", default_config.empty),
                valid=_VALID_STATUSLINE_EMPTY_MODES,
                context="statusline.empty",
            ),
        ),
        active_session_window_minutes=_parse_positive_int(
            table.get(
                "active_session_window_minutes",
                default_config.active_session_window_minutes,
            ),
            context="statusline.active_session_window_minutes",
        ),
        elements=_parse_statusline_elements(
            table.get("elements", list(default_config.elements)),
            context="statusline.elements",
        ),
        cache=_parse_statusline_cache_config(
            table.get("cache"),
            default_config.cache,
        ),
        thresholds=_parse_statusline_thresholds_config(
            table.get("thresholds"),
            default_config.thresholds,
        ),
    )


def _parse_statusline_cache_config(
    value: object,
    default_config: StatuslineCacheConfig,
) -> StatuslineCacheConfig:
    table = _parse_optional_table(value, context="statusline.cache")
    _validate_allowed_keys(
        table,
        _STATUSLINE_CACHE_FIELDS,
        context="statusline.cache",
    )
    return StatuslineCacheConfig(
        output_cache_secs=_parse_non_negative_int(
            table.get("output_cache_secs", default_config.output_cache_secs),
            context="statusline.cache.output_cache_secs",
            required=True,
        )
        or 0,
        min_refresh_interval_secs=_parse_non_negative_int(
            table.get(
                "min_refresh_interval_secs",
                default_config.min_refresh_interval_secs,
            ),
            context="statusline.cache.min_refresh_interval_secs",
            required=True,
        )
        or 0,
        stale_after_secs=_parse_non_negative_int(
            table.get("stale_after_secs", default_config.stale_after_secs),
            context="statusline.cache.stale_after_secs",
            required=True,
        )
        or 0,
    )


def _parse_statusline_thresholds_config(
    value: object,
    default_config: StatuslineThresholdsConfig,
) -> StatuslineThresholdsConfig:
    table = _parse_optional_table(value, context="statusline.thresholds")
    _validate_allowed_keys(
        table,
        _STATUSLINE_THRESHOLDS_FIELDS,
        context="statusline.thresholds",
    )
    return StatuslineThresholdsConfig(
        quota_warning_percent=_parse_non_negative_int(
            table.get(
                "quota_warning_percent",
                default_config.quota_warning_percent,
            ),
            context="statusline.thresholds.quota_warning_percent",
            required=True,
        )
        or 0,
        quota_danger_percent=_parse_non_negative_int(
            table.get(
                "quota_danger_percent",
                default_config.quota_danger_percent,
            ),
            context="statusline.thresholds.quota_danger_percent",
            required=True,
        )
        or 0,
        burn_warning_percent=_parse_non_negative_int(
            table.get(
                "burn_warning_percent",
                default_config.burn_warning_percent,
            ),
            context="statusline.thresholds.burn_warning_percent",
            required=True,
        )
        or 0,
        burn_danger_percent=_parse_non_negative_int(
            table.get(
                "burn_danger_percent",
                default_config.burn_danger_percent,
            ),
            context="statusline.thresholds.burn_danger_percent",
            required=True,
        )
        or 0,
        context_warning_percent=_parse_non_negative_int(
            table.get(
                "context_warning_percent",
                default_config.context_warning_percent,
            ),
            context="statusline.thresholds.context_warning_percent",
            required=True,
        )
        or 0,
        context_danger_percent=_parse_non_negative_int(
            table.get(
                "context_danger_percent",
                default_config.context_danger_percent,
            ),
            context="statusline.thresholds.context_danger_percent",
            required=True,
        )
        or 0,
    )


def _parse_statusline_default_harness(value: object, *, context: str) -> str:
    if not isinstance(value, str):
        msg = f"{context} must be a string."
        raise ValueError(msg)
    normalized = value.strip().lower()
    if normalized == "auto":
        return "auto"
    return _parse_supported_harness(normalized, context=context)


def _parse_statusline_elements(value: object, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        msg = f"{context} must be a list of element names."
        raise ValueError(msg)
    elements: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str):
            msg = f"{context}[{index}] must be a string."
            raise ValueError(msg)
        normalized = item.strip().lower()
        if normalized not in _VALID_STATUSLINE_ELEMENTS:
            msg = (
                f"{context}[{index}] must be one of: "
                f"{', '.join(sorted(_VALID_STATUSLINE_ELEMENTS))}."
            )
            raise ValueError(msg)
        if normalized in seen:
            continue
        seen.add(normalized)
        elements.append(normalized)
    if not elements:
        msg = f"{context} must contain at least one element."
        raise ValueError(msg)
    return tuple(elements)


def _parse_context_windows(value: object) -> tuple[ContextWindowConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = "context_window must be an array of tables."
        raise ValueError(msg)
    windows: list[ContextWindowConfig] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_window in enumerate(value, start=1):
        if not isinstance(raw_window, dict):
            msg = f"context_window[{index}] must be a TOML table."
            raise ValueError(msg)
        _validate_allowed_keys(
            raw_window,
            _CONTEXT_WINDOW_FIELDS,
            context=f"context_window[{index}]",
        )
        window = ContextWindowConfig(
            provider=_parse_required_identity(
                raw_window.get("provider"),
                context=f"context_window[{index}].provider",
            ),
            model=_parse_required_identity(
                raw_window.get("model"),
                context=f"context_window[{index}].model",
            ),
            tokens=_parse_positive_int(
                raw_window.get("tokens"),
                context=f"context_window[{index}].tokens",
            ),
        )
        key = (normalize_identity(window.provider), normalize_identity(window.model))
        if key in seen:
            msg = (
                f"context_window[{index}] duplicates provider/model "
                f"{window.provider}/{window.model}."
            )
            raise ValueError(msg)
        seen.add(key)
        windows.append(window)
    return tuple(windows)


def _parse_import_harnesses(value: object, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        msg = f"{context} must be a list of harness names."
        raise ValueError(msg)
    harnesses: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        harness = _parse_supported_harness(item, context=f"{context}[{index}]")
        if harness in seen:
            msg = f"{context}[{index}] duplicates harness {harness!r}."
            raise ValueError(msg)
        seen.add(harness)
        harnesses.append(harness)
    if not harnesses:
        msg = f"{context} must contain at least one harness."
        raise ValueError(msg)
    return tuple(harnesses)


def _parse_import_sources(
    value: object, *, context: str
) -> dict[str, Path | list[Path]]:
    table = _parse_optional_table(value, context=context)
    sources: dict[str, Path | list[Path]] = {}
    for raw_harness, raw_path in table.items():
        harness = _parse_supported_harness(
            raw_harness,
            context=f"{context}.{raw_harness}",
        )
        # Support both string and list of strings
        if isinstance(raw_path, str):
            sources[harness] = Path(raw_path).expanduser()
        elif isinstance(raw_path, list):
            paths: list[Path] = []
            for idx, item in enumerate(raw_path, start=1):
                if not isinstance(item, str):
                    msg = (
                        f"{context}.{raw_harness}[{idx}] must be a string path, "
                        f"got {type(item).__name__}."
                    )
                    raise ValueError(msg)
                paths.append(Path(item).expanduser())
            sources[harness] = paths
        else:
            msg = (
                f"{context}.{raw_harness} must be a string or list of strings, "
                f"got {type(raw_path).__name__}."
            )
            raise ValueError(msg)
    return sources


def _parse_supported_harness(value: object, *, context: str) -> str:
    harness = normalize_identity(_parse_string(value, context=context))
    if harness not in _SUPPORTED_HARNESSES:
        msg = f"{context} must be one of: {', '.join(sorted(_SUPPORTED_HARNESSES))}."
        raise ValueError(msg)
    return harness


def _parse_actual_cost_rules(
    value: object,
    default_rules: tuple[ActualCostRule, ...],
) -> tuple[ActualCostRule, ...]:
    if value is None:
        return default_rules
    if not isinstance(value, list):
        msg = "actual_cost must be an array of tables."
        raise ValueError(msg)
    rules: list[ActualCostRule] = []
    for index, raw_rule in enumerate(value, start=1):
        if not isinstance(raw_rule, dict):
            msg = f"actual_cost[{index}] must be a TOML table."
            raise ValueError(msg)
        _validate_allowed_keys(
            raw_rule,
            _ACTUAL_COST_RULE_FIELDS,
            context=f"actual_cost[{index}]",
        )
        mode = _parse_choice(
            raw_rule.get("mode"),
            valid=_VALID_ACTUAL_COST_MODES,
            context=f"actual_cost[{index}].mode",
        )
        rules.append(
            ActualCostRule(
                harness=_parse_rule_identity(
                    raw_rule.get("harness"),
                    context=f"actual_cost[{index}].harness",
                ),
                provider=_parse_rule_identity(
                    raw_rule.get("provider"),
                    context=f"actual_cost[{index}].provider",
                ),
                model=_parse_rule_identity(
                    raw_rule.get("model"),
                    context=f"actual_cost[{index}].model",
                ),
                mode=cast(ActualCostMode, mode),
            )
        )
    return tuple(rules)


def _parse_prices(value: object, *, context: str) -> tuple[Price, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = f"{context} must be an array of tables."
        raise ValueError(msg)

    prices: list[Price] = []
    exact_keys: dict[tuple[str, str, int | None, int | None, str], Price] = {}
    lookup_keys: dict[tuple[str, str], tuple[str, str]] = {}
    for index, raw_price in enumerate(value, start=1):
        if not isinstance(raw_price, dict):
            msg = f"{context}[{index}] must be a TOML table."
            raise ValueError(msg)
        _validate_allowed_keys(
            raw_price,
            _PRICE_FIELDS,
            context=f"{context}[{index}]",
        )
        input_usd_per_1m = _parse_required_non_negative_float(
            raw_price.get("input_usd_per_1m"),
            context=f"{context}[{index}].input_usd_per_1m",
        )
        output_usd_per_1m = _parse_required_non_negative_float(
            raw_price.get("output_usd_per_1m"),
            context=f"{context}[{index}].output_usd_per_1m",
        )
        context_min_tokens = _parse_non_negative_int(
            raw_price.get("context_min_tokens"),
            context=f"{context}[{index}].context_min_tokens",
            required=False,
        )
        context_max_tokens = _parse_non_negative_int(
            raw_price.get("context_max_tokens"),
            context=f"{context}[{index}].context_max_tokens",
            required=False,
        )
        if (
            context_min_tokens is not None
            and context_max_tokens is not None
            and context_min_tokens > context_max_tokens
        ):
            msg = (
                f"{context}[{index}] requires context_min_tokens <= context_max_tokens."
            )
            raise ValueError(msg)
        price = Price(
            provider=_parse_required_identity(
                raw_price.get("provider"),
                context=f"{context}[{index}].provider",
            ),
            model=_parse_required_identity(
                raw_price.get("model"),
                context=f"{context}[{index}].model",
            ),
            aliases=_parse_aliases(
                raw_price.get("aliases"),
                context=f"{context}[{index}].aliases",
            ),
            input_usd_per_1m=input_usd_per_1m,
            cached_input_usd_per_1m=_parse_non_negative_float(
                raw_price.get("cached_input_usd_per_1m"),
                context=f"{context}[{index}].cached_input_usd_per_1m",
                required=False,
            ),
            cache_write_usd_per_1m=_parse_non_negative_float(
                raw_price.get("cache_write_usd_per_1m"),
                context=f"{context}[{index}].cache_write_usd_per_1m",
                required=False,
            ),
            cached_output_usd_per_1m=_parse_non_negative_float(
                raw_price.get("cached_output_usd_per_1m"),
                context=f"{context}[{index}].cached_output_usd_per_1m",
                required=False,
            ),
            output_usd_per_1m=output_usd_per_1m,
            reasoning_usd_per_1m=_parse_non_negative_float(
                raw_price.get("reasoning_usd_per_1m"),
                context=f"{context}[{index}].reasoning_usd_per_1m",
                required=False,
            ),
            category=_parse_optional_string(
                raw_price.get("category"),
                context=f"{context}[{index}].category",
            ),
            release_status=_parse_optional_string(
                raw_price.get("release_status"),
                context=f"{context}[{index}].release_status",
            ),
            context_min_tokens=context_min_tokens,
            context_max_tokens=context_max_tokens,
            context_label=_parse_optional_string(
                raw_price.get("context_label"),
                context=f"{context}[{index}].context_label",
            ),
            context_basis=cast(
                PriceContextBasis,
                _parse_choice(
                    raw_price.get("context_basis", "prompt_like"),
                    valid=_VALID_PRICE_CONTEXT_BASES,
                    context=f"{context}[{index}].context_basis",
                ),
            ),
        )
        exact_key = _price_variant_key(price)
        existing = exact_keys.get(exact_key)
        if existing is not None and existing != price:
            msg = (
                f"{context}[{index}] duplicates provider/model/context variant "
                f"{price.provider}/{price.model} with different values."
            )
            raise ValueError(msg)
        exact_keys[exact_key] = price

        canonical_key = (
            normalize_identity(price.provider),
            normalize_identity(price.model),
        )
        for lookup in (price.model, *price.aliases):
            lookup_key = (
                normalize_identity(price.provider),
                normalize_identity(lookup),
            )
            previous = lookup_keys.get(lookup_key)
            if previous is not None and previous != canonical_key:
                msg = (
                    f"{context}[{index}] reuses alias {lookup!r} for "
                    f"{price.provider}/{price.model}."
                )
                raise ValueError(msg)
            lookup_keys[lookup_key] = canonical_key

        prices.append(price)
    _validate_price_context_ranges(tuple(prices), context=context)
    return tuple(prices)


def _parse_aliases(value: object, *, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = f"{context} must be a list of strings."
        raise ValueError(msg)
    aliases: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        alias = _parse_required_identity(item, context=f"{context}[{index}]")
        normalized = normalize_identity(alias)
        if normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(alias)
    return tuple(aliases)


def _parse_required_non_negative_float(value: object, *, context: str) -> float:
    parsed = _parse_non_negative_float(value, context=context, required=True)
    assert parsed is not None
    return parsed


def _parse_non_negative_float(
    value: object,
    *,
    context: str,
    required: bool,
) -> float | None:
    if value is None:
        if required:
            msg = f"{context} is required."
            raise ValueError(msg)
        return None
    if not isinstance(value, (int, float)):
        msg = f"{context} must be a number."
        raise ValueError(msg)
    numeric_value = float(value)
    if numeric_value < 0:
        msg = f"{context} must be non-negative."
        raise ValueError(msg)
    return numeric_value


def _parse_non_negative_int(
    value: object,
    *,
    context: str,
    required: bool,
) -> int | None:
    if value is None:
        if required:
            msg = f"{context} is required."
            raise ValueError(msg)
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{context} must be an integer."
        raise ValueError(msg)
    if value < 0:
        msg = f"{context} must be non-negative."
        raise ValueError(msg)
    return value


def _parse_positive_int(value: object, *, context: str) -> int:
    parsed = _parse_non_negative_int(value, context=context, required=True)
    assert parsed is not None
    if parsed <= 0:
        msg = f"{context} must be positive."
        raise ValueError(msg)
    return parsed


def _parse_positive_float(value: object, *, context: str) -> float | None:
    parsed = _parse_non_negative_float(value, context=context, required=False)
    if parsed is not None and parsed <= 0:
        msg = f"{context} must be positive."
        raise ValueError(msg)
    return parsed


def _parse_bool(value: object, *, context: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{context} must be a boolean."
        raise ValueError(msg)
    return value


def _parse_rule_identity(value: object, *, context: str) -> str | None:
    if value is None:
        return None
    text = _parse_string(value, context=context)
    if text == "*":
        return "*"
    return normalize_identity(text)


def _parse_required_identity(value: object, *, context: str) -> str:
    text = _parse_string(value, context=context)
    if not text.strip():
        msg = f"{context} must not be empty."
        raise ValueError(msg)
    normalize_identity(text)
    return text


def _parse_optional_string(value: object, *, context: str) -> str | None:
    if value is None:
        return None
    text = _parse_string(value, context=context)
    return text or None


def _parse_required_identity_list(value: object, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        msg = f"{context} must be a list of identity strings."
        raise ValueError(msg)
    providers: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        normalized = normalize_identity(
            _parse_required_identity(item, context=f"{context}[{index}]")
        )
        if normalized in seen:
            continue
        seen.add(normalized)
        providers.append(normalized)
    if not providers:
        msg = f"{context} must contain at least one provider."
        raise ValueError(msg)
    return tuple(providers)


def _parse_string(value: object, *, context: str) -> str:
    if not isinstance(value, str):
        msg = f"{context} must be a string."
        raise ValueError(msg)
    return value.strip()


def _parse_choice(
    value: object,
    *,
    valid: set[str],
    context: str,
) -> str:
    text = _parse_string(value, context=context)
    if text not in valid:
        valid_values = ", ".join(sorted(valid))
        msg = f"{context} must be one of: {valid_values}"
        raise ValueError(msg)
    return text


def _parse_subscription_windows(
    value: object,
    *,
    context: str,
    timezone_name: str | None,
) -> tuple[SubscriptionWindowConfig, ...]:
    if not isinstance(value, list):
        msg = f"{context} must be an array of tables."
        raise ValueError(msg)
    if not value:
        msg = f"{context} must include at least one window."
        raise ValueError(msg)

    windows: list[SubscriptionWindowConfig] = []
    enabled_periods: set[str] = set()
    for index, raw_window in enumerate(value, start=1):
        if not isinstance(raw_window, dict):
            msg = f"{context}[{index}] must be a TOML table."
            raise ValueError(msg)
        _validate_allowed_keys(
            raw_window,
            _SUBSCRIPTION_WINDOW_FIELDS,
            context=f"{context}[{index}]",
        )
        period = cast(
            SubscriptionWindowPeriod,
            _parse_choice(
                raw_window.get("period"),
                valid=_VALID_SUBSCRIPTION_WINDOW_PERIODS,
                context=f"{context}[{index}].period",
            ),
        )
        limit_usd = _parse_positive_float(
            raw_window.get("limit_usd"),
            context=f"{context}[{index}].limit_usd",
        )
        if limit_usd is None:
            msg = f"{context}[{index}].limit_usd is required."
            raise ValueError(msg)
        reset_mode = cast(
            SubscriptionWindowResetMode,
            _parse_choice(
                raw_window.get("reset_mode", "fixed"),
                valid=_VALID_SUBSCRIPTION_WINDOW_RESET_MODES,
                context=f"{context}[{index}].reset_mode",
            ),
        )
        reset_at = _parse_string(
            raw_window.get("reset_at"),
            context=f"{context}[{index}].reset_at",
        )
        if not reset_at:
            msg = f"{context}[{index}].reset_at must not be empty."
            raise ValueError(msg)
        enabled = _parse_bool(
            raw_window.get("enabled", True),
            context=f"{context}[{index}].enabled",
        )
        _validate_subscription_reset_at(
            reset_at=reset_at,
            timezone_name=timezone_name,
            context=f"{context}[{index}].reset_at",
        )
        if enabled and period in enabled_periods:
            msg = f"{context}[{index}].period duplicates enabled period {period!r}."
            raise ValueError(msg)
        if enabled:
            enabled_periods.add(period)
        windows.append(
            SubscriptionWindowConfig(
                period=period,
                limit_usd=limit_usd,
                reset_at=reset_at,
                reset_mode=reset_mode,
                enabled=enabled,
            )
        )

    return tuple(windows)


def _subscription_window_reset_at(
    windows: tuple[SubscriptionWindowConfig, ...],
    *,
    period: SubscriptionFixedCostPeriod,
) -> str | None:
    for window in windows:
        if window.enabled and window.period == period:
            return window.reset_at
    return None


def _validate_subscription_reset_at(
    *,
    reset_at: str,
    timezone_name: str | None,
    context: str,
) -> None:
    from toktrail.periods import resolve_fixed_subscription_window

    try:
        resolve_fixed_subscription_window(
            period="daily",
            reset_at=reset_at,
            timezone_name=timezone_name,
            now_ms=0,
        )
    except ValueError as exc:
        msg = f"{context} is invalid: {exc}"
        raise ValueError(msg) from exc


def _validate_allowed_keys(
    table: dict[str, object],
    allowed: set[str],
    *,
    context: str,
) -> None:
    unknown = sorted(set(table) - allowed)
    if not unknown:
        return
    msg = f"{context} has unsupported keys: {', '.join(unknown)}"
    raise ValueError(msg)


def _reject_misplaced_keys(
    data: dict[str, object],
    *,
    invalid: set[str],
    context: str,
    destination_hint: str,
) -> None:
    for key in sorted(invalid & set(data)):
        raise ValueError(f"{context} contains [{key}]. {destination_hint}")


def _matches_rule_value(rule_value: str | None, actual_value: str | None) -> bool:
    if rule_value in (None, "*"):
        return True
    if actual_value is None:
        return False
    return rule_value == normalize_identity(actual_value)


def _specificity_score(value: str | None) -> int:
    if value in (None, "*"):
        return 0
    return 1
