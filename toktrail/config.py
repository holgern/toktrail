"""Compatibility facade for config APIs during staged refactor."""

from __future__ import annotations

from toktrail.config_parts import core_config as _core_config

for _name, _value in vars(_core_config).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_core_config, name)


__all__ = list(
    getattr(
        _core_config,
        "__all__",
        [name for name in globals() if not name.startswith("_")],
    )
)
