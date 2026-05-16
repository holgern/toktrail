from __future__ import annotations

import json
import socket
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from time import time
from typing import Literal

from toktrail import __version__
from toktrail import db as db_module

SYNC_ARCHIVE_FORMAT = "toktrail.sync-archive.v1"
MANIFEST_NAME = "manifest.json"
STATE_DB_NAME = "toktrail-state.sqlite"
CONFIG_NAME = "config.toml"

ConflictMode = Literal["fail", "skip"]
RemoteActiveMode = Literal["fail", "close-at-export", "keep"]


@dataclass(frozen=True)
class StateExportResult:
    archive_path: Path
    exported_at_ms: int
    schema_version: int
    machine_id: str
    machine_name: str | None
    run_count: int
    source_session_count: int
    usage_event_count: int
    run_event_count: int
    raw_json_count: int


@dataclass(frozen=True)
class StateImportConflict:
    kind: str
    harness: str | None = None
    global_dedup_key: str | None = None
    local_fingerprint: str | None = None
    imported_fingerprint: str | None = None
    message: str = ""


@dataclass(frozen=True)
class StateImportResult:
    archive_path: Path
    dry_run: bool
    runs_inserted: int
    runs_updated: int
    source_sessions_inserted: int
    source_sessions_updated: int
    usage_events_inserted: int
    usage_events_skipped: int
    run_events_inserted: int
    conflicts: tuple[StateImportConflict, ...]


class _ImportConflictError(ValueError):
    def __init__(self, conflicts: tuple[StateImportConflict, ...]) -> None:
        self.conflicts = conflicts
        first = conflicts[0] if conflicts else None
        detail = (
            first.message if first is not None and first.message else "import conflict"
        )
        super().__init__(detail)


def export_state_archive(
    db_path: Path,
    archive_path: Path,
    *,
    config_path: Path | None = None,
    include_config: bool = False,
    redact_raw_json: bool = False,
) -> StateExportResult:
    exported_at_ms = _now_ms()
    archive_path = archive_path.expanduser()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = db_path.expanduser()
    bootstrap_conn = db_module.connect(db_path)
    try:
        from toktrail.config import load_machine_config

        db_module.migrate(bootstrap_conn)
        machine_config = load_machine_config().config
        db_module.apply_local_machine_config(bootstrap_conn, machine_config)
        bootstrap_conn.commit()
    finally:
        bootstrap_conn.close()

    with tempfile.TemporaryDirectory(prefix="toktrail-sync-export-") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        snapshot_path = temp_dir / STATE_DB_NAME
        _create_state_snapshot(
            db_path=db_path,
            snapshot_path=snapshot_path,
            redact_raw_json=redact_raw_json,
        )
        counts = _read_counts(snapshot_path)
        machine_id, machine_name = _read_machine_identity(snapshot_path)

        archive_config_name: str | None = None
        checksum_map = {STATE_DB_NAME: _sha256_file(snapshot_path)}

        if include_config:
            if config_path is None:
                msg = "--include-config requires a config path."
                raise ValueError(msg)
            resolved_config = config_path.expanduser()
            if not resolved_config.exists() or not resolved_config.is_file():
                msg = f"Config file not found: {resolved_config}"
                raise ValueError(msg)
            config_target = temp_dir / CONFIG_NAME
            config_target.write_text(
                resolved_config.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            archive_config_name = CONFIG_NAME
            checksum_map[CONFIG_NAME] = _sha256_file(config_target)

        manifest = {
            "format": SYNC_ARCHIVE_FORMAT,
            "created_by": "toktrail",
            "toktrail_version": __version__,
            "exported_at_ms": exported_at_ms,
            "schema_version": db_module.SCHEMA_VERSION,
            "machine_id": machine_id,
            "machine_name": machine_name,
            "contains": {
                "state_db": STATE_DB_NAME,
                "config": archive_config_name,
            },
            "counts": {
                "runs": counts["runs"],
                "source_sessions": counts["source_sessions"],
                "usage_events": counts["usage_events"],
                "run_events": counts["run_events"],
                "raw_json_rows": counts["raw_json_rows"],
            },
            "sha256": checksum_map,
        }
        manifest_path = temp_dir / MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(manifest_path, arcname=MANIFEST_NAME)
            tar.add(snapshot_path, arcname=STATE_DB_NAME)
            if archive_config_name is not None:
                tar.add(temp_dir / archive_config_name, arcname=archive_config_name)

    return StateExportResult(
        archive_path=archive_path,
        exported_at_ms=exported_at_ms,
        schema_version=db_module.SCHEMA_VERSION,
        machine_id=machine_id,
        machine_name=machine_name,
        run_count=counts["runs"],
        source_session_count=counts["source_sessions"],
        usage_event_count=counts["usage_events"],
        run_event_count=counts["run_events"],
        raw_json_count=counts["raw_json_rows"],
    )


def import_state_archive(
    db_path: Path,
    archive_path: Path,
    *,
    dry_run: bool = False,
    on_conflict: ConflictMode = "fail",
    remote_active: RemoteActiveMode = "fail",
) -> StateImportResult:
    if on_conflict not in {"fail", "skip"}:
        msg = "--on-conflict must be one of: fail, skip."
        raise ValueError(msg)
    if remote_active not in {"fail", "close-at-export", "keep"}:
        msg = "--remote-active must be one of: fail, close-at-export, keep."
        raise ValueError(msg)

    archive_path = archive_path.expanduser()
    if not archive_path.exists() or not archive_path.is_file():
        msg = f"Archive not found: {archive_path}"
        raise ValueError(msg)

    with tempfile.TemporaryDirectory(prefix="toktrail-sync-import-") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        _safe_extract_archive(archive_path, temp_dir)
        manifest_path = _find_manifest(temp_dir)
        manifest = _load_manifest(manifest_path)
        _validate_manifest(manifest)

        contains = _required_dict(manifest, "contains")
        db_member = _required_str(contains, "state_db")
        imported_db_path = (temp_dir / db_member).resolve()
        if not imported_db_path.exists() or not imported_db_path.is_file():
            msg = f"Archive state database not found: {db_member}"
            raise ValueError(msg)

        checksum_map = _required_dict(manifest, "sha256")
        expected_db_hash = _required_str(checksum_map, db_member)
        actual_db_hash = _sha256_file(imported_db_path)
        if actual_db_hash != expected_db_hash:
            msg = f"Archive checksum mismatch for {db_member}."
            raise ValueError(msg)

        imported_schema_version = _required_int(manifest, "schema_version")
        if imported_schema_version > db_module.SCHEMA_VERSION:
            msg = (
                "Archive schema version is newer than this toktrail build: "
                f"{imported_schema_version} > {db_module.SCHEMA_VERSION}"
            )
            raise ValueError(msg)

        imported_at_ms = _required_int(manifest, "exported_at_ms")
        imported_machine_id = _required_str(manifest, "machine_id")
        imported_machine_name = _optional_manifest_str(manifest, "machine_name")

        target = db_module.connect(db_path.expanduser())
        imported = sqlite3.connect(imported_db_path)
        imported.row_factory = sqlite3.Row
        try:
            db_module.migrate(target)
            db_module.migrate(imported)
            _validate_imported_db(imported)

            target.execute("BEGIN")
            try:
                _merge_machines(
                    target,
                    imported,
                    imported_machine_id=imported_machine_id,
                    imported_machine_name=imported_machine_name,
                    imported_at_ms=imported_at_ms,
                )
                run_id_map, runs_inserted, runs_updated = _merge_runs(
                    target,
                    imported,
                    imported_machine_id=imported_machine_id,
                    imported_at_ms=imported_at_ms,
                    remote_active=remote_active,
                )
                source_id_map, source_inserted, source_updated = _merge_source_sessions(
                    target,
                    imported,
                    run_id_map=run_id_map,
                )
                (
                    usage_id_map,
                    usage_inserted,
                    usage_skipped,
                    conflicts,
                ) = _merge_usage_events(
                    target,
                    imported,
                    run_id_map=run_id_map,
                    source_session_id_map=source_id_map,
                    imported_machine_id=imported_machine_id,
                    on_conflict=on_conflict,
                )
                run_events_inserted = _merge_run_events(
                    target,
                    imported,
                    run_id_map=run_id_map,
                    usage_event_id_map=usage_id_map,
                )
            except Exception:
                target.rollback()
                raise

            if dry_run:
                target.rollback()
            else:
                target.commit()
        finally:
            imported.close()
            target.close()

    return StateImportResult(
        archive_path=archive_path,
        dry_run=dry_run,
        runs_inserted=runs_inserted,
        runs_updated=runs_updated,
        source_sessions_inserted=source_inserted,
        source_sessions_updated=source_updated,
        usage_events_inserted=usage_inserted,
        usage_events_skipped=usage_skipped,
        run_events_inserted=run_events_inserted,
        conflicts=conflicts,
    )


def default_archive_name() -> str:
    host = socket.gethostname().strip().lower().replace(" ", "-") or "host"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"toktrail-state-{host}-{stamp}.tar.gz"


def _create_state_snapshot(
    *,
    db_path: Path,
    snapshot_path: Path,
    redact_raw_json: bool,
) -> None:
    src = db_module.connect(db_path)
    src.row_factory = sqlite3.Row
    dest = sqlite3.connect(snapshot_path)
    try:
        db_module.migrate(src)
        src.backup(dest)
        if redact_raw_json:
            dest.execute("UPDATE usage_events SET raw_json = NULL")
        dest.commit()
    finally:
        dest.close()
        src.close()


def _read_counts(snapshot_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(snapshot_path)
    conn.row_factory = sqlite3.Row
    try:
        return {
            "runs": _query_count(conn, "runs"),
            "source_sessions": _query_count(conn, "source_sessions"),
            "usage_events": _query_count(conn, "usage_events"),
            "run_events": _query_count(conn, "run_events"),
            "raw_json_rows": _query_count(
                conn,
                "usage_events",
                where_clause="WHERE raw_json IS NOT NULL",
            ),
        }
    finally:
        conn.close()


def _read_machine_identity(snapshot_path: Path) -> tuple[str, str | None]:
    conn = sqlite3.connect(snapshot_path)
    conn.row_factory = sqlite3.Row
    try:
        machine_id = db_module.get_machine_id(conn)
        machine = db_module.get_machine(conn, machine_id)
        return machine_id, machine.name if machine is not None else None
    finally:
        conn.close()


def _query_count(
    conn: sqlite3.Connection,
    table: str,
    *,
    where_clause: str = "",
) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM {table} {where_clause}"
    ).fetchone()
    if row is None:
        msg = f"Could not count rows for {table}."
        raise ValueError(msg)
    return int(row["count"])


def _safe_extract_archive(archive_path: Path, temp_dir: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                msg = f"Unsafe archive member path: {member.name}"
                raise ValueError(msg)
            if member.islnk() or member.issym():
                msg = f"Refusing archive link member: {member.name}"
                raise ValueError(msg)
            if not member.isfile():
                msg = f"Unsupported archive member: {member.name}"
                raise ValueError(msg)
        try:
            tar.extractall(temp_dir, filter="data")
        except TypeError:
            # Python versions before tarfile's extraction filter support.
            tar.extractall(temp_dir)


def _find_manifest(temp_dir: Path) -> Path:
    manifests = [path for path in temp_dir.rglob(MANIFEST_NAME) if path.is_file()]
    if len(manifests) != 1:
        msg = "Archive must contain exactly one manifest.json."
        raise ValueError(msg)
    return manifests[0]


def _load_manifest(manifest_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"Invalid manifest JSON: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(payload, dict):
        msg = "Manifest must be a JSON object."
        raise ValueError(msg)
    return payload


def _validate_manifest(manifest: dict[str, object]) -> None:
    if _required_str(manifest, "format") != SYNC_ARCHIVE_FORMAT:
        msg = "Unsupported sync archive format."
        raise ValueError(msg)
    contains = _required_dict(manifest, "contains")
    db_name = _required_str(contains, "state_db")
    _required_dict(manifest, "counts")
    checksums = _required_dict(manifest, "sha256")
    _required_str(checksums, db_name)
    _required_int(manifest, "schema_version")
    _required_int(manifest, "exported_at_ms")
    _required_str(manifest, "machine_id")
    _optional_manifest_str(manifest, "machine_name")


def _validate_imported_db(imported: sqlite3.Connection) -> None:
    row = imported.execute("PRAGMA user_version").fetchone()
    if row is None:
        msg = "Could not read imported database schema version."
        raise ValueError(msg)
    user_version = int(row[0])
    if user_version > db_module.SCHEMA_VERSION:
        msg = (
            "Imported database schema version is newer than this toktrail build: "
            f"{user_version} > {db_module.SCHEMA_VERSION}"
        )
        raise ValueError(msg)


def _merge_machines(
    target: sqlite3.Connection,
    imported: sqlite3.Connection,
    *,
    imported_machine_id: str,
    imported_machine_name: str | None,
    imported_at_ms: int,
) -> None:
    imported_rows = imported.execute(
        """
        SELECT
            machine_id,
            name,
            last_seen_ms
        FROM machines
        ORDER BY machine_id
        """
    ).fetchall()
    for row in imported_rows:
        db_module.upsert_machine(
            target,
            machine_id=str(row["machine_id"]),
            name=str(row["name"]) if row["name"] is not None else None,
            seen_ms=int(row["last_seen_ms"]),
            is_local=False,
            imported_at_ms=imported_at_ms,
        )
    db_module.upsert_machine(
        target,
        machine_id=imported_machine_id,
        name=imported_machine_name,
        seen_ms=imported_at_ms,
        is_local=False,
        imported_at_ms=imported_at_ms,
    )
    local_machine_id = db_module.get_local_machine_id(target)
    db_module.upsert_machine(
        target,
        machine_id=local_machine_id,
        name=None,
        seen_ms=_now_ms(),
        is_local=True,
    )


def _merge_runs(
    target: sqlite3.Connection,
    imported: sqlite3.Connection,
    *,
    imported_machine_id: str,
    imported_at_ms: int,
    remote_active: RemoteActiveMode,
) -> tuple[dict[int, int], int, int]:
    imported_rows = imported.execute(
        """
        SELECT id, sync_id, origin_machine_id, name, started_at_ms, ended_at_ms,
               scope_harnesses_json, scope_provider_ids_json,
               scope_model_ids_json, scope_source_session_ids_json,
               scope_thinking_levels_json, scope_agents_json,
               archived_at_ms, created_at_ms, updated_at_ms, imported_at_ms
        FROM runs
        ORDER BY id
        """
    ).fetchall()
    local_active_sync_ids = {
        str(row["sync_id"])
        for row in target.execute(
            "SELECT sync_id FROM runs WHERE ended_at_ms IS NULL AND sync_id IS NOT NULL"
        ).fetchall()
    }
    if remote_active == "fail" and local_active_sync_ids:
        for imported_row in imported_rows:
            if imported_row["ended_at_ms"] is not None:
                continue
            imported_sync_id = str(imported_row["sync_id"])
            if imported_sync_id not in local_active_sync_ids:
                msg = (
                    "Imported archive includes an active run that conflicts "
                    "with a local "
                    "active run. Use --remote-active close-at-export or "
                    "--remote-active keep."
                )
                raise ValueError(msg)

    inserted = 0
    updated = 0
    run_id_map: dict[int, int] = {}
    for imported_row in imported_rows:
        imported_sync_id = str(imported_row["sync_id"])
        imported_ended = _optional_int_value(imported_row["ended_at_ms"])
        if imported_ended is None and remote_active == "close-at-export":
            imported_ended = imported_at_ms

        local_row = target.execute(
            """
            SELECT
                id,
                origin_machine_id,
                name,
                started_at_ms,
                ended_at_ms,
                scope_harnesses_json,
                scope_provider_ids_json,
                scope_model_ids_json,
                scope_source_session_ids_json,
                scope_thinking_levels_json,
                scope_agents_json,
                archived_at_ms,
                updated_at_ms,
                imported_at_ms
            FROM runs
            WHERE sync_id = ?
            """,
            (imported_sync_id,),
        ).fetchone()
        if local_row is None:
            new_origin_machine_id = _coalesce_text(
                imported_row["origin_machine_id"],
                imported_machine_id,
            )
            if new_origin_machine_id is None:
                msg = "Imported run origin_machine_id is missing."
                raise ValueError(msg)
            db_module.upsert_machine(
                target,
                machine_id=new_origin_machine_id,
                name=None,
                seen_ms=int(imported_row["updated_at_ms"]),
                is_local=False,
                imported_at_ms=imported_at_ms,
            )
            cursor = target.execute(
                """
                INSERT INTO runs (
                    sync_id,
                    origin_machine_id,
                    name,
                    started_at_ms,
                    ended_at_ms,
                    scope_harnesses_json,
                    scope_provider_ids_json,
                    scope_model_ids_json,
                    scope_source_session_ids_json,
                    scope_thinking_levels_json,
                    scope_agents_json,
                    archived_at_ms,
                    created_at_ms,
                    updated_at_ms,
                    imported_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    imported_sync_id,
                    new_origin_machine_id,
                    imported_row["name"],
                    int(imported_row["started_at_ms"]),
                    imported_ended,
                    _coalesce_text(imported_row["scope_harnesses_json"], "[]"),
                    _coalesce_text(imported_row["scope_provider_ids_json"], "[]"),
                    _coalesce_text(imported_row["scope_model_ids_json"], "[]"),
                    _coalesce_text(
                        imported_row["scope_source_session_ids_json"],
                        "[]",
                    ),
                    _coalesce_text(imported_row["scope_thinking_levels_json"], "[]"),
                    _coalesce_text(imported_row["scope_agents_json"], "[]"),
                    _optional_int_value(imported_row["archived_at_ms"]),
                    int(imported_row["created_at_ms"]),
                    int(imported_row["updated_at_ms"]),
                    imported_at_ms,
                ),
            )
            run_id_map[int(imported_row["id"])] = _required_lastrowid(cursor)
            inserted += 1
            continue

        local_name = (
            str(local_row["name"]).strip()
            if local_row["name"] is not None and str(local_row["name"]).strip()
            else None
        )
        merged_name = local_name or _coalesce_text(imported_row["name"], None)
        merged_started = min(
            int(local_row["started_at_ms"]),
            int(imported_row["started_at_ms"]),
        )
        merged_ended = _merge_ended_at(
            _optional_int_value(local_row["ended_at_ms"]),
            imported_ended,
        )
        merged_updated = max(
            int(local_row["updated_at_ms"]),
            int(imported_row["updated_at_ms"]),
        )
        merged_origin = _coalesce_text(
            local_row["origin_machine_id"],
            _coalesce_text(imported_row["origin_machine_id"], imported_machine_id),
        )
        if merged_origin is None:
            msg = "Merged run origin_machine_id is missing."
            raise ValueError(msg)
        db_module.upsert_machine(
            target,
            machine_id=merged_origin,
            name=None,
            seen_ms=max(
                int(local_row["updated_at_ms"]),
                int(imported_row["updated_at_ms"]),
            ),
            is_local=False,
            imported_at_ms=imported_at_ms,
        )
        merged_scope_harnesses_json = _merge_scope_json(
            local_row["scope_harnesses_json"],
            imported_row["scope_harnesses_json"],
        )
        merged_scope_provider_json = _merge_scope_json(
            local_row["scope_provider_ids_json"],
            imported_row["scope_provider_ids_json"],
        )
        merged_scope_model_json = _merge_scope_json(
            local_row["scope_model_ids_json"],
            imported_row["scope_model_ids_json"],
        )
        merged_scope_source_session_json = _merge_scope_json(
            local_row["scope_source_session_ids_json"],
            imported_row["scope_source_session_ids_json"],
        )
        merged_scope_thinking_json = _merge_scope_json(
            local_row["scope_thinking_levels_json"],
            imported_row["scope_thinking_levels_json"],
        )
        merged_scope_agents_json = _merge_scope_json(
            local_row["scope_agents_json"],
            imported_row["scope_agents_json"],
        )
        merged_archived_at = _max_optional_int_value(
            _optional_int_value(local_row["archived_at_ms"]),
            _optional_int_value(imported_row["archived_at_ms"]),
        )
        merged_imported_at = _max_optional_int_value(
            _optional_int_value(local_row["imported_at_ms"]),
            imported_at_ms,
        )
        target.execute(
            """
            UPDATE runs
            SET origin_machine_id = ?,
                name = ?,
                started_at_ms = ?,
                ended_at_ms = ?,
                scope_harnesses_json = ?,
                scope_provider_ids_json = ?,
                scope_model_ids_json = ?,
                scope_source_session_ids_json = ?,
                scope_thinking_levels_json = ?,
                scope_agents_json = ?,
                archived_at_ms = ?,
                updated_at_ms = ?,
                imported_at_ms = ?
            WHERE id = ?
            """,
            (
                merged_origin,
                merged_name,
                merged_started,
                merged_ended,
                merged_scope_harnesses_json,
                merged_scope_provider_json,
                merged_scope_model_json,
                merged_scope_source_session_json,
                merged_scope_thinking_json,
                merged_scope_agents_json,
                merged_archived_at,
                merged_updated,
                merged_imported_at,
                int(local_row["id"]),
            ),
        )
        run_id_map[int(imported_row["id"])] = int(local_row["id"])
        updated += 1

    return run_id_map, inserted, updated


def _merge_source_sessions(
    target: sqlite3.Connection,
    imported: sqlite3.Connection,
    *,
    run_id_map: dict[int, int],
) -> tuple[dict[int, int], int, int]:
    inserted = 0
    updated = 0
    source_id_map: dict[int, int] = {}
    rows = imported.execute(
        """
        SELECT id, sync_id, tracking_session_id, harness, source_session_id,
               first_seen_ms, last_seen_ms, created_at_ms, updated_at_ms
        FROM source_sessions
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        imported_tracking_id = int(row["tracking_session_id"])
        mapped_tracking_id = run_id_map[imported_tracking_id]
        sync_id = str(row["sync_id"])
        local = target.execute(
            """
            SELECT id, first_seen_ms, last_seen_ms, updated_at_ms
            FROM source_sessions
            WHERE sync_id = ?
            """,
            (sync_id,),
        ).fetchone()
        if local is None:
            cursor = target.execute(
                """
                INSERT INTO source_sessions (
                    sync_id,
                    tracking_session_id,
                    harness,
                    source_session_id,
                    first_seen_ms,
                    last_seen_ms,
                    created_at_ms,
                    updated_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sync_id,
                    mapped_tracking_id,
                    str(row["harness"]),
                    str(row["source_session_id"]),
                    row["first_seen_ms"],
                    row["last_seen_ms"],
                    int(row["created_at_ms"]),
                    int(row["updated_at_ms"]),
                ),
            )
            source_id_map[int(row["id"])] = _required_lastrowid(cursor)
            inserted += 1
            continue

        merged_first = _min_optional_int_value(
            _optional_int_value(local["first_seen_ms"]),
            _optional_int_value(row["first_seen_ms"]),
        )
        merged_last = _max_optional_int_value(
            _optional_int_value(local["last_seen_ms"]),
            _optional_int_value(row["last_seen_ms"]),
        )
        merged_updated = max(
            int(local["updated_at_ms"]),
            int(row["updated_at_ms"]),
        )
        target.execute(
            """
            UPDATE source_sessions
            SET first_seen_ms = ?, last_seen_ms = ?, updated_at_ms = ?
            WHERE id = ?
            """,
            (merged_first, merged_last, merged_updated, int(local["id"])),
        )
        source_id_map[int(row["id"])] = int(local["id"])
        updated += 1
    return source_id_map, inserted, updated


def _merge_usage_events(
    target: sqlite3.Connection,
    imported: sqlite3.Connection,
    *,
    run_id_map: dict[int, int],
    source_session_id_map: dict[int, int],
    imported_machine_id: str,
    on_conflict: ConflictMode,
) -> tuple[dict[int, int], int, int, tuple[StateImportConflict, ...]]:
    inserted = 0
    skipped = 0
    usage_id_map: dict[int, int] = {}
    conflicts: list[StateImportConflict] = []
    rows = imported.execute(
        """
        SELECT id, tracking_session_id, harness_session_id, harness, source_session_id,
               source_row_id, source_message_id, source_dedup_key, global_dedup_key,
               fingerprint_hash, origin_machine_id,
               role, provider_id, model_id, thinking_level, agent,
               created_ms, completed_ms, input_tokens, output_tokens, reasoning_tokens,
               cache_read_tokens, cache_write_tokens, cache_output_tokens,
               source_cost_usd, raw_json, imported_at_ms
        FROM usage_events
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        harness = str(row["harness"])
        dedup_key = str(row["global_dedup_key"])
        local = target.execute(
            """
            SELECT id, fingerprint_hash
            FROM usage_events
            WHERE harness = ? AND global_dedup_key = ?
            """,
            (harness, dedup_key),
        ).fetchone()
        if local is not None:
            existing_origin_machine_id = _coalesce_text(
                row["origin_machine_id"],
                imported_machine_id,
            )
            if existing_origin_machine_id is not None:
                db_module.upsert_machine(
                    target,
                    machine_id=existing_origin_machine_id,
                    name=None,
                    seen_ms=int(row["created_ms"]),
                    is_local=False,
                )
            local_id = int(local["id"])
            if str(local["fingerprint_hash"]) != str(row["fingerprint_hash"]):
                conflict = StateImportConflict(
                    kind="usage_event_fingerprint_conflict",
                    harness=harness,
                    global_dedup_key=dedup_key,
                    local_fingerprint=str(local["fingerprint_hash"]),
                    imported_fingerprint=str(row["fingerprint_hash"]),
                    message=(
                        "Fingerprint mismatch for duplicate usage event "
                        f"{harness}:{dedup_key}."
                    ),
                )
                conflicts.append(conflict)
                if on_conflict == "fail":
                    raise _ImportConflictError(tuple(conflicts))
            usage_id_map[int(row["id"])] = local_id
            skipped += 1
            continue

        mapped_tracking = _map_nullable_id(run_id_map, row["tracking_session_id"])
        mapped_harness_session = _map_nullable_id(
            source_session_id_map,
            row["harness_session_id"],
        )
        event_origin_machine_id = _coalesce_text(
            row["origin_machine_id"],
            imported_machine_id,
        )
        if event_origin_machine_id is None:
            msg = "Usage event origin_machine_id is missing."
            raise ValueError(msg)
        db_module.upsert_machine(
            target,
            machine_id=event_origin_machine_id,
            name=None,
            seen_ms=int(row["created_ms"]),
            is_local=False,
        )
        cursor = target.execute(
            """
            INSERT INTO usage_events (
                tracking_session_id,
                harness_session_id,
                harness,
                source_session_id,
                source_row_id,
                source_message_id,
                source_dedup_key,
                global_dedup_key,
                fingerprint_hash,
                origin_machine_id,
                role,
                provider_id,
                model_id,
                thinking_level,
                agent,
                created_ms,
                completed_ms,
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cache_output_tokens,
                source_cost_usd,
                raw_json,
                imported_at_ms
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                mapped_tracking,
                mapped_harness_session,
                harness,
                str(row["source_session_id"]),
                row["source_row_id"],
                row["source_message_id"],
                row["source_dedup_key"],
                row["global_dedup_key"],
                row["fingerprint_hash"],
                event_origin_machine_id,
                row["role"],
                row["provider_id"],
                row["model_id"],
                row["thinking_level"],
                row["agent"],
                int(row["created_ms"]),
                row["completed_ms"],
                int(row["input_tokens"]),
                int(row["output_tokens"]),
                int(row["reasoning_tokens"]),
                int(row["cache_read_tokens"]),
                int(row["cache_write_tokens"]),
                int(row["cache_output_tokens"]),
                row["source_cost_usd"],
                row["raw_json"],
                int(row["imported_at_ms"]),
            ),
        )
        usage_id_map[int(row["id"])] = _required_lastrowid(cursor)
        inserted += 1

    return usage_id_map, inserted, skipped, tuple(conflicts)


def _merge_run_events(
    target: sqlite3.Connection,
    imported: sqlite3.Connection,
    *,
    run_id_map: dict[int, int],
    usage_event_id_map: dict[int, int],
) -> int:
    inserted = 0
    rows = imported.execute(
        """
        SELECT tracking_session_id, usage_event_id, created_at_ms
        FROM run_events
        """
    ).fetchall()
    for row in rows:
        imported_tracking_id = int(row["tracking_session_id"])
        imported_usage_event_id = int(row["usage_event_id"])
        mapped_tracking_id = run_id_map[imported_tracking_id]
        mapped_usage_event_id = usage_event_id_map[imported_usage_event_id]
        cursor = target.execute(
            """
            INSERT OR IGNORE INTO run_events (
                tracking_session_id,
                usage_event_id,
                created_at_ms
            )
            VALUES (?, ?, ?)
            """,
            (
                mapped_tracking_id,
                mapped_usage_event_id,
                int(row["created_at_ms"]),
            ),
        )
        inserted += cursor.rowcount
    return inserted


def _required_str(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"Manifest field {key!r} must be a non-empty string."
        raise ValueError(msg)
    return value


def _required_int(mapping: dict[str, object], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"Manifest field {key!r} must be an integer."
        raise ValueError(msg)
    return value


def _required_dict(mapping: dict[str, object], key: str) -> dict[str, object]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        msg = f"Manifest field {key!r} must be an object."
        raise ValueError(msg)
    return value


def _optional_manifest_str(mapping: dict[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"Manifest field {key!r} must be a string when present."
        raise ValueError(msg)
    stripped = value.strip()
    return stripped or None


def _coalesce_text(value: object, fallback: str | None) -> str | None:
    if value is None:
        return fallback
    return str(value)


def _optional_int_value(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"Expected nullable integer value, got {value!r}"
        raise ValueError(msg)
    return value


def _scope_json_has_values(value: object) -> bool:
    if value is None:
        return False
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, list) and len(parsed) > 0


def _merge_scope_json(local_value: object, imported_value: object) -> str:
    local_text = _coalesce_text(local_value, "[]") or "[]"
    imported_text = _coalesce_text(imported_value, "[]") or "[]"
    if _scope_json_has_values(local_text):
        return local_text
    return imported_text


def _min_optional_int_value(left: int | None, right: int | None) -> int | None:
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _max_optional_int_value(left: int | None, right: int | None) -> int | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def _merge_ended_at(local: int | None, imported: int | None) -> int | None:
    if local is None:
        return imported
    if imported is None:
        return local
    return max(local, imported)


def _map_nullable_id(id_map: dict[int, int], value: object) -> int | None:
    mapped_input = _optional_int_value(value)
    if mapped_input is None:
        return None
    mapped = id_map.get(mapped_input)
    if mapped is None:
        msg = f"Imported row references unknown id mapping: {mapped_input}"
        raise ValueError(msg)
    return mapped


def _required_lastrowid(cursor: sqlite3.Cursor) -> int:
    rowid = cursor.lastrowid
    if rowid is None:
        msg = "SQLite insert did not provide a rowid."
        raise ValueError(msg)
    return int(rowid)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _now_ms() -> int:
    return int(time() * 1000)
