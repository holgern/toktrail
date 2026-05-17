"""Compatibility facade for config APIs during staged refactor."""

from __future__ import annotations

from toktrail.config_parts import legacy_config as _legacy_config

for _name, _value in vars(_legacy_config).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_legacy_config, name)


__all__ = list(
    getattr(
        _legacy_config,
        "__all__",
        [name for name in globals() if not name.startswith("_")],
    )
)
