"""Compatibility facade for CLI entrypoints during staged refactor."""

from __future__ import annotations

from toktrail.cli_parts import app as _app_module

for _name, _value in vars(_app_module).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

app = _app_module.app
cli_main = _app_module.cli_main


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_app_module, name)


__all__ = list(
    getattr(
        _app_module, "__all__", [name for name in globals() if not name.startswith("_")]
    )
)
