# mypy: ignore-errors
from __future__ import annotations

from pathlib import Path

from toktrail.cli_parts.formatting import _format_cost


def leaf_path(value: str | None, *, fallback: str = "-") -> str:
    if not value:
        return fallback
    return value.rstrip("/").rsplit("/", 1)[-1] or value


def compact_model(models: tuple[str, ...], *, limit: int = 1) -> str:
    if not models:
        return "-"
    visible = list(models[:limit])
    suffix = "" if len(models) <= limit else f" +{len(models) - limit}"
    return ", ".join(visible) + suffix


def compact_time(value: str) -> str:
    if len(value) >= 16 and value[4:5] == "-" and value[13:14] == ":":
        return value[11:16]
    return value[:16]


def session_cost_label(actual_cost: float, virtual_cost: float) -> str:
    if actual_cost > 0:
        return _format_cost(actual_cost)
    if virtual_cost > 0:
        return _format_cost(virtual_cost)
    return _format_cost(0.0)


def abbreviate_home(path_value: str) -> str:
    home = str(Path.home())
    if path_value.startswith(home):
        return "~" + path_value[len(home) :]
    return path_value
