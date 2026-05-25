"""Config defaults/template ownership facade."""

from __future__ import annotations

from toktrail.config_parts._config_impl import (
    COPILOT_TEMPLATE_NAME,
    DEFAULT_TEMPLATE_NAME,
    default_costing_config,
    default_import_config,
    default_machine_config,
    default_pricing_config,
    default_runtime_config,
    default_subscriptions_config,
    default_toktrail_config,
    render_config_template,
    render_machine_template,
    render_prices_template,
    render_subscriptions_template,
)

__all__ = [name for name in globals() if not name.startswith("_")]
