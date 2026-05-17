"""Compatibility facade for config internals during staged refactor."""

from __future__ import annotations

from toktrail.config_parts import core_config as _core_config
from toktrail.config_parts.core_config import *  # noqa: F403

__all__ = list(
    getattr(
        _core_config,
        "__all__",
        [name for name in globals() if not name.startswith("_")],
    )
)
