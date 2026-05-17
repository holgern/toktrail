"""Compatibility re-export module for staged refactor."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_core = import_module("toktrail.config_parts.core_config")


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


__all__: list[str] = []
