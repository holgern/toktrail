"""Compatibility app module for staged CLI extraction."""

from __future__ import annotations

from toktrail.cli_parts import legacy_cli as _legacy
from toktrail.cli_parts.legacy_cli import app, cli_main


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_legacy, name)


__all__ = ["app", "cli_main"]
