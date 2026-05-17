"""Compatibility facade for API models during staged refactor."""

from __future__ import annotations

from toktrail.api.model_parts import legacy_models as _legacy_models

for _name, _value in vars(_legacy_models).items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_legacy_models, name)


__all__ = list(getattr(_legacy_models, "__all__", [name for name in globals() if not name.startswith("_")]))
