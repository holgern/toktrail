"""Compatibility facade for config APIs during staged refactor."""

from __future__ import annotations

from importlib import import_module

from toktrail.config_parts.defaults import *  # noqa: F401,F403
from toktrail.config_parts.load import *  # noqa: F401,F403
from toktrail.config_parts.models import *  # noqa: F401,F403
from toktrail.config_parts.parse_areas import *  # noqa: F401,F403
from toktrail.config_parts.parse_pricing import *  # noqa: F401,F403
from toktrail.config_parts.parse_runtime import *  # noqa: F401,F403
from toktrail.config_parts.parse_statusline import *  # noqa: F401,F403
from toktrail.config_parts.parse_subscriptions import *  # noqa: F401,F403
from toktrail.config_parts.parse_sync import *  # noqa: F401,F403

_impl = import_module("toktrail.config_parts._config_impl")


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_impl, name)


__all__ = [name for name in vars(_impl) if not name.startswith("__")]
