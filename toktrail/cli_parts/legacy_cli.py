"""Compatibility facade for CLI internals during staged refactor."""

from __future__ import annotations

from toktrail.cli_parts import main_cli as _main_cli
from toktrail.cli_parts.main_cli import *  # noqa: F403

app = _main_cli.app
cli_main = _main_cli.cli_main


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_main_cli, name)


__all__ = list(
    getattr(
        _main_cli, "__all__", [name for name in globals() if not name.startswith("_")]
    )
)
