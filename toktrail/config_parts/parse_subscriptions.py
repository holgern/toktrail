"""Subscription parser ownership facade."""

from __future__ import annotations

from toktrail.config_parts._config_impl import (
    parse_subscriptions_config,
)

__all__ = [name for name in globals() if not name.startswith("_")]
