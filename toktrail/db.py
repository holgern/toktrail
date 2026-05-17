"""Compatibility facade for DB APIs during staged refactor."""

from __future__ import annotations

from toktrail._db import core_db as _core_db

SCHEMA_VERSION = _core_db.SCHEMA_VERSION

for _name, _value in vars(_core_db).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_core_db, name)


__all__ = list(
    getattr(
        _core_db, "__all__", [name for name in globals() if not name.startswith("_")]
    )
)
