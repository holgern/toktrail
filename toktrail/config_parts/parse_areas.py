"""Compatibility re-export module for staged refactor."""
from __future__ import annotations

from importlib import import_module
from typing import Any

_legacy = import_module("toktrail.config_parts.legacy_config")

def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)

__all__: list[str] = []
