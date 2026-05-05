from __future__ import annotations

import sqlite3
from pathlib import Path

from toktrail import config as config_module
from toktrail import db as db_module
from toktrail.adapters.registry import HarnessDefinition as InternalHarnessDefinition
from toktrail.adapters.registry import get_harness
from toktrail.config import CostingConfig
from toktrail.errors import (
    ConfigurationError,
    SourcePathError,
    StateDatabaseError,
    UnsupportedHarnessError,
)
from toktrail.paths import resolve_toktrail_config_path, resolve_toktrail_db_path


def _normalize_harness_name(harness: str) -> str:
    return harness.strip().lower()


def _get_harness(harness: str) -> InternalHarnessDefinition:
    normalized = _normalize_harness_name(harness)
    try:
        return get_harness(normalized)
    except ValueError as exc:
        msg = f"Unsupported harness: {harness}"
        raise UnsupportedHarnessError(msg) from exc


def _open_state_db(db_path: Path | None) -> tuple[sqlite3.Connection, Path]:
    resolved = resolve_toktrail_db_path(db_path)
    try:
        conn = db_module.connect(resolved)
        db_module.migrate(conn)
    except (OSError, sqlite3.Error, ValueError) as exc:
        msg = f"Could not open toktrail state database at {resolved}: {exc}"
        raise StateDatabaseError(msg) from exc
    return conn, resolved


def _load_costing_config(config_path: Path | None) -> CostingConfig:
    resolved = resolve_toktrail_config_path(config_path)
    try:
        prices_path = None
        subscriptions_path = None
        if config_path is not None:
            prices_path = resolved.with_name("prices.toml")
            subscriptions_path = resolved.with_name("subscriptions.toml")
        loaded = config_module.load_resolved_costing_config(
            config_cli_value=resolved,
            prices_cli_value=prices_path,
            subscriptions_cli_value=subscriptions_path,
        )
        return loaded.config
    except ValueError:
        try:
            return config_module.load_costing_config(resolved)
        except ValueError as fallback_exc:
            msg = f"Invalid toktrail config at {resolved}: {fallback_exc}"
            raise ConfigurationError(msg) from fallback_exc


def _missing_source_path_message(
    harness_name: str,
    resolved_source: Path | None,
    *,
    explicit_source: Path | None,
) -> str:
    if harness_name == "opencode":
        return f"OpenCode database not found: {resolved_source}"
    if harness_name == "pi":
        return f"Pi sessions path not found: {resolved_source}"
    if harness_name == "copilot" and (
        explicit_source is not None
        or (resolved_source is not None and resolved_source.suffix == ".jsonl")
    ):
        return f"Copilot telemetry file not found: {resolved_source}"
    display_name = _get_harness(harness_name).display_name
    return f"{display_name} source path not found: {resolved_source}"


def _validate_source_path(
    harness_name: str,
    resolved_source: Path | None,
    *,
    explicit_source: Path | None,
    allow_missing_default: bool = False,
    allow_missing_explicit: bool = False,
) -> Path | None:
    if resolved_source is None:
        if explicit_source is None and allow_missing_default:
            return None
        msg = _missing_source_path_message(
            harness_name,
            resolved_source,
            explicit_source=explicit_source,
        )
        raise SourcePathError(msg)

    if resolved_source.exists():
        if harness_name == "opencode" and not resolved_source.is_file():
            msg = _missing_source_path_message(
                harness_name,
                resolved_source,
                explicit_source=explicit_source,
            )
            raise SourcePathError(msg)
        return resolved_source

    if explicit_source is not None and allow_missing_explicit:
        return resolved_source
    if explicit_source is None and allow_missing_default:
        return resolved_source

    msg = _missing_source_path_message(
        harness_name,
        resolved_source,
        explicit_source=explicit_source,
    )
    raise SourcePathError(msg)
