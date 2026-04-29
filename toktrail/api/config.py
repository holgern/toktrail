from __future__ import annotations

from pathlib import Path

from toktrail import config as config_module
from toktrail.api.paths import (
    default_toktrail_config_path,
    resolve_toktrail_config_path,
)
from toktrail.errors import ConfigurationError, InvalidAPIUsageError


def config_exists(config_path: Path | None = None) -> bool:
    return resolve_toktrail_config_path(config_path).exists()


def config_summary(config_path: Path | None = None) -> dict[str, object]:
    resolved = resolve_toktrail_config_path(config_path)
    try:
        loaded = config_module.load_resolved_costing_config(config_path)
        summary = config_module.summarize_costing_config(loaded.config)
    except ValueError as exc:
        msg = f"Invalid toktrail config at {resolved}: {exc}"
        raise ConfigurationError(msg) from exc
    return {
        "path": str(loaded.path),
        "exists": loaded.exists,
        "config_version": summary.config_version,
        "default_actual_mode": summary.default_actual_mode,
        "default_virtual_mode": summary.default_virtual_mode,
        "missing_price": summary.missing_price,
        "price_profile": summary.price_profile,
        "actual_rule_count": summary.actual_rule_count,
        "actual_price_count": summary.actual_price_count,
        "virtual_price_count": summary.virtual_price_count,
    }


def render_config_template(template: str = "default") -> str:
    try:
        return config_module.render_config_template(template)
    except ValueError as exc:
        msg = f"Unsupported toktrail config template {template!r}: {exc}"
        raise ConfigurationError(msg) from exc


def init_config(
    config_path: Path | None = None,
    *,
    template: str = "default",
    force: bool = False,
) -> Path:
    resolved = resolve_toktrail_config_path(config_path)
    if resolved.exists() and not force:
        msg = (
            f"Toktrail config already exists at {resolved}; "
            "pass force=True to overwrite."
        )
        raise InvalidAPIUsageError(msg)
    try:
        content = config_module.render_config_template(template)
    except ValueError as exc:
        msg = f"Unsupported toktrail config template {template!r}: {exc}"
        raise ConfigurationError(msg) from exc
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except OSError as exc:
        msg = f"Could not write toktrail config to {resolved}: {exc}"
        raise ConfigurationError(msg) from exc
    return resolved


__all__ = [
    "config_exists",
    "config_summary",
    "default_toktrail_config_path",
    "init_config",
    "render_config_template",
    "resolve_toktrail_config_path",
]
