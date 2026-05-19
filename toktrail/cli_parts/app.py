"""CLI app facade for the current Typer entrypoint."""

from __future__ import annotations

from toktrail.cli_parts import main_cli as _main


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_main, name)


__all__ = list(
    getattr(_main, "__all__", [name for name in globals() if not name.startswith("_")])
)
