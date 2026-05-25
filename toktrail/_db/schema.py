"""Domain facade module for staged DB extraction."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_impl = import_module("toktrail._db._impl_db")


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


__all__: list[str] = []
