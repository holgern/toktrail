"""Compatibility facade for DB internals during staged refactor."""

from __future__ import annotations

from toktrail._db import core_db as _core_db
from toktrail._db.core_db import *  # noqa: F403

__all__ = list(
    getattr(
        _core_db, "__all__", [name for name in globals() if not name.startswith("_")]
    )
)
