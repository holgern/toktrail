from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

from toktrail.api._common import _open_state_db
from toktrail.api._conversions import (
    _to_public_state_export_result,
    _to_public_state_import_result,
)
from toktrail.api.models import StateExportResult, StateImportResult
from toktrail.errors import InvalidAPIUsageError, StateDatabaseError
from toktrail.paths import resolve_toktrail_config_path
from toktrail.sync import (
    default_archive_name as _default_archive_name,
)
from toktrail.sync import (
    export_state_archive as _export_state_archive,
)
from toktrail.sync import (
    import_state_archive as _import_state_archive,
)

ConflictMode = Literal["fail", "skip"]
RemoteActiveMode = Literal["fail", "close-at-export", "keep"]


def default_archive_name() -> str:
    return _default_archive_name()


def export_state_archive(
    db_path: Path | None,
    archive_path: Path,
    *,
    config_path: Path | None = None,
    include_config: bool = False,
    redact_raw_json: bool = False,
) -> StateExportResult:
    conn, resolved_db = _open_state_db(db_path)
    conn.close()
    resolved_config = (
        resolve_toktrail_config_path(config_path) if include_config else None
    )
    try:
        result = _export_state_archive(
            resolved_db,
            archive_path,
            config_path=resolved_config,
            include_config=include_config,
            redact_raw_json=redact_raw_json,
        )
    except ValueError as exc:
        raise InvalidAPIUsageError(str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise StateDatabaseError(str(exc)) from exc
    return _to_public_state_export_result(result)


def import_state_archive(
    db_path: Path | None,
    archive_path: Path,
    *,
    dry_run: bool = False,
    on_conflict: str = "fail",
    remote_active: str = "fail",
) -> StateImportResult:
    conn, resolved_db = _open_state_db(db_path)
    conn.close()
    try:
        if on_conflict not in {"fail", "skip"}:
            msg = "on_conflict must be one of: fail, skip."
            raise InvalidAPIUsageError(msg)
        if remote_active not in {"fail", "close-at-export", "keep"}:
            msg = "remote_active must be one of: fail, close-at-export, keep."
            raise InvalidAPIUsageError(msg)
        conflict_mode: ConflictMode
        if on_conflict == "skip":
            conflict_mode = "skip"
        else:
            conflict_mode = "fail"
        remote_active_mode: RemoteActiveMode
        if remote_active == "close-at-export":
            remote_active_mode = "close-at-export"
        elif remote_active == "keep":
            remote_active_mode = "keep"
        else:
            remote_active_mode = "fail"
        result = _import_state_archive(
            resolved_db,
            archive_path,
            dry_run=dry_run,
            on_conflict=conflict_mode,
            remote_active=remote_active_mode,
        )
    except ValueError as exc:
        raise InvalidAPIUsageError(str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise StateDatabaseError(str(exc)) from exc
    return _to_public_state_import_result(result)


__all__ = [
    "default_archive_name",
    "export_state_archive",
    "import_state_archive",
]
