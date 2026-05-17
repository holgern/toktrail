"""Compatibility facade for git sync APIs during staged refactor."""

from __future__ import annotations

from toktrail.git_sync_parts import core as _core
from toktrail.git_sync_parts.core import *  # noqa: F403

__all__ = list(
    getattr(_core, "__all__", [name for name in globals() if not name.startswith("_")])
)
