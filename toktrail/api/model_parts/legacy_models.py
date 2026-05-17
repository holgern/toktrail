"""Compatibility facade for API model parts during staged refactor."""

from __future__ import annotations

from toktrail.api.model_parts import core_models as _core_models
from toktrail.api.model_parts.core_models import *  # noqa: F403

__all__ = list(
    getattr(
        _core_models,
        "__all__",
        [name for name in globals() if not name.startswith("_")],
    )
)
