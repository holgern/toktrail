"""Compatibility facade for CLI entrypoints during staged refactor."""

from __future__ import annotations

from toktrail.cli_parts import legacy_cli as _legacy_cli

for _name, _value in vars(_legacy_cli).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

app = _legacy_cli.app
cli_main = _legacy_cli.cli_main


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_legacy_cli, name)


__all__ = list(getattr(_legacy_cli, "__all__", [name for name in globals() if not name.startswith("_")]))
