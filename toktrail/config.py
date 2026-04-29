from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

from toktrail.paths import resolve_toktrail_config_path

ActualCostMode = Literal["source", "zero", "pricing"]
VirtualCostMode = Literal["zero", "pricing"]
MissingPriceMode = Literal["zero", "warn"]
ImportMissingSourceMode = Literal["warn", "error", "skip"]

CONFIG_VERSION = 1
DEFAULT_TEMPLATE_NAME = "default"
COPILOT_TEMPLATE_NAME = "copilot"
_VALID_ACTUAL_COST_MODES = {"source", "zero", "pricing"}
_VALID_VIRTUAL_COST_MODES = {"zero", "pricing"}
_VALID_MISSING_PRICE_MODES = {"zero", "warn"}
_VALID_IMPORT_MISSING_SOURCE_MODES = {"warn", "error", "skip"}
_PRICE_FIELDS = {
    "provider",
    "model",
    "aliases",
    "input_usd_per_1m",
    "cached_input_usd_per_1m",
    "cache_write_usd_per_1m",
    "output_usd_per_1m",
    "reasoning_usd_per_1m",
    "category",
    "release_status",
}
_ACTUAL_COST_RULE_FIELDS = {"harness", "provider", "model", "mode"}
_IMPORT_FIELDS = {"harnesses", "sources", "missing_source", "include_raw_json"}
_COSTING_FIELDS = {
    "default_actual_mode",
    "default_virtual_mode",
    "missing_price",
    "price_profile",
}
_PRICING_FIELDS = {"virtual", "actual"}
_SUPPORTED_HARNESSES = {"opencode", "pi", "copilot", "codex", "goose", "droid"}
_SEPARATOR_RE = re.compile(r"[/_\s]+")
_INVALID_IDENTITY_CHARS_RE = re.compile(r"[^a-z0-9.-]+")
_DASH_RE = re.compile(r"-+")

DEFAULT_CONFIG_TEXT = """\
config_version = 1

[imports]
harnesses = ["opencode", "pi", "copilot", "codex", "goose", "droid"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "~/.local/share/opencode/opencode.db"
pi = "~/.pi/agent/sessions"
copilot = "~/.copilot/otel"
codex = "~/.codex/sessions"
goose = "~/.local/share/goose/sessions/sessions.db"
droid = "~/.factory/sessions"

[costing]
default_actual_mode = "source"
default_virtual_mode = "pricing"
missing_price = "warn"

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
harness = "goose"
mode = "zero"

[[actual_cost]]
harness = "droid"
mode = "zero"
"""

COPILOT_TEMPLATE_TEXT = """\
config_version = 1

[imports]
harnesses = ["opencode", "pi", "copilot", "codex", "goose", "droid"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "~/.local/share/opencode/opencode.db"
pi = "~/.pi/agent/sessions"
copilot = "~/.copilot/otel"
codex = "~/.codex/sessions"
goose = "~/.local/share/goose/sessions/sessions.db"
droid = "~/.factory/sessions"

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
harness = "goose"
mode = "zero"

[[actual_cost]]
harness = "droid"
mode = "zero"

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
    output_usd_per_1m: float = 0.0
    reasoning_usd_per_1m: float | None = None
    category: str | None = None
    release_status: str | None = None


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
class CostingConfig:
    config_version: int = CONFIG_VERSION
    default_actual_mode: ActualCostMode = "source"
    default_virtual_mode: VirtualCostMode = "pricing"
    missing_price: MissingPriceMode = "warn"
    price_profile: str | None = None
    actual_rules: tuple[ActualCostRule, ...] = ()
    virtual_prices: tuple[Price, ...] = ()
    actual_prices: tuple[Price, ...] = ()

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
        "goose",
        "droid",
    )
    sources: dict[str, Path] | None = None
    missing_source: ImportMissingSourceMode = "warn"
    include_raw_json: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", dict(self.sources or {}))


@dataclass(frozen=True)
class ToktrailConfig:
    costing: CostingConfig
    imports: ImportConfig


@dataclass(frozen=True)
class LoadedCostingConfig:
    path: Path
    exists: bool
    config: CostingConfig


@dataclass(frozen=True)
class LoadedToktrailConfig:
    path: Path
    exists: bool
    config: ToktrailConfig


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
                harness="goose",
                provider=None,
                model=None,
                mode="zero",
            ),
            ActualCostRule(
                harness="droid",
                provider=None,
                model=None,
                mode="zero",
            ),
        )
    )


def default_import_config() -> ImportConfig:
    return ImportConfig()


def default_toktrail_config() -> ToktrailConfig:
    return ToktrailConfig(
        costing=default_costing_config(),
        imports=default_import_config(),
    )


def render_config_template(template: str = DEFAULT_TEMPLATE_NAME) -> str:
    if template == DEFAULT_TEMPLATE_NAME:
        return DEFAULT_CONFIG_TEXT
    if template == COPILOT_TEMPLATE_NAME:
        return COPILOT_TEMPLATE_TEXT
    msg = f"Unsupported config template: {template}"
    raise ValueError(msg)


def load_costing_config(path: Path) -> CostingConfig:
    return load_toktrail_config(path).costing


def load_toktrail_config(path: Path) -> ToktrailConfig:
    if not path.exists():
        return default_toktrail_config()
    if not path.is_file():
        msg = f"Toktrail config path is not a file: {path}"
        raise ValueError(msg)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        msg = f"Invalid TOML in toktrail config {path}: {exc}"
        raise ValueError(msg) from exc
    return parse_toktrail_config(data)


def load_resolved_costing_config(
    cli_value: Path | None = None,
) -> LoadedCostingConfig:
    loaded = load_resolved_toktrail_config(cli_value)
    return LoadedCostingConfig(
        path=loaded.path,
        exists=loaded.exists,
        config=loaded.config.costing,
    )


def load_resolved_toktrail_config(
    cli_value: Path | None = None,
) -> LoadedToktrailConfig:
    path = resolve_toktrail_config_path(cli_value)
    return LoadedToktrailConfig(
        path=path,
        exists=path.exists(),
        config=load_toktrail_config(path),
    )


def parse_costing_config(data: object) -> CostingConfig:
    return parse_toktrail_config(data).costing


def parse_toktrail_config(data: object) -> ToktrailConfig:
    if not isinstance(data, dict):
        msg = "Toktrail config must be a TOML table."
        raise ValueError(msg)

    default_config = default_toktrail_config()
    costing_default = default_config.costing
    config_version = _parse_config_version(data.get("config_version", CONFIG_VERSION))
    costing_table = _parse_optional_table(data.get("costing"), context="costing")
    _validate_allowed_keys(costing_table, _COSTING_FIELDS, context="costing")
    default_actual_mode = _parse_choice(
        costing_table.get("default_actual_mode", costing_default.default_actual_mode),
        valid=_VALID_ACTUAL_COST_MODES,
        context="costing.default_actual_mode",
    )
    default_virtual_mode = _parse_choice(
        costing_table.get("default_virtual_mode", costing_default.default_virtual_mode),
        valid=_VALID_VIRTUAL_COST_MODES,
        context="costing.default_virtual_mode",
    )
    missing_price = _parse_choice(
        costing_table.get("missing_price", costing_default.missing_price),
        valid=_VALID_MISSING_PRICE_MODES,
        context="costing.missing_price",
    )
    price_profile = _parse_optional_string(
        costing_table.get("price_profile"),
        context="costing.price_profile",
    )

    actual_rules = _parse_actual_cost_rules(
        data.get("actual_cost"),
        costing_default.actual_rules,
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

    return ToktrailConfig(
        costing=CostingConfig(
            config_version=config_version,
            default_actual_mode=cast(ActualCostMode, default_actual_mode),
            default_virtual_mode=cast(VirtualCostMode, default_virtual_mode),
            missing_price=cast(MissingPriceMode, missing_price),
            price_profile=price_profile,
            actual_rules=actual_rules,
            virtual_prices=virtual_prices,
            actual_prices=actual_prices,
        ),
        imports=_parse_import_config(
            data.get("imports"),
            default_config.imports,
        ),
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
    )


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


def _parse_import_sources(value: object, *, context: str) -> dict[str, Path]:
    table = _parse_optional_table(value, context=context)
    sources: dict[str, Path] = {}
    for raw_harness, raw_path in table.items():
        harness = _parse_supported_harness(
            raw_harness,
            context=f"{context}.{raw_harness}",
        )
        path_value = _parse_string(raw_path, context=f"{context}.{raw_harness}")
        sources[harness] = Path(path_value).expanduser()
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
    exact_keys: dict[tuple[str, str], Price] = {}
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
        )
        exact_key = (
            normalize_identity(price.provider),
            normalize_identity(price.model),
        )
        existing = exact_keys.get(exact_key)
        if existing is not None and existing != price:
            msg = (
                f"{context}[{index}] duplicates provider/model "
                f"{price.provider}/{price.model} with different values."
            )
            raise ValueError(msg)
        exact_keys[exact_key] = price

        canonical_key = exact_key
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
