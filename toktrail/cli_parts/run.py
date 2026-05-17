"""Compatibility re-export module for staged refactor."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_app = import_module("toktrail.cli_parts.app")


def __getattr__(name: str) -> Any:
    return getattr(_app, name)


__all__: list[str] = []
