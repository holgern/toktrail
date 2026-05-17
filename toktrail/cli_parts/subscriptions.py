"""Compatibility re-export module for staged refactor."""
from __future__ import annotations

from importlib import import_module
from typing import Any

_legacy = import_module("toktrail.cli_parts.legacy_cli")

def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)

__all__: list[str] = []
