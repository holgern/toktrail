from __future__ import annotations

import json
import socket
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import time
from typing import Any, cast

from toktrail.adapters.base import ImportSourceState
from toktrail.config import (
    CostingConfig,
    MachineConfig,
    SubscriptionConfig,
    SubscriptionWindowConfig,
    default_costing_config,
    normalize_identity,
)
from toktrail.costing import (
    CostBreakdown,
    CostingRuntime,
    SimulationTarget,
    UsageCostAtom,
    compile_costing_config,
    resolve_price_resolution,
    simulate_cost,
)
from toktrail.models import Run as TrackingSession
from toktrail.models import (
    RunScope,
    TokenBreakdown,
    UsageEvent,
    normalize_run_scope,
    normalize_thinking_level,
)
from toktrail.reporting import (
    ActivitySummaryRow,
    AreaSummaryRow,
    CostTotals,
    HarnessSummaryRow,
    MachineSummaryRow,
    ModelSummaryRow,
    ProviderSummaryRow,
    RunReport,
    SessionTotals,
    SimulationSummaryRow,
    SubscriptionBillingPeriod,
    SubscriptionUsagePeriod,
    SubscriptionUsageReport,
    SubscriptionUsageRow,
    UnconfiguredModelRow,
    UsageAreasReport,
    UsageReportFilter,
    UsageRunRow,
    UsageRunsFilter,
    UsageRunsReport,
    UsageSeriesBucket,
    UsageSeriesFilter,
    UsageSeriesInstance,
    UsageSeriesReport,
    UsageSessionRow,
    UsageSessionsFilter,
    UsageSessionsReport,
)

SCHEMA_VERSION = 11
_PERIOD_SORT: dict[str, int] = {
    "5h": 0,
    "daily": 1,
    "weekly": 2,
    "monthly": 3,
    "yearly": 4,
}
_STATE_METADATA_MACHINE_ID_KEY = "machine_id"
_STATE_METADATA_CREATED_AT_MS_KEY = "created_at_ms"


@dataclass(frozen=True)
class InsertUsageResult:
    rows_inserted: int
    rows_linked: int = 0
    rows_skipped: int = 0
    rows_scope_excluded: int = 0


@dataclass(frozen=True)
class Machine:
    machine_id: str
    name: str | None
    name_key: str | None
    first_seen_ms: int
    last_seen_ms: int
    is_local: bool
    created_at_ms: int
    updated_at_ms: int
    imported_at_ms: int | None = None

    @property
    def label(self) -> str:
        return self.name or f"machine:{self.machine_id[:8]}"


@dataclass(frozen=True)
class Area:
    id: int
    sync_id: str
    parent_id: int | None
    slug: str
    name: str
    path: str
    archived_at_ms: int | None
    created_at_ms: int
    updated_at_ms: int
    imported_at_ms: int | None = None


@dataclass(frozen=True)
class AreaSessionAssignment:
    id: int
    sync_id: str
    area_id: int
    origin_machine_id: str
    harness: str
    source_session_id: str
    assigned_at_ms: int
    updated_at_ms: int
    imported_at_ms: int | None = None


@dataclass(frozen=True)
class _UsageWhere:
    source_clause: str
    where_clause: str
    params: tuple[object, ...]


@dataclass(frozen=True)
class _AggregateRow:
    group: tuple[object, ...]
    area_id: int | None
    origin_machine_id: str | None
    harness: str
    source_session_id: str
    provider_id: str
    model_id: str
    thinking_level: str | None
    agent: str | None
    context_tokens: int
    message_count: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cache_output_tokens: int
    source_cost_usd: Decimal
    first_created_ms: int | None = None
    last_created_ms: int | None = None

    @property
    def tokens(self) -> TokenBreakdown:
        return TokenBreakdown(
            input=self.input_tokens,
            output=self.output_tokens,
            reasoning=self.reasoning_tokens,
            cache_read=self.cache_read_tokens,
            cache_write=self.cache_write_tokens,
            cache_output=self.cache_output_tokens,
        )


def _now_ms() -> int:
    return int(time() * 1000)


def _machine_name_key(name: str | None) -> str | None:
    if name is None:
        return None
    return normalize_identity(name)


def _validate_machine_name(name: str | None) -> str | None:
    if name is None:
        return None
    normalized = name.strip()
    if not normalized:
        return None
    if len(normalized) > 80:
        msg = "Machine name must be at most 80 characters."
        raise ValueError(msg)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        msg = "Machine name cannot contain control characters."
        raise ValueError(msg)
    return normalized


def default_hostname_name() -> str | None:
    raw = socket.gethostname().strip()
    if not raw:
        return None
    try:
        _machine_name_key(raw)
    except ValueError:
        return None
    return raw


def _json_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        msg = "Run scope JSON must be a list."
        raise ValueError(msg)
    return tuple(str(item) for item in parsed)


def _dump_json_tuple(values: tuple[str, ...]) -> str:
    return json.dumps(list(values), separators=(",", ":"), sort_keys=False)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.create_aggregate("DECIMAL_SUM", 1, cast(Any, _new_decimal_sum))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _new_decimal_sum() -> _DecimalSum:
    return _DecimalSum()


def migrate(conn: sqlite3.Connection) -> None:
    current_version = _read_user_version(conn)
    if current_version == 0:
        _create_schema(conn)
        _ensure_machine_id(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return

    if current_version < 1 or current_version > SCHEMA_VERSION:
        msg = (
            f"Unsupported pre-release toktrail schema version {current_version}; "
            "delete the state DB or export/import manually before first release."
        )
        raise ValueError(msg)

    if current_version == 1:
        _migrate_v1_to_v2(conn)
        current_version = 2
    if current_version == 2:
        _migrate_v2_to_v3(conn)
        current_version = 3
    if current_version == 3:
        _migrate_v3_to_v4(conn)
        current_version = 4
    if current_version == 4:
        _migrate_v4_to_v5(conn)
        current_version = 5
    if current_version == 5:
        _migrate_v5_to_v6(conn)
        current_version = 6
    if current_version == 6:
        _migrate_v6_to_v7(conn)
        current_version = 7
    if current_version == 7:
        _migrate_v7_to_v8(conn)
        current_version = 8
    if current_version == 8:
        _migrate_v8_to_v9(conn)
        current_version = 9
    if current_version == 9:
        _migrate_v9_to_v10(conn)
        current_version = 10
    if current_version == 10:
        _migrate_v10_to_v11(conn)
        current_version = 11

    _ensure_machine_id(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _read_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    if row is None:
        msg = "Could not read SQLite user_version."
        raise ValueError(msg)
    return _required_int(row[0])


def _table_has_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row["name"]) == column_name for row in rows)


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_type_sql: str,
) -> None:
    if _table_has_column(conn, table_name, column_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type_sql}")


def _read_state_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        """
        SELECT value
        FROM state_metadata
        WHERE key = ?
        """,
        (key,),
    ).fetchone()
    if row is None:
        return None
    return str(row["value"])


def _write_state_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO state_metadata (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def get_state_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    return _read_state_metadata(conn, key)


def set_state_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    _write_state_metadata(conn, key, value)


def upsert_skipped_source(
    conn: sqlite3.Connection,
    *,
    harness: str,
    source_path: str,
    reason: str,
    source_session_key: str = "",
    fingerprint_size: int | None = None,
    fingerprint_mtime_ns: int | None = None,
    updated_at_ms: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO skipped_sources (
            harness, source_path, source_session_key, reason,
            fingerprint_size, fingerprint_mtime_ns, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(harness, source_path, source_session_key) DO UPDATE SET
            reason = excluded.reason,
            fingerprint_size = excluded.fingerprint_size,
            fingerprint_mtime_ns = excluded.fingerprint_mtime_ns,
            updated_at_ms = excluded.updated_at_ms
        """,
        (
            harness,
            source_path,
            source_session_key,
            reason,
            fingerprint_size,
            fingerprint_mtime_ns,
            updated_at_ms if updated_at_ms is not None else _now_ms(),
        ),
    )


def list_skipped_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT harness, source_path, source_session_key, reason,
                   fingerprint_size, fingerprint_mtime_ns, updated_at_ms
            FROM skipped_sources
            ORDER BY updated_at_ms DESC, harness, source_path
            """
        )
    )


def clear_skipped_sources(conn: sqlite3.Connection, harness: str | None = None) -> int:
    if harness is None:
        cursor = conn.execute("DELETE FROM skipped_sources")
    else:
        cursor = conn.execute(
            "DELETE FROM skipped_sources WHERE harness = ?",
            (harness,),
        )
    return int(cursor.rowcount)


def _ensure_machine_id(conn: sqlite3.Connection) -> str:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS state_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    machine_id = _read_state_metadata(conn, _STATE_METADATA_MACHINE_ID_KEY)
    if machine_id is None:
        machine_id = uuid.uuid4().hex
        _write_state_metadata(conn, _STATE_METADATA_MACHINE_ID_KEY, machine_id)
    created_at_ms = _read_state_metadata(conn, _STATE_METADATA_CREATED_AT_MS_KEY)
    if created_at_ms is None:
        created_at_ms = str(_now_ms())
        _write_state_metadata(conn, _STATE_METADATA_CREATED_AT_MS_KEY, created_at_ms)
    try:
        machine_seen_ms = int(created_at_ms)
    except ValueError:
        machine_seen_ms = _now_ms()
    _create_machines_table(conn)
    upsert_machine(
        conn,
        machine_id=machine_id,
        name=None,
        seen_ms=machine_seen_ms,
        is_local=True,
    )
    return machine_id


def _build_source_session_sync_id(
    *,
    run_sync_id: str,
    harness: str,
    source_session_id: str,
) -> str:
    payload = f"{run_sync_id}\0{harness}\0{source_session_id}".encode()
    return sha256(payload).hexdigest()


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT NOT NULL UNIQUE,
            origin_machine_id TEXT,
            name TEXT,
            started_at_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            scope_harnesses_json TEXT NOT NULL DEFAULT '[]',
            scope_provider_ids_json TEXT NOT NULL DEFAULT '[]',
            scope_model_ids_json TEXT NOT NULL DEFAULT '[]',
            scope_source_session_ids_json TEXT NOT NULL DEFAULT '[]',
            scope_thinking_levels_json TEXT NOT NULL DEFAULT '[]',
            scope_agents_json TEXT NOT NULL DEFAULT '[]',
            archived_at_ms INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_runs_started
        ON runs(started_at_ms);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_sync_id
        ON runs(sync_id);

        CREATE INDEX IF NOT EXISTS idx_runs_archived_started
        ON runs(archived_at_ms, started_at_ms);

        CREATE INDEX IF NOT EXISTS idx_runs_active_archived_started
        ON runs(ended_at_ms, archived_at_ms, started_at_ms);

        CREATE TABLE IF NOT EXISTS machines (
            machine_id TEXT PRIMARY KEY,
            name TEXT,
            name_key TEXT,
            first_seen_ms INTEGER NOT NULL,
            last_seen_ms INTEGER NOT NULL,
            is_local INTEGER NOT NULL DEFAULT 0,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_machines_name_key
        ON machines(name_key);

        CREATE INDEX IF NOT EXISTS idx_machines_last_seen
        ON machines(last_seen_ms);

        CREATE TABLE IF NOT EXISTS areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT NOT NULL UNIQUE,
            parent_id INTEGER REFERENCES areas(id) ON DELETE RESTRICT,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            archived_at_ms INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_areas_parent_slug
        ON areas(parent_id, slug);

        CREATE INDEX IF NOT EXISTS idx_areas_path
        ON areas(path);

        CREATE TABLE IF NOT EXISTS area_session_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT NOT NULL UNIQUE,
            area_id INTEGER NOT NULL REFERENCES areas(id) ON DELETE RESTRICT,
            origin_machine_id TEXT NOT NULL,
            harness TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            assigned_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER,
            UNIQUE(origin_machine_id, harness, source_session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_area_session_lookup
        ON area_session_assignments(origin_machine_id, harness, source_session_id);

        CREATE INDEX IF NOT EXISTS idx_area_session_area
        ON area_session_assignments(area_id);

        CREATE TABLE IF NOT EXISTS machine_active_areas (
            machine_id TEXT PRIMARY KEY
                REFERENCES machines(machine_id) ON DELETE CASCADE,
            area_id INTEGER REFERENCES areas(id) ON DELETE SET NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER
        );

        CREATE TABLE IF NOT EXISTS source_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT NOT NULL UNIQUE,
            tracking_session_id INTEGER NOT NULL
                REFERENCES runs(id) ON DELETE CASCADE,
            harness TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            first_seen_ms INTEGER,
            last_seen_ms INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            UNIQUE(tracking_session_id, harness, source_session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_sessions_lookup
        ON source_sessions(harness, source_session_id);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_sessions_sync_id
        ON source_sessions(sync_id);

        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_session_id INTEGER
                REFERENCES runs(id) ON DELETE SET NULL,
            harness_session_id INTEGER
                REFERENCES source_sessions(id) ON DELETE SET NULL,
            harness TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            source_row_id TEXT,
            source_message_id TEXT,
            source_dedup_key TEXT,
            global_dedup_key TEXT,
            fingerprint_hash TEXT NOT NULL,
            origin_machine_id TEXT,
            area_id INTEGER REFERENCES areas(id) ON DELETE SET NULL,
            role TEXT NOT NULL DEFAULT 'assistant',
            provider_id TEXT NOT NULL DEFAULT 'unknown',
            provider_key TEXT NOT NULL DEFAULT 'unknown',
            model_id TEXT NOT NULL,
            model_key TEXT NOT NULL DEFAULT '',
            thinking_level TEXT,
            agent TEXT,
            agent_key TEXT,
            created_ms INTEGER NOT NULL,
            completed_ms INTEGER,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            cache_output_tokens INTEGER NOT NULL DEFAULT 0,
            source_cost_usd TEXT NOT NULL DEFAULT '0',
            raw_json TEXT,
            imported_at_ms INTEGER NOT NULL,
            UNIQUE(harness, global_dedup_key)
        );

        CREATE INDEX IF NOT EXISTS idx_usage_events_tracking_session
        ON usage_events(tracking_session_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_harness_session
        ON usage_events(harness, source_session_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_origin_machine_created
        ON usage_events(origin_machine_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_area_created
        ON usage_events(area_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_fingerprint
        ON usage_events(harness, fingerprint_hash);

        CREATE INDEX IF NOT EXISTS idx_usage_events_model_thinking
        ON usage_events(provider_id, model_id, thinking_level);

        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_created
        ON usage_events(provider_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_created
        ON usage_events(created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_harness_created
        ON usage_events(harness, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_source_session_created
        ON usage_events(source_session_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_model_created
        ON usage_events(provider_id, model_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_key_created
        ON usage_events(provider_key, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_model_key_created
        ON usage_events(provider_key, model_key, created_ms);

        CREATE TABLE IF NOT EXISTS run_events (
            tracking_session_id INTEGER NOT NULL
                REFERENCES runs(id) ON DELETE CASCADE,
            usage_event_id INTEGER NOT NULL
                REFERENCES usage_events(id) ON DELETE CASCADE,
            created_at_ms INTEGER NOT NULL,
            PRIMARY KEY (tracking_session_id, usage_event_id)
        );

        CREATE INDEX IF NOT EXISTS idx_run_events_usage
        ON run_events(usage_event_id);

        CREATE TABLE IF NOT EXISTS state_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS import_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            harness TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_session_key TEXT NOT NULL DEFAULT '',
            fingerprint_size INTEGER,
            fingerprint_mtime_ns INTEGER,
            fingerprint_inode INTEGER,
            sqlite_page_count INTEGER,
            sqlite_schema_version INTEGER,
            last_imported_created_ms INTEGER,
            last_seen_rowid INTEGER,
            last_file_offset INTEGER,
            updated_at_ms INTEGER NOT NULL,
            UNIQUE(harness, source_path, source_session_key)
        );

        CREATE INDEX IF NOT EXISTS idx_import_sources_lookup
        ON import_sources(harness, source_path, source_session_key);


        CREATE TABLE IF NOT EXISTS skipped_sources (
            harness TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_session_key TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL,
            fingerprint_size INTEGER,
            fingerprint_mtime_ns INTEGER,
            updated_at_ms INTEGER NOT NULL,
            PRIMARY KEY (harness, source_path, source_session_key)
        );

        CREATE TABLE IF NOT EXISTS sync_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_key TEXT NOT NULL UNIQUE,
            archive_sha256 TEXT NOT NULL,
            source_machine_id TEXT,
            exported_at_ms INTEGER,
            archive_path TEXT,
            imported_at_ms INTEGER NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_sync_imports_exported
        ON sync_imports(exported_at_ms);
        """
    )


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    _create_schema(conn)


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_created
        ON usage_events(provider_id, created_ms);
        """
    )


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(
        conn,
        "usage_events",
        "cache_output_tokens",
        "INTEGER NOT NULL DEFAULT 0",
    )


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS state_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    _add_column_if_missing(conn, "runs", "sync_id", "TEXT")
    _add_column_if_missing(conn, "runs", "origin_machine_id", "TEXT")
    _add_column_if_missing(conn, "runs", "imported_at_ms", "INTEGER")
    _add_column_if_missing(conn, "source_sessions", "sync_id", "TEXT")

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_sync_id
        ON runs(sync_id)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_sessions_sync_id
        ON source_sessions(sync_id)
        """
    )

    machine_id = _ensure_machine_id(conn)
    run_rows = conn.execute(
        """
        SELECT id, sync_id, origin_machine_id
        FROM runs
        """
    ).fetchall()
    for row in run_rows:
        sync_id = (
            str(row["sync_id"]) if row["sync_id"] is not None else uuid.uuid4().hex
        )
        origin_machine_id = (
            str(row["origin_machine_id"])
            if row["origin_machine_id"] is not None
            else machine_id
        )
        conn.execute(
            """
            UPDATE runs
            SET sync_id = ?, origin_machine_id = ?
            WHERE id = ?
            """,
            (sync_id, origin_machine_id, _required_int(row["id"])),
        )

    source_rows = conn.execute(
        """
        SELECT
            ss.id,
            ss.sync_id,
            ss.harness,
            ss.source_session_id,
            r.sync_id AS run_sync_id
        FROM source_sessions AS ss
        JOIN runs AS r ON r.id = ss.tracking_session_id
        """
    ).fetchall()
    for row in source_rows:
        if row["sync_id"] is not None:
            continue
        run_sync_id = str(row["run_sync_id"])
        sync_id = _build_source_session_sync_id(
            run_sync_id=run_sync_id,
            harness=str(row["harness"]),
            source_session_id=str(row["source_session_id"]),
        )
        conn.execute(
            """
            UPDATE source_sessions
            SET sync_id = ?
            WHERE id = ?
            """,
            (sync_id, _required_int(row["id"])),
        )


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(
        conn,
        "usage_events",
        "provider_key",
        "TEXT NOT NULL DEFAULT 'unknown'",
    )
    _add_column_if_missing(
        conn,
        "usage_events",
        "model_key",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(conn, "usage_events", "agent_key", "TEXT")
    conn.execute(
        """
        UPDATE usage_events
        SET provider_key = LOWER(COALESCE(provider_id, 'unknown')),
            model_key = LOWER(COALESCE(model_id, '')),
            agent_key = LOWER(COALESCE(agent, ''))
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_created
        ON usage_events(created_ms)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_harness_created
        ON usage_events(harness, created_ms)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_source_session_created
        ON usage_events(source_session_id, created_ms)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_model_created
        ON usage_events(provider_id, model_id, created_ms)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_key_created
        ON usage_events(provider_key, created_ms)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_model_key_created
        ON usage_events(provider_key, model_key, created_ms)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS import_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            harness TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_session_key TEXT NOT NULL DEFAULT '',
            fingerprint_size INTEGER,
            fingerprint_mtime_ns INTEGER,
            fingerprint_inode INTEGER,
            sqlite_page_count INTEGER,
            sqlite_schema_version INTEGER,
            last_imported_created_ms INTEGER,
            last_seen_rowid INTEGER,
            last_file_offset INTEGER,
            updated_at_ms INTEGER NOT NULL,
            UNIQUE(harness, source_path, source_session_key)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_import_sources_lookup
        ON import_sources(harness, source_path, source_session_key)
        """
    )


def _create_skipped_sources_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skipped_sources (
            harness TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_session_key TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL,
            fingerprint_size INTEGER,
            fingerprint_mtime_ns INTEGER,
            updated_at_ms INTEGER NOT NULL,
            PRIMARY KEY (harness, source_path, source_session_key)
        )
        """
    )


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    _create_skipped_sources_table(conn)


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    _create_sync_imports_table(conn)


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    _create_machines_table(conn)
    _add_column_if_missing(conn, "usage_events", "origin_machine_id", "TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_origin_machine_created
        ON usage_events(origin_machine_id, created_ms)
        """
    )
    local_machine_id = _ensure_machine_id(conn)
    _backfill_usage_event_origin_machine_ids(conn, local_machine_id=local_machine_id)


def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    _create_areas_table(conn)
    _create_area_session_assignments_table(conn)
    _create_machine_active_areas_table(conn)
    _add_column_if_missing(conn, "usage_events", "area_id", "INTEGER")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_events_area_created
        ON usage_events(area_id, created_ms)
        """
    )


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(
        conn,
        "runs",
        "scope_harnesses_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        conn,
        "runs",
        "scope_provider_ids_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        conn,
        "runs",
        "scope_model_ids_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        conn,
        "runs",
        "scope_source_session_ids_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        conn,
        "runs",
        "scope_thinking_levels_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        conn,
        "runs",
        "scope_agents_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(conn, "runs", "archived_at_ms", "INTEGER")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runs_archived_started
        ON runs(archived_at_ms, started_at_ms)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runs_active_archived_started
        ON runs(ended_at_ms, archived_at_ms, started_at_ms)
        """
    )


def _create_sync_imports_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_key TEXT NOT NULL UNIQUE,
            archive_sha256 TEXT NOT NULL,
            source_machine_id TEXT,
            exported_at_ms INTEGER,
            archive_path TEXT,
            imported_at_ms INTEGER NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sync_imports_exported
        ON sync_imports(exported_at_ms)
        """
    )


def _create_machines_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS machines (
            machine_id TEXT PRIMARY KEY,
            name TEXT,
            name_key TEXT,
            first_seen_ms INTEGER NOT NULL,
            last_seen_ms INTEGER NOT NULL,
            is_local INTEGER NOT NULL DEFAULT 0,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_machines_name_key
        ON machines(name_key)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_machines_last_seen
        ON machines(last_seen_ms)
        """
    )


def _create_areas_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT NOT NULL UNIQUE,
            parent_id INTEGER REFERENCES areas(id) ON DELETE RESTRICT,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            archived_at_ms INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_areas_parent_slug
        ON areas(parent_id, slug)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_areas_path
        ON areas(path)
        """
    )


def _create_area_session_assignments_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS area_session_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_id TEXT NOT NULL UNIQUE,
            area_id INTEGER NOT NULL REFERENCES areas(id) ON DELETE RESTRICT,
            origin_machine_id TEXT NOT NULL,
            harness TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            assigned_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER,
            UNIQUE(origin_machine_id, harness, source_session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_area_session_lookup
        ON area_session_assignments(origin_machine_id, harness, source_session_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_area_session_area
        ON area_session_assignments(area_id)
        """
    )


def _create_machine_active_areas_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS machine_active_areas (
            machine_id TEXT PRIMARY KEY
                REFERENCES machines(machine_id) ON DELETE CASCADE,
            area_id INTEGER REFERENCES areas(id) ON DELETE SET NULL,
            updated_at_ms INTEGER NOT NULL,
            imported_at_ms INTEGER
        )
        """
    )


def _backfill_usage_event_origin_machine_ids(
    conn: sqlite3.Connection,
    *,
    local_machine_id: str,
) -> None:
    upsert_machine(
        conn,
        machine_id=local_machine_id,
        name=None,
        seen_ms=_now_ms(),
        is_local=True,
    )
    conn.execute(
        """
        UPDATE usage_events
        SET origin_machine_id = (
            SELECT r.origin_machine_id
            FROM run_events AS re
            JOIN runs AS r ON r.id = re.tracking_session_id
            WHERE re.usage_event_id = usage_events.id
              AND r.origin_machine_id IS NOT NULL
            ORDER BY r.started_at_ms DESC
            LIMIT 1
        )
        WHERE origin_machine_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM run_events AS re
              JOIN runs AS r ON r.id = re.tracking_session_id
              WHERE re.usage_event_id = usage_events.id
                AND r.origin_machine_id IS NOT NULL
          )
        """
    )
    conn.execute(
        """
        UPDATE usage_events
        SET origin_machine_id = ?
        WHERE origin_machine_id IS NULL
        """,
        (local_machine_id,),
    )
    machine_ids = conn.execute(
        """
        SELECT DISTINCT origin_machine_id
        FROM runs
        WHERE origin_machine_id IS NOT NULL
        UNION
        SELECT DISTINCT origin_machine_id
        FROM usage_events
        WHERE origin_machine_id IS NOT NULL
        """
    ).fetchall()
    for row in machine_ids:
        machine_id = str(row["origin_machine_id"])
        upsert_machine(
            conn,
            machine_id=machine_id,
            name=None,
            seen_ms=_now_ms(),
            is_local=(machine_id == local_machine_id),
        )


def has_imported_sync_archive(conn: sqlite3.Connection, archive_sha256: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sync_imports
        WHERE archive_key = ?
        LIMIT 1
        """,
        (archive_sha256,),
    ).fetchone()
    return row is not None


def record_imported_sync_archive(
    conn: sqlite3.Connection,
    *,
    archive_sha256: str,
    source_machine_id: str | None,
    exported_at_ms: int | None,
    archive_path: str,
    result_json: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sync_imports (
            archive_key,
            archive_sha256,
            source_machine_id,
            exported_at_ms,
            archive_path,
            imported_at_ms,
            dry_run,
            result_json
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(archive_key) DO NOTHING
        """,
        (
            archive_sha256,
            archive_sha256,
            source_machine_id,
            exported_at_ms,
            archive_path,
            _now_ms(),
            result_json,
        ),
    )


def create_tracking_session(
    conn: sqlite3.Connection,
    name: str | None,
    *,
    started_at_ms: int | None = None,
    scope: RunScope | None = None,
) -> int:
    active_session_id = get_active_tracking_session(conn)
    if active_session_id is not None:
        msg = f"Tracking session {active_session_id} is already active."
        raise ValueError(msg)
    now_ms = started_at_ms if started_at_ms is not None else _now_ms()
    sync_id = uuid.uuid4().hex
    origin_machine_id = _ensure_machine_id(conn)
    normalized_scope = normalize_run_scope(scope)
    cursor = conn.execute(
        """
        INSERT INTO runs (
            sync_id,
            origin_machine_id,
            name,
            started_at_ms,
            scope_harnesses_json,
            scope_provider_ids_json,
            scope_model_ids_json,
            scope_source_session_ids_json,
            scope_thinking_levels_json,
            scope_agents_json,
            archived_at_ms,
            created_at_ms,
            updated_at_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sync_id,
            origin_machine_id,
            name,
            now_ms,
            _dump_json_tuple(normalized_scope.harnesses),
            _dump_json_tuple(normalized_scope.provider_ids),
            _dump_json_tuple(normalized_scope.model_ids),
            _dump_json_tuple(normalized_scope.source_session_ids),
            _dump_json_tuple(normalized_scope.thinking_levels),
            _dump_json_tuple(normalized_scope.agents),
            None,
            now_ms,
            now_ms,
        ),
    )
    conn.commit()
    return _required_lastrowid(cursor.lastrowid)


def end_tracking_session(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    ended_at_ms: int | None = None,
) -> None:
    now_ms = ended_at_ms if ended_at_ms is not None else _now_ms()
    cursor = conn.execute(
        """
        UPDATE runs
        SET ended_at_ms = COALESCE(ended_at_ms, ?), updated_at_ms = ?
        WHERE id = ?
        """,
        (now_ms, now_ms, session_id),
    )
    if cursor.rowcount == 0:
        msg = f"Tracking session not found: {session_id}"
        raise ValueError(msg)
    conn.commit()


def get_active_tracking_session(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT id
        FROM runs
        WHERE ended_at_ms IS NULL
        ORDER BY started_at_ms DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return _required_int(row["id"])


def get_tracking_session(
    conn: sqlite3.Connection, session_id: int
) -> TrackingSession | None:
    row = conn.execute(
        """
        SELECT
            id,
            sync_id,
            name,
            started_at_ms,
            ended_at_ms,
            scope_harnesses_json,
            scope_provider_ids_json,
            scope_model_ids_json,
            scope_source_session_ids_json,
            scope_thinking_levels_json,
            scope_agents_json,
            archived_at_ms
        FROM runs
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _tracking_session_from_row(row)


def list_tracking_sessions(
    conn: sqlite3.Connection,
    *,
    include_archived: bool = False,
    archived_only: bool = False,
    active_only: bool = False,
    include_ended: bool = True,
) -> list[TrackingSession]:
    where_clauses: list[str] = []
    params: list[object] = []
    if archived_only:
        where_clauses.append("archived_at_ms IS NOT NULL")
    elif not include_archived:
        where_clauses.append("archived_at_ms IS NULL")
    if active_only:
        where_clauses.append("ended_at_ms IS NULL")
    elif not include_ended:
        where_clauses.append("ended_at_ms IS NULL")
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    rows = conn.execute(
        """
        SELECT
            id,
            sync_id,
            name,
            started_at_ms,
            ended_at_ms,
            scope_harnesses_json,
            scope_provider_ids_json,
            scope_model_ids_json,
            scope_source_session_ids_json,
            scope_thinking_levels_json,
            scope_agents_json,
            archived_at_ms
        FROM runs
        """
        + where_sql
        + """
        ORDER BY started_at_ms DESC, id DESC
        """,
        params,
    ).fetchall()
    return [_tracking_session_from_row(row) for row in rows]


def archive_tracking_session(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    archived_at_ms: int | None = None,
) -> None:
    session = get_tracking_session(conn, session_id)
    if session is None:
        msg = f"Tracking session not found: {session_id}"
        raise ValueError(msg)
    if session.ended_at_ms is None:
        msg = f"Cannot archive active run {session_id}. Stop it first."
        raise ValueError(msg)
    now_ms = archived_at_ms if archived_at_ms is not None else _now_ms()
    conn.execute(
        """
        UPDATE runs
        SET archived_at_ms = ?, updated_at_ms = ?
        WHERE id = ?
        """,
        (now_ms, now_ms, session_id),
    )
    conn.commit()


def unarchive_tracking_session(conn: sqlite3.Connection, session_id: int) -> None:
    now_ms = _now_ms()
    cursor = conn.execute(
        """
        UPDATE runs
        SET archived_at_ms = NULL, updated_at_ms = ?
        WHERE id = ?
        """,
        (now_ms, session_id),
    )
    if cursor.rowcount == 0:
        msg = f"Tracking session not found: {session_id}"
        raise ValueError(msg)
    conn.commit()


def _machine_from_row(row: sqlite3.Row) -> Machine:
    return Machine(
        machine_id=str(row["machine_id"]),
        name=str(row["name"]) if row["name"] is not None else None,
        name_key=str(row["name_key"]) if row["name_key"] is not None else None,
        first_seen_ms=_required_int(row["first_seen_ms"]),
        last_seen_ms=_required_int(row["last_seen_ms"]),
        is_local=bool(_required_int(row["is_local"])),
        created_at_ms=_required_int(row["created_at_ms"]),
        updated_at_ms=_required_int(row["updated_at_ms"]),
        imported_at_ms=_optional_int(row["imported_at_ms"]),
    )


def _area_from_row(row: sqlite3.Row) -> Area:
    return Area(
        id=_required_int(row["id"]),
        sync_id=str(row["sync_id"]),
        parent_id=_optional_int(row["parent_id"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        path=str(row["path"]),
        archived_at_ms=_optional_int(row["archived_at_ms"]),
        created_at_ms=_required_int(row["created_at_ms"]),
        updated_at_ms=_required_int(row["updated_at_ms"]),
        imported_at_ms=_optional_int(row["imported_at_ms"]),
    )


def _area_session_assignment_from_row(row: sqlite3.Row) -> AreaSessionAssignment:
    return AreaSessionAssignment(
        id=_required_int(row["id"]),
        sync_id=str(row["sync_id"]),
        area_id=_required_int(row["area_id"]),
        origin_machine_id=str(row["origin_machine_id"]),
        harness=str(row["harness"]),
        source_session_id=str(row["source_session_id"]),
        assigned_at_ms=_required_int(row["assigned_at_ms"]),
        updated_at_ms=_required_int(row["updated_at_ms"]),
        imported_at_ms=_optional_int(row["imported_at_ms"]),
    )


def get_local_machine_id(conn: sqlite3.Connection) -> str:
    return _ensure_machine_id(conn)


def upsert_machine(
    conn: sqlite3.Connection,
    *,
    machine_id: str,
    name: str | None,
    seen_ms: int,
    is_local: bool,
    imported_at_ms: int | None = None,
) -> Machine:
    _create_machines_table(conn)
    now_ms = _now_ms()
    normalized_name = _validate_machine_name(name)
    normalized_key = _machine_name_key(normalized_name)
    if is_local:
        conn.execute(
            """
            UPDATE machines
            SET is_local = 0,
                updated_at_ms = ?
            WHERE machine_id <> ?
              AND is_local = 1
            """,
            (now_ms, machine_id),
        )
    existing_row = conn.execute(
        """
        SELECT
            machine_id,
            name,
            name_key,
            first_seen_ms,
            last_seen_ms,
            is_local,
            created_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM machines
        WHERE machine_id = ?
        """,
        (machine_id,),
    ).fetchone()
    if existing_row is None:
        conn.execute(
            """
            INSERT INTO machines (
                machine_id,
                name,
                name_key,
                first_seen_ms,
                last_seen_ms,
                is_local,
                created_at_ms,
                updated_at_ms,
                imported_at_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                machine_id,
                normalized_name,
                normalized_key,
                seen_ms,
                seen_ms,
                1 if is_local else 0,
                now_ms,
                now_ms,
                imported_at_ms,
            ),
        )
    else:
        existing = _machine_from_row(existing_row)
        merged_name = normalized_name if name is not None else existing.name
        merged_key = _machine_name_key(merged_name)
        merged_first_seen = min(existing.first_seen_ms, seen_ms)
        merged_last_seen = max(existing.last_seen_ms, seen_ms)
        merged_imported_at = _max_optional_int(
            existing.imported_at_ms,
            imported_at_ms,
        )
        conn.execute(
            """
            UPDATE machines
            SET name = ?,
                name_key = ?,
                first_seen_ms = ?,
                last_seen_ms = ?,
                is_local = ?,
                updated_at_ms = ?,
                imported_at_ms = ?
            WHERE machine_id = ?
            """,
            (
                merged_name,
                merged_key,
                merged_first_seen,
                merged_last_seen,
                1 if is_local else 0,
                now_ms,
                merged_imported_at,
                machine_id,
            ),
        )
    row = conn.execute(
        """
        SELECT
            machine_id,
            name,
            name_key,
            first_seen_ms,
            last_seen_ms,
            is_local,
            created_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM machines
        WHERE machine_id = ?
        """,
        (machine_id,),
    ).fetchone()
    if row is None:
        msg = f"Machine row not found after upsert: {machine_id}"
        raise ValueError(msg)
    return _machine_from_row(row)


def set_local_machine_name(conn: sqlite3.Connection, name: str | None) -> Machine:
    local_machine_id = _ensure_machine_id(conn)
    if isinstance(name, str) and not name.strip():
        msg = "Machine name must not be empty. Use clear-name to unset."
        raise ValueError(msg)
    return upsert_machine(
        conn,
        machine_id=local_machine_id,
        name=_validate_machine_name(name),
        seen_ms=_now_ms(),
        is_local=True,
    )


def apply_local_machine_config(
    conn: sqlite3.Connection,
    machine_config: MachineConfig,
) -> Machine:
    local_machine_id = _ensure_machine_id(conn)
    effective_name = machine_config.name or default_hostname_name()
    if effective_name is None:
        effective_name = f"machine:{local_machine_id[:8]}"
    return upsert_machine(
        conn,
        machine_id=local_machine_id,
        name=effective_name,
        seen_ms=_now_ms(),
        is_local=True,
    )


def get_machine(conn: sqlite3.Connection, machine_id: str) -> Machine | None:
    row = conn.execute(
        """
        SELECT
            machine_id,
            name,
            name_key,
            first_seen_ms,
            last_seen_ms,
            is_local,
            created_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM machines
        WHERE machine_id = ?
        """,
        (machine_id,),
    ).fetchone()
    if row is None:
        return None
    return _machine_from_row(row)


def list_machines(conn: sqlite3.Connection) -> tuple[Machine, ...]:
    _ensure_machine_id(conn)
    rows = conn.execute(
        """
        SELECT
            machine_id,
            name,
            name_key,
            first_seen_ms,
            last_seen_ms,
            is_local,
            created_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM machines
        ORDER BY is_local DESC, last_seen_ms DESC, machine_id ASC
        """
    ).fetchall()
    return tuple(_machine_from_row(row) for row in rows)


def machine_label_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = list_machines(conn)
    name_key_counts: dict[str, int] = {}
    for machine in rows:
        if machine.name is None or machine.name_key is None:
            continue
        name_key_counts[machine.name_key] = name_key_counts.get(machine.name_key, 0) + 1
    labels: dict[str, str] = {}
    for machine in rows:
        label = machine.label
        if (
            machine.name is not None
            and machine.name_key is not None
            and name_key_counts.get(machine.name_key, 0) > 1
        ):
            label = f"{machine.name} ({machine.machine_id[:8]})"
        labels[machine.machine_id] = label
    return labels


def resolve_machine_selector(conn: sqlite3.Connection, selector: str) -> Machine:
    raw = selector.strip()
    if not raw:
        msg = "Machine selector must not be empty."
        raise ValueError(msg)
    exact = get_machine(conn, raw)
    if exact is not None:
        return exact

    matches: dict[str, Machine] = {}
    if len(raw) >= 8:
        prefix_rows = conn.execute(
            """
            SELECT
                machine_id,
                name,
                name_key,
                first_seen_ms,
                last_seen_ms,
                is_local,
                created_at_ms,
                updated_at_ms,
                imported_at_ms
            FROM machines
            WHERE machine_id LIKE ?
            """,
            (f"{raw}%",),
        ).fetchall()
        for row in prefix_rows:
            machine = _machine_from_row(row)
            matches[machine.machine_id] = machine

    normalized_selector: str | None
    try:
        normalized_selector = normalize_identity(raw)
    except ValueError:
        normalized_selector = None
    if normalized_selector is not None:
        name_rows = conn.execute(
            """
            SELECT
                machine_id,
                name,
                name_key,
                first_seen_ms,
                last_seen_ms,
                is_local,
                created_at_ms,
                updated_at_ms,
                imported_at_ms
            FROM machines
            WHERE name_key = ? OR name_key LIKE ?
            """,
            (normalized_selector, f"{normalized_selector}%"),
        ).fetchall()
        for row in name_rows:
            machine = _machine_from_row(row)
            matches[machine.machine_id] = machine

    if not matches:
        msg = f"No machine matched selector {selector!r}."
        raise ValueError(msg)
    if len(matches) > 1:
        labels = machine_label_map(conn)
        candidates = ", ".join(
            (
                f"{labels.get(machine.machine_id, machine.label)} "
                f"[{machine.machine_id[:8]}]"
            )
            for machine in sorted(matches.values(), key=lambda item: item.machine_id)
        )
        msg = f"Machine selector {selector!r} is ambiguous. Candidates: {candidates}"
        raise ValueError(msg)
    return next(iter(matches.values()))


def normalize_area_path(path: str) -> tuple[str, tuple[str, ...]]:
    raw = path.strip()
    if not raw:
        msg = "Area path must not be empty."
        raise ValueError(msg)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        msg = "Area path cannot contain control characters."
        raise ValueError(msg)
    names: list[str] = []
    slugs: list[str] = []
    for segment in raw.split("/"):
        name = segment.strip()
        if not name:
            msg = f"Area path {path!r} contains an empty segment."
            raise ValueError(msg)
        if name in {".", ".."}:
            msg = f"Area path {path!r} contains an invalid segment {name!r}."
            raise ValueError(msg)
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
            msg = "Area path cannot contain control characters."
            raise ValueError(msg)
        names.append(name)
        slugs.append(normalize_identity(name))
    return "/".join(slugs), tuple(names)


def get_area(conn: sqlite3.Connection, area_id: int) -> Area | None:
    row = conn.execute(
        """
        SELECT
            id,
            sync_id,
            parent_id,
            slug,
            name,
            path,
            archived_at_ms,
            created_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM areas
        WHERE id = ?
        """,
        (area_id,),
    ).fetchone()
    if row is None:
        return None
    return _area_from_row(row)


def get_area_by_path(conn: sqlite3.Connection, path: str) -> Area | None:
    normalized_path, _ = normalize_area_path(path)
    row = conn.execute(
        """
        SELECT
            id,
            sync_id,
            parent_id,
            slug,
            name,
            path,
            archived_at_ms,
            created_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM areas
        WHERE path = ?
        """,
        (normalized_path,),
    ).fetchone()
    if row is None:
        return None
    return _area_from_row(row)


def list_areas(
    conn: sqlite3.Connection,
    *,
    include_archived: bool = False,
) -> tuple[Area, ...]:
    _create_areas_table(conn)
    where_clause = "" if include_archived else "WHERE archived_at_ms IS NULL"
    rows = conn.execute(
        f"""
        SELECT
            id,
            sync_id,
            parent_id,
            slug,
            name,
            path,
            archived_at_ms,
            created_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM areas
        {where_clause}
        ORDER BY path ASC
        """
    ).fetchall()
    return tuple(_area_from_row(row) for row in rows)


def ensure_area(
    conn: sqlite3.Connection,
    path: str,
    *,
    name: str | None = None,
    imported_at_ms: int | None = None,
) -> Area:
    _create_areas_table(conn)
    normalized_path, names = normalize_area_path(path)
    slugs = normalized_path.split("/")
    now_ms = _now_ms()
    parent_id: int | None = None
    area: Area | None = None
    current_parts: list[str] = []
    for index, (slug, default_name) in enumerate(zip(slugs, names, strict=True)):
        current_parts.append(slug)
        current_path = "/".join(current_parts)
        display_name = (
            name.strip()
            if index == len(slugs) - 1 and name is not None and name.strip()
            else default_name
        )
        existing = get_area_by_path(conn, current_path)
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO areas (
                    sync_id,
                    parent_id,
                    slug,
                    name,
                    path,
                    archived_at_ms,
                    created_at_ms,
                    updated_at_ms,
                    imported_at_ms
                )
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    parent_id,
                    slug,
                    display_name,
                    current_path,
                    now_ms,
                    now_ms,
                    imported_at_ms,
                ),
            )
            area_id = _required_lastrowid(cursor.lastrowid)
            area = get_area(conn, area_id)
            if area is None:
                msg = f"Area not found after insert: {current_path}"
                raise ValueError(msg)
        else:
            area = existing
            if area.parent_id != parent_id:
                msg = f"Area tree mismatch for path {current_path!r}."
                raise ValueError(msg)
            if (
                area.name != display_name
                and index == len(slugs) - 1
                and name is not None
            ):
                conn.execute(
                    """
                    UPDATE areas
                    SET name = ?, updated_at_ms = ?, imported_at_ms = ?
                    WHERE id = ?
                    """,
                    (
                        display_name,
                        now_ms,
                        _max_optional_int(area.imported_at_ms, imported_at_ms),
                        area.id,
                    ),
                )
                refreshed = get_area(conn, area.id)
                if refreshed is None:
                    msg = f"Area not found after update: {current_path}"
                    raise ValueError(msg)
                area = refreshed
        parent_id = area.id
    if area is None:
        msg = f"Failed to create or load area for path {path!r}."
        raise ValueError(msg)
    return area


def resolve_area_ids(
    conn: sqlite3.Connection,
    path: str,
    *,
    include_descendants: bool = True,
) -> tuple[int, ...]:
    normalized_path, _ = normalize_area_path(path)
    area = get_area_by_path(conn, normalized_path)
    if area is None:
        msg = f"Area not found: {normalized_path}"
        raise ValueError(msg)
    if not include_descendants:
        return (area.id,)
    rows = conn.execute(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT id
            FROM areas
            WHERE path = ?
            UNION ALL
            SELECT child.id
            FROM areas AS child
            JOIN descendants AS parent_desc ON child.parent_id = parent_desc.id
            WHERE child.archived_at_ms IS NULL
        )
        SELECT id
        FROM descendants
        ORDER BY id
        """,
        (normalized_path,),
    ).fetchall()
    return tuple(_required_int(row["id"]) for row in rows)


def get_active_area(
    conn: sqlite3.Connection,
    *,
    machine_id: str | None = None,
) -> Area | None:
    _create_machine_active_areas_table(conn)
    effective_machine_id = machine_id or get_local_machine_id(conn)
    row = conn.execute(
        """
        SELECT area_id
        FROM machine_active_areas
        WHERE machine_id = ?
        """,
        (effective_machine_id,),
    ).fetchone()
    if row is None or row["area_id"] is None:
        return None
    return get_area(conn, _required_int(row["area_id"]))


def set_active_area(
    conn: sqlite3.Connection,
    area_id: int | None,
    *,
    machine_id: str | None = None,
    imported_at_ms: int | None = None,
) -> None:
    _create_machine_active_areas_table(conn)
    effective_machine_id = machine_id or get_local_machine_id(conn)
    local_machine_id = get_local_machine_id(conn)
    upsert_machine(
        conn,
        machine_id=effective_machine_id,
        name=None,
        seen_ms=_now_ms(),
        is_local=(effective_machine_id == local_machine_id),
        imported_at_ms=imported_at_ms,
    )
    if area_id is not None and get_area(conn, area_id) is None:
        msg = f"Area not found: {area_id}"
        raise ValueError(msg)
    now_ms = _now_ms()
    conn.execute(
        """
        INSERT INTO machine_active_areas (
            machine_id,
            area_id,
            updated_at_ms,
            imported_at_ms
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(machine_id) DO UPDATE SET
            area_id = excluded.area_id,
            updated_at_ms = excluded.updated_at_ms,
            imported_at_ms = CASE
                WHEN machine_active_areas.imported_at_ms IS NULL
                    THEN excluded.imported_at_ms
                WHEN excluded.imported_at_ms IS NULL
                    THEN machine_active_areas.imported_at_ms
                ELSE MAX(machine_active_areas.imported_at_ms, excluded.imported_at_ms)
            END
        """,
        (effective_machine_id, area_id, now_ms, imported_at_ms),
    )


def get_area_session_assignment(
    conn: sqlite3.Connection,
    *,
    origin_machine_id: str,
    harness: str,
    source_session_id: str,
) -> AreaSessionAssignment | None:
    _create_area_session_assignments_table(conn)
    row = conn.execute(
        """
        SELECT
            id,
            sync_id,
            area_id,
            origin_machine_id,
            harness,
            source_session_id,
            assigned_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM area_session_assignments
        WHERE origin_machine_id = ?
          AND harness = ?
          AND source_session_id = ?
        """,
        (origin_machine_id, harness, source_session_id),
    ).fetchone()
    if row is None:
        return None
    return _area_session_assignment_from_row(row)


def find_session_assignment_area_id(
    conn: sqlite3.Connection,
    origin_machine_id: str,
    harness: str,
    source_session_id: str,
) -> int | None:
    assignment = get_area_session_assignment(
        conn,
        origin_machine_id=origin_machine_id,
        harness=harness,
        source_session_id=source_session_id,
    )
    if assignment is None:
        return None
    return assignment.area_id


def assign_area_to_source_session(
    conn: sqlite3.Connection,
    *,
    area_id: int,
    origin_machine_id: str,
    harness: str,
    source_session_id: str,
    create_only: bool = False,
    imported_at_ms: int | None = None,
) -> AreaSessionAssignment:
    _create_area_session_assignments_table(conn)
    if get_area(conn, area_id) is None:
        msg = f"Area not found: {area_id}"
        raise ValueError(msg)
    existing = get_area_session_assignment(
        conn,
        origin_machine_id=origin_machine_id,
        harness=harness,
        source_session_id=source_session_id,
    )
    now_ms = _now_ms()
    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO area_session_assignments (
                sync_id,
                area_id,
                origin_machine_id,
                harness,
                source_session_id,
                assigned_at_ms,
                updated_at_ms,
                imported_at_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                area_id,
                origin_machine_id,
                harness,
                source_session_id,
                now_ms,
                now_ms,
                imported_at_ms,
            ),
        )
        assignment_id = _required_lastrowid(cursor.lastrowid)
    else:
        assignment_id = existing.id
        if not create_only and (
            existing.area_id != area_id or imported_at_ms is not None
        ):
            conn.execute(
                """
                UPDATE area_session_assignments
                SET area_id = ?,
                    updated_at_ms = ?,
                    imported_at_ms = ?
                WHERE id = ?
                """,
                (
                    area_id,
                    now_ms,
                    _max_optional_int(existing.imported_at_ms, imported_at_ms),
                    existing.id,
                ),
            )
    conn.execute(
        """
        UPDATE usage_events
        SET area_id = ?
        WHERE origin_machine_id = ?
          AND harness = ?
          AND source_session_id = ?
        """,
        (area_id, origin_machine_id, harness, source_session_id),
    )
    row = conn.execute(
        """
        SELECT
            id,
            sync_id,
            area_id,
            origin_machine_id,
            harness,
            source_session_id,
            assigned_at_ms,
            updated_at_ms,
            imported_at_ms
        FROM area_session_assignments
        WHERE id = ?
        """,
        (assignment_id,),
    ).fetchone()
    if row is None:
        msg = (
            "Area session assignment not found after upsert: "
            f"{origin_machine_id}/{harness}/{source_session_id}"
        )
        raise ValueError(msg)
    return _area_session_assignment_from_row(row)


def unassign_area_from_source_session(
    conn: sqlite3.Connection,
    *,
    origin_machine_id: str,
    harness: str,
    source_session_id: str,
) -> None:
    _create_area_session_assignments_table(conn)
    conn.execute(
        """
        DELETE FROM area_session_assignments
        WHERE origin_machine_id = ?
          AND harness = ?
          AND source_session_id = ?
        """,
        (origin_machine_id, harness, source_session_id),
    )
    conn.execute(
        """
        UPDATE usage_events
        SET area_id = NULL
        WHERE origin_machine_id = ?
          AND harness = ?
          AND source_session_id = ?
        """,
        (origin_machine_id, harness, source_session_id),
    )


def _source_session_has_usage_events(
    conn: sqlite3.Connection,
    *,
    origin_machine_id: str,
    harness: str,
    source_session_id: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM usage_events
        WHERE origin_machine_id = ?
          AND harness = ?
          AND source_session_id = ?
        LIMIT 1
        """,
        (origin_machine_id, harness, source_session_id),
    ).fetchone()
    return row is not None


def _areas_by_id(conn: sqlite3.Connection) -> dict[int, Area]:
    return {area.id: area for area in list_areas(conn, include_archived=True)}


def get_machine_id(conn: sqlite3.Connection) -> str:
    return get_local_machine_id(conn)


def _source_session_key(source_session_id: str | None) -> str:
    return source_session_id or ""


def get_import_source_state(
    conn: sqlite3.Connection,
    *,
    harness: str,
    source_path: str,
    source_session_id: str | None = None,
) -> ImportSourceState | None:
    row = conn.execute(
        """
        SELECT
            harness,
            source_path,
            source_session_key,
            fingerprint_size,
            fingerprint_mtime_ns,
            fingerprint_inode,
            sqlite_page_count,
            sqlite_schema_version,
            last_imported_created_ms,
            last_seen_rowid,
            last_file_offset,
            updated_at_ms
        FROM import_sources
        WHERE harness = ? AND source_path = ? AND source_session_key = ?
        """,
        (harness, source_path, _source_session_key(source_session_id)),
    ).fetchone()
    if row is None:
        return None
    return ImportSourceState(
        harness=str(row["harness"]),
        source_path=str(row["source_path"]),
        source_session_id=(
            str(row["source_session_key"])
            if str(row["source_session_key"]) != ""
            else None
        ),
        fingerprint_size=_optional_int(row["fingerprint_size"]),
        fingerprint_mtime_ns=_optional_int(row["fingerprint_mtime_ns"]),
        fingerprint_inode=_optional_int(row["fingerprint_inode"]),
        sqlite_page_count=_optional_int(row["sqlite_page_count"]),
        sqlite_schema_version=_optional_int(row["sqlite_schema_version"]),
        last_imported_created_ms=_optional_int(row["last_imported_created_ms"]),
        last_seen_rowid=_optional_int(row["last_seen_rowid"]),
        last_file_offset=_optional_int(row["last_file_offset"]),
        updated_at_ms=_optional_int(row["updated_at_ms"]),
    )


def upsert_import_source_state(
    conn: sqlite3.Connection,
    *,
    harness: str,
    source_path: str,
    source_session_id: str | None = None,
    fingerprint_size: int | None = None,
    fingerprint_mtime_ns: int | None = None,
    fingerprint_inode: int | None = None,
    sqlite_page_count: int | None = None,
    sqlite_schema_version: int | None = None,
    last_imported_created_ms: int | None = None,
    last_seen_rowid: int | None = None,
    last_file_offset: int | None = None,
) -> None:
    now_ms = _now_ms()
    conn.execute(
        """
        INSERT INTO import_sources (
            harness,
            source_path,
            source_session_key,
            fingerprint_size,
            fingerprint_mtime_ns,
            fingerprint_inode,
            sqlite_page_count,
            sqlite_schema_version,
            last_imported_created_ms,
            last_seen_rowid,
            last_file_offset,
            updated_at_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(harness, source_path, source_session_key) DO UPDATE SET
            fingerprint_size = excluded.fingerprint_size,
            fingerprint_mtime_ns = excluded.fingerprint_mtime_ns,
            fingerprint_inode = excluded.fingerprint_inode,
            sqlite_page_count = excluded.sqlite_page_count,
            sqlite_schema_version = excluded.sqlite_schema_version,
            last_imported_created_ms = excluded.last_imported_created_ms,
            last_seen_rowid = excluded.last_seen_rowid,
            last_file_offset = excluded.last_file_offset,
            updated_at_ms = excluded.updated_at_ms
        """,
        (
            harness,
            source_path,
            _source_session_key(source_session_id),
            fingerprint_size,
            fingerprint_mtime_ns,
            fingerprint_inode,
            sqlite_page_count,
            sqlite_schema_version,
            last_imported_created_ms,
            last_seen_rowid,
            last_file_offset,
            now_ms,
        ),
    )


def attach_harness_session(
    conn: sqlite3.Connection,
    tracking_session_id: int,
    harness: str,
    source_session_id: str,
    *,
    first_seen_ms: int | None,
    last_seen_ms: int | None,
) -> int:
    now_ms = _now_ms()
    run_sync_id = _get_run_sync_id(conn, tracking_session_id)
    source_session_sync_id = _build_source_session_sync_id(
        run_sync_id=run_sync_id,
        harness=harness,
        source_session_id=source_session_id,
    )
    existing = conn.execute(
        """
        SELECT id, sync_id, first_seen_ms, last_seen_ms
        FROM source_sessions
        WHERE tracking_session_id = ? AND harness = ? AND source_session_id = ?
        """,
        (tracking_session_id, harness, source_session_id),
    ).fetchone()
    if existing is None:
        cursor = conn.execute(
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
                source_session_sync_id,
                tracking_session_id,
                harness,
                source_session_id,
                first_seen_ms,
                last_seen_ms,
                now_ms,
                now_ms,
            ),
        )
        return _required_lastrowid(cursor.lastrowid)

    existing_first = (
        _required_int(existing["first_seen_ms"])
        if existing["first_seen_ms"] is not None
        else None
    )
    existing_last = (
        _required_int(existing["last_seen_ms"])
        if existing["last_seen_ms"] is not None
        else None
    )
    merged_first = _min_optional_int(existing_first, first_seen_ms)
    merged_last = _max_optional_int(existing_last, last_seen_ms)
    conn.execute(
        """
        UPDATE source_sessions
        SET sync_id = COALESCE(sync_id, ?),
            first_seen_ms = ?,
            last_seen_ms = ?,
            updated_at_ms = ?
        WHERE id = ?
        """,
        (
            source_session_sync_id,
            merged_first,
            merged_last,
            now_ms,
            _required_int(existing["id"]),
        ),
    )
    return _required_int(existing["id"])


def _get_run_sync_id(conn: sqlite3.Connection, tracking_session_id: int) -> str:
    row = conn.execute(
        """
        SELECT sync_id
        FROM runs
        WHERE id = ?
        """,
        (tracking_session_id,),
    ).fetchone()
    if row is None or row["sync_id"] is None:
        msg = f"Tracking session not found: {tracking_session_id}"
        raise ValueError(msg)
    return str(row["sync_id"])


def usage_event_matches_run_scope(event: UsageEvent, scope: RunScope) -> bool:
    normalized_scope = normalize_run_scope(scope)
    if normalized_scope.empty:
        return True
    if (
        normalized_scope.harnesses
        and normalize_identity(event.harness) not in normalized_scope.harnesses
    ):
        return False
    if (
        normalized_scope.source_session_ids
        and event.source_session_id not in normalized_scope.source_session_ids
    ):
        return False
    if (
        normalized_scope.provider_ids
        and normalize_identity(event.provider_id) not in normalized_scope.provider_ids
    ):
        return False
    if (
        normalized_scope.model_ids
        and normalize_identity(event.model_id) not in normalized_scope.model_ids
    ):
        return False
    if normalized_scope.thinking_levels:
        level = normalize_thinking_level(event.thinking_level)
        if level is None or level not in normalized_scope.thinking_levels:
            return False
    if normalized_scope.agents:
        if event.agent is None:
            return False
        if normalize_identity(event.agent) not in normalized_scope.agents:
            return False
    return True


def insert_usage_events(
    conn: sqlite3.Connection,
    tracking_session_id: int | None,
    events: list[UsageEvent],
    *,
    since_ms: int | None = None,
    link_scope: RunScope | None = None,
    origin_machine_id: str | None = None,
) -> InsertUsageResult:
    filtered_events = [
        event for event in events if since_ms is None or event.created_ms >= since_ms
    ]
    effective_scope = normalize_run_scope(link_scope)
    link_events = (
        filtered_events
        if tracking_session_id is None or effective_scope.empty
        else [
            event
            for event in filtered_events
            if usage_event_matches_run_scope(event, effective_scope)
        ]
    )
    link_event_keys = {(event.harness, event.global_dedup_key) for event in link_events}
    harness_session_ids: dict[tuple[str, str], int] = {}
    imported_at_ms = _now_ms()
    local_machine_id = get_local_machine_id(conn)
    event_origin_machine_id = origin_machine_id or local_machine_id
    active_area = get_active_area(conn, machine_id=event_origin_machine_id)
    active_area_id = active_area.id if active_area is not None else None
    session_area_cache: dict[tuple[str, str, str], int | None] = {}
    rows_inserted = 0
    rows_linked = 0
    rows_scope_excluded = max(len(filtered_events) - len(link_events), 0)

    with conn:
        upsert_machine(
            conn,
            machine_id=event_origin_machine_id,
            name=None,
            seen_ms=imported_at_ms,
            is_local=(event_origin_machine_id == local_machine_id),
        )
        if tracking_session_id is not None:
            grouped_ranges = _group_event_ranges(link_events)
            for (
                harness,
                source_session_id,
            ), (
                first_seen_ms,
                last_seen_ms,
            ) in grouped_ranges.items():
                harness_session_ids[(harness, source_session_id)] = (
                    attach_harness_session(
                        conn,
                        tracking_session_id,
                        harness,
                        source_session_id,
                        first_seen_ms=first_seen_ms,
                        last_seen_ms=last_seen_ms,
                    )
                )

        conn.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS tmp_usage_import (
                tracking_session_id INTEGER,
                harness_session_id INTEGER,
                harness TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                source_row_id TEXT,
                source_message_id TEXT,
                source_dedup_key TEXT,
                global_dedup_key TEXT NOT NULL,
                fingerprint_hash TEXT NOT NULL,
                origin_machine_id TEXT NOT NULL,
                area_id INTEGER,
                provider_id TEXT NOT NULL,
                provider_key TEXT NOT NULL,
                model_id TEXT NOT NULL,
                model_key TEXT NOT NULL,
                thinking_level TEXT,
                agent TEXT,
                agent_key TEXT,
                created_ms INTEGER NOT NULL,
                completed_ms INTEGER,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                reasoning_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL,
                cache_write_tokens INTEGER NOT NULL,
                cache_output_tokens INTEGER NOT NULL,
                source_cost_usd TEXT NOT NULL,
                raw_json TEXT,
                imported_at_ms INTEGER NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM tmp_usage_import")

        temp_rows: list[tuple[object, ...]] = []
        for event in filtered_events:
            should_link = (
                tracking_session_id is not None
                and (event.harness, event.global_dedup_key) in link_event_keys
            )
            harness_session_id = None
            if should_link:
                harness_session_id = harness_session_ids.get(
                    (event.harness, event.source_session_id)
                )
            provider_key = normalize_identity(event.provider_id)
            model_key = normalize_identity(event.model_id)
            agent_key = normalize_identity(event.agent) if event.agent else None
            session_key = (
                event_origin_machine_id,
                event.harness,
                event.source_session_id,
            )
            if session_key not in session_area_cache:
                row_area_id = find_session_assignment_area_id(
                    conn,
                    event_origin_machine_id,
                    event.harness,
                    event.source_session_id,
                )
                if row_area_id is None and not _source_session_has_usage_events(
                    conn,
                    origin_machine_id=event_origin_machine_id,
                    harness=event.harness,
                    source_session_id=event.source_session_id,
                ):
                    if active_area_id is not None:
                        row_area_id = assign_area_to_source_session(
                            conn,
                            area_id=active_area_id,
                            origin_machine_id=event_origin_machine_id,
                            harness=event.harness,
                            source_session_id=event.source_session_id,
                            create_only=True,
                        ).area_id
                session_area_cache[session_key] = row_area_id
            row_area_id = session_area_cache[session_key]
            temp_rows.append(
                (
                    tracking_session_id if should_link else None,
                    harness_session_id,
                    event.harness,
                    event.source_session_id,
                    event.source_row_id,
                    event.source_message_id,
                    event.source_dedup_key,
                    event.global_dedup_key,
                    event.fingerprint_hash,
                    event_origin_machine_id,
                    row_area_id,
                    event.provider_id,
                    provider_key,
                    event.model_id,
                    model_key,
                    event.thinking_level,
                    event.agent,
                    agent_key,
                    event.created_ms,
                    event.completed_ms,
                    event.tokens.input,
                    event.tokens.output,
                    event.tokens.reasoning,
                    event.tokens.cache_read,
                    event.tokens.cache_write,
                    event.tokens.cache_output,
                    _source_cost_to_storage(event.source_cost_usd),
                    event.raw_json,
                    imported_at_ms,
                )
            )
        if temp_rows:
            temp_placeholders = ", ".join("?" for _ in range(29))
            conn.executemany(
                """
                INSERT INTO tmp_usage_import (
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
                    area_id,
                    provider_id,
                    provider_key,
                    model_id,
                    model_key,
                    thinking_level,
                    agent,
                    agent_key,
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
                VALUES ("""
                + temp_placeholders
                + ")",
                temp_rows,
            )

        before_usage_count = _required_int(
            conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO usage_events (
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
                area_id,
                role,
                provider_id,
                provider_key,
                model_id,
                model_key,
                thinking_level,
                agent,
                agent_key,
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
            SELECT
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
                area_id,
                'assistant',
                provider_id,
                provider_key,
                model_id,
                model_key,
                thinking_level,
                agent,
                agent_key,
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
            FROM tmp_usage_import
            """
        )
        after_usage_count = _required_int(
            conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        )
        rows_inserted = max(after_usage_count - before_usage_count, 0)

        if tracking_session_id is not None:
            before_link_count = _required_int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_events
                    WHERE tracking_session_id = ?
                    """,
                    (tracking_session_id,),
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO run_events (
                    tracking_session_id,
                    usage_event_id,
                    created_at_ms
                )
                SELECT
                    ?,
                    ue.id,
                    ?
                FROM usage_events AS ue
                JOIN tmp_usage_import AS tmp
                  ON tmp.harness = ue.harness
                 AND tmp.global_dedup_key = ue.global_dedup_key
                WHERE tmp.tracking_session_id IS NOT NULL
                """,
                (tracking_session_id, imported_at_ms),
            )
            after_link_count = _required_int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM run_events
                    WHERE tracking_session_id = ?
                    """,
                    (tracking_session_id,),
                ).fetchone()[0]
            )
            rows_linked = max(after_link_count - before_link_count, 0)

        conn.execute("DELETE FROM tmp_usage_import")

    return InsertUsageResult(
        rows_inserted=rows_inserted,
        rows_linked=rows_linked,
        rows_skipped=len(filtered_events) - rows_inserted,
        rows_scope_excluded=rows_scope_excluded,
    )


def summarize_tracking_session(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    costing_config: CostingConfig | None = None,
    simulation_targets: tuple[SimulationTarget, ...] = (),
) -> RunReport:
    return summarize_usage(
        conn,
        UsageReportFilter(tracking_session_id=session_id),
        costing_config=costing_config,
    )


def list_usage_events(
    conn: sqlite3.Connection,
    filters: UsageReportFilter,
    *,
    order: str = "created",
) -> list[UsageEvent]:
    filters, _ = _apply_tracking_session_time_window(conn, filters)
    filters = _resolve_usage_area_filter(conn, filters)
    source_clause, where_clause, params = _usage_report_query_parts(filters)
    if order == "created":
        order_clause = " ORDER BY ue.created_ms ASC, ue.id ASC"
    elif order == "created_desc":
        order_clause = " ORDER BY ue.created_ms DESC, ue.id DESC"
    else:
        msg = "Unsupported order. Use created or created_desc."
        raise ValueError(msg)
    rows = conn.execute(
        """
        SELECT
            ue.harness,
            ue.source_session_id,
            ue.source_row_id,
            ue.source_message_id,
            ue.source_dedup_key,
            ue.global_dedup_key,
            ue.fingerprint_hash,
            ue.origin_machine_id,
            ue.provider_id,
            ue.model_id,
            ue.thinking_level,
            ue.agent,
            ue.created_ms,
            ue.completed_ms,
            ue.input_tokens,
            ue.output_tokens,
            ue.reasoning_tokens,
            ue.cache_read_tokens,
            ue.cache_write_tokens,
            ue.cache_output_tokens,
            ue.source_cost_usd,
            ue.raw_json
        """
        + source_clause
        + where_clause
        + order_clause,
        params,
    ).fetchall()
    return [_usage_event_from_row(row) for row in rows]


def summarize_usage(
    conn: sqlite3.Connection,
    filters: UsageReportFilter,
    *,
    costing_config: CostingConfig | None = None,
    simulation_targets: tuple[SimulationTarget, ...] = (),
) -> RunReport:
    filters, session = _apply_tracking_session_time_window(conn, filters)
    filters = _resolve_usage_area_filter(conn, filters)
    rows = _aggregate_usage_rows(
        conn,
        filters,
        group_by=(
            "origin_machine_id",
            "harness",
            "source_session_id",
            "provider_id",
            "model_id",
            "thinking_level",
            "agent",
            "context_tokens",
        ),
        split_thinking=filters.split_thinking,
    )

    config = costing_config or default_costing_config()
    runtime = compile_costing_config(config)
    totals_tokens = TokenBreakdown()
    totals_costs = CostTotals()
    by_provider: dict[str, _ReportBucket] = {}
    by_harness: dict[str, _ReportBucket] = {}
    by_machine: dict[str | None, _ReportBucket] = {}
    by_model: dict[tuple[str, str, str | None], _ReportBucket] = {}
    by_agent: dict[str, _ReportBucket] = {}
    unconfigured: dict[
        tuple[str, str, str, str | None, tuple[str, ...]], _UnconfiguredBucket
    ] = {}

    for row in rows:
        atom = UsageCostAtom(
            harness=row.harness,
            provider_id=row.provider_id,
            model_id=row.model_id,
            thinking_level=row.thinking_level if filters.split_thinking else None,
            agent=row.agent,
            message_count=row.message_count,
            tokens=row.tokens,
            source_cost_usd=row.source_cost_usd,
        )
        resolution = resolve_price_resolution(
            harness=atom.harness,
            provider_id=atom.provider_id,
            model_id=atom.model_id,
            config=config,
            context_tokens=row.context_tokens,
            runtime=runtime,
        )
        breakdown = runtime.compute_costs(
            harness=atom.harness,
            provider_id=atom.provider_id,
            model_id=atom.model_id,
            tokens=atom.tokens,
            source_cost_usd=atom.source_cost_usd,
            message_count=atom.message_count,
        )
        totals_tokens = _add_tokens(totals_tokens, atom.tokens)
        totals_costs = _add_cost_breakdown(totals_costs, breakdown)

        by_provider.setdefault(atom.provider_id, _ReportBucket()).add(atom, breakdown)
        by_harness.setdefault(atom.harness, _ReportBucket()).add(atom, breakdown)
        by_machine.setdefault(row.origin_machine_id, _ReportBucket()).add(
            atom,
            breakdown,
        )
        by_model.setdefault(
            (atom.provider_id, atom.model_id, atom.thinking_level),
            _ReportBucket(),
        ).add(atom, breakdown)
        by_agent.setdefault(atom.agent or "unknown", _ReportBucket()).add(
            atom, breakdown
        )
        if resolution.missing_kinds:
            unconfigured.setdefault(
                (
                    atom.harness,
                    atom.provider_id,
                    atom.model_id,
                    atom.thinking_level,
                    resolution.missing_kinds,
                ),
                _UnconfiguredBucket(),
            ).add(atom)

    simulations: list[SimulationSummaryRow] = []
    for target in simulation_targets:
        if costing_config is None:
            costing_config = default_costing_config()
        sim_result = simulate_cost(
            tokens=totals_tokens,
            target=target,
            config=costing_config,
            baseline_actual_usd=totals_costs.actual_cost_usd,
            baseline_virtual_usd=totals_costs.virtual_cost_usd,
        )
        simulations.append(
            SimulationSummaryRow(
                target_provider=sim_result.target_provider,
                target_model=sim_result.target_model,
                input_tokens=totals_tokens.input,
                output_tokens=totals_tokens.output,
                reasoning_tokens=totals_tokens.reasoning,
                cache_read_tokens=totals_tokens.cache_read,
                cache_write_tokens=totals_tokens.cache_write,
                cache_output_tokens=totals_tokens.cache_output,
                total_tokens=totals_tokens.total,
                cost_usd=sim_result.cost_usd,
                baseline_virtual_usd=sim_result.baseline_virtual_usd,
                delta_vs_virtual_usd=sim_result.delta_vs_virtual_usd,
            )
        )
    machine_labels = machine_label_map(conn)
    machines_by_id = {machine.machine_id: machine for machine in list_machines(conn)}
    return RunReport(
        session=session,
        totals=SessionTotals(tokens=totals_tokens, costs=totals_costs),
        by_provider=[
            ProviderSummaryRow(
                provider_id=provider_id,
                message_count=bucket.message_count,
                tokens=bucket.tokens,
                costs=bucket.costs,
            )
            for provider_id, bucket in sorted(
                by_provider.items(),
                key=lambda item: (
                    -item[1].costs.actual_cost_usd,
                    -item[1].costs.source_cost_usd,
                    -item[1].tokens.total,
                    item[0],
                ),
            )
        ],
        by_harness=[
            HarnessSummaryRow(
                harness=harness,
                message_count=bucket.message_count,
                tokens=bucket.tokens,
                costs=bucket.costs,
            )
            for harness, bucket in sorted(
                by_harness.items(),
                key=lambda item: (
                    -item[1].costs.actual_cost_usd,
                    -item[1].tokens.total,
                    item[0],
                ),
            )
        ],
        by_machine=[
            MachineSummaryRow(
                machine_id=machine_id,
                machine_name=(
                    machines_by_id[machine_id].name
                    if machine_id is not None and machine_id in machines_by_id
                    else None
                ),
                machine_label=(
                    machine_labels.get(machine_id, f"machine:{machine_id[:8]}")
                    if machine_id is not None
                    else "unknown"
                ),
                message_count=bucket.message_count,
                tokens=bucket.tokens,
                costs=bucket.costs,
            )
            for machine_id, bucket in sorted(
                by_machine.items(),
                key=lambda item: (
                    -item[1].costs.actual_cost_usd,
                    -item[1].tokens.total,
                    item[0] or "",
                ),
            )
        ],
        by_model=[
            ModelSummaryRow(
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                message_count=bucket.message_count,
                tokens=bucket.tokens,
                costs=bucket.costs,
            )
            for (provider_id, model_id, thinking_level), bucket in sorted(
                by_model.items(),
                key=lambda item: (
                    -item[1].costs.actual_cost_usd,
                    -item[1].message_count,
                    item[0][0],
                    item[0][1],
                    item[0][2] or "",
                ),
            )
        ],
        by_activity=[
            ActivitySummaryRow(
                agent=agent,
                message_count=bucket.message_count,
                tokens=bucket.tokens,
                costs=bucket.costs,
            )
            for agent, bucket in sorted(
                by_agent.items(),
                key=lambda item: (
                    -item[1].costs.actual_cost_usd,
                    -item[1].tokens.total,
                    item[0],
                ),
            )
        ],
        unconfigured_models=[
            UnconfiguredModelRow(
                required=required,
                harness=harness,
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                message_count=bucket.message_count,
                tokens=bucket.tokens,
            )
            for (
                harness,
                provider_id,
                model_id,
                thinking_level,
                required,
            ), bucket in sorted(
                unconfigured.items(),
                key=lambda item: (
                    -item[1].tokens.total,
                    -item[1].message_count,
                    item[0][0],
                    item[0][1],
                    item[0][2],
                    item[0][3] or "",
                    item[0][4],
                ),
            )
        ],
        filters=filters,
    )


def summarize_subscription_usage(
    conn: sqlite3.Connection,
    config: CostingConfig,
    *,
    provider_id: str | None = None,
    now_ms: int | None = None,
) -> SubscriptionUsageReport:
    from toktrail.periods import (
        resolve_first_use_subscription_window,
        resolve_fixed_subscription_window,
    )

    generated_at_ms = _now_ms() if now_ms is None else now_ms
    provider_filter = (
        normalize_identity(provider_id) if provider_id is not None else None
    )

    subscriptions = [
        subscription
        for subscription in config.subscriptions
        if subscription.enabled
        and (
            provider_filter is None
            or provider_filter in _subscription_usage_provider_ids(subscription)
        )
    ]

    runtime = compile_costing_config(config)

    request_details: dict[str, dict[str, object]] = {}
    request_items: list[_SubscriptionWindowRequest] = []
    first_use_cache: dict[tuple[tuple[str, ...], int], list[int]] = {}
    billing_request_by_subscription: dict[str, str] = {}
    request_counter = 0

    for subscription in sorted(subscriptions, key=lambda item: item.id):
        provider_ids = _subscription_usage_provider_ids(subscription)
        for window_config in sorted(
            subscription.windows,
            key=lambda item: (_PERIOD_SORT.get(item.period, 99), item.period),
        ):
            if not window_config.enabled:
                continue
            status = "active"
            since_ms: int | None
            until_ms: int | None
            last_since_ms: int | None = None
            last_until_ms: int | None = None
            last_usage_ms: int | None = None
            if window_config.reset_mode == "fixed":
                fixed_window = resolve_fixed_subscription_window(
                    period=window_config.period,
                    reset_at=window_config.reset_at,
                    timezone_name=subscription.timezone,
                    now_ms=generated_at_ms,
                )
                since_ms = fixed_window.since_ms
                until_ms = fixed_window.until_ms
            else:
                reset_anchor = resolve_fixed_subscription_window(
                    period="daily",
                    reset_at=window_config.reset_at,
                    timezone_name=subscription.timezone,
                    now_ms=0,
                )
                cache_key = (provider_ids, reset_anchor.since_ms)
                usage_timestamps = first_use_cache.get(cache_key)
                if usage_timestamps is None:
                    usage_timestamps = _provider_usage_timestamps(
                        conn,
                        provider_ids=provider_ids,
                        since_ms=reset_anchor.since_ms,
                        until_ms=generated_at_ms,
                    )
                    first_use_cache[cache_key] = usage_timestamps
                first_use_window = resolve_first_use_subscription_window(
                    period=window_config.period,
                    reset_at=window_config.reset_at,
                    timezone_name=subscription.timezone,
                    usage_timestamps_ms=usage_timestamps,
                    now_ms=generated_at_ms,
                )
                status = first_use_window.status
                since_ms = first_use_window.since_ms
                until_ms = first_use_window.until_ms
                last_since_ms = first_use_window.last_since_ms
                last_until_ms = first_use_window.last_until_ms
                last_usage_ms = first_use_window.last_usage_ms

            request_id = f"req-{request_counter:04d}"
            request_counter += 1
            request_details[request_id] = {
                "subscription_id": subscription.id,
                "kind": "quota",
                "period": window_config.period,
                "window": window_config,
                "status": status,
                "since_ms": since_ms,
                "until_ms": until_ms,
                "last_since_ms": last_since_ms,
                "last_until_ms": last_until_ms,
                "last_usage_ms": last_usage_ms,
                "basis": subscription.quota_cost_basis,
                "provider_ids": provider_ids,
            }
            if since_ms is not None and until_ms is not None:
                request_items.append(
                    _SubscriptionWindowRequest(
                        request_id=request_id,
                        subscription_id=subscription.id,
                        kind="quota",
                        period=window_config.period,
                        provider_ids=provider_ids,
                        since_ms=since_ms,
                        until_ms=until_ms,
                        quota_cost_basis=subscription.quota_cost_basis,
                    )
                )

        if subscription.fixed_cost_usd is not None:
            billing_reset_at = (
                subscription.fixed_cost_reset_at
                or _subscription_reset_at_from_windows(
                    subscription.windows,
                    period=subscription.fixed_cost_period,
                )
            )
            if billing_reset_at is None:
                msg = (
                    "fixed_cost_reset_at is required when fixed_cost_usd is set and "
                    "no matching subscription window exists."
                )
                raise ValueError(msg)
            billing_window = resolve_fixed_subscription_window(
                period=subscription.fixed_cost_period,
                reset_at=billing_reset_at,
                timezone_name=subscription.timezone,
                now_ms=generated_at_ms,
            )
            billing_basis = (
                subscription.fixed_cost_basis or subscription.quota_cost_basis
            )
            request_id = f"req-{request_counter:04d}"
            request_counter += 1
            request_details[request_id] = {
                "subscription_id": subscription.id,
                "kind": "billing",
                "period": subscription.fixed_cost_period,
                "reset_at": billing_reset_at,
                "since_ms": billing_window.since_ms,
                "until_ms": billing_window.until_ms,
                "basis": billing_basis,
                "provider_ids": provider_ids,
                "fixed_cost_usd": subscription.fixed_cost_usd,
            }
            billing_request_by_subscription[subscription.id] = request_id
            request_items.append(
                _SubscriptionWindowRequest(
                    request_id=request_id,
                    subscription_id=subscription.id,
                    kind="billing",
                    period=subscription.fixed_cost_period,
                    provider_ids=provider_ids,
                    since_ms=billing_window.since_ms,
                    until_ms=billing_window.until_ms,
                    quota_cost_basis=billing_basis,
                )
            )

    request_summaries = _summarize_window_requests(
        conn,
        requests=tuple(request_items),
        runtime=runtime,
    )

    rows: list[SubscriptionUsageRow] = []
    for subscription in sorted(subscriptions, key=lambda item: item.id):
        provider_ids = _subscription_usage_provider_ids(subscription)
        periods: list[SubscriptionUsagePeriod] = []
        for request_id, detail in sorted(request_details.items()):
            if (
                detail["subscription_id"] != subscription.id
                or detail["kind"] != "quota"
            ):
                continue
            window = cast(SubscriptionWindowConfig, detail["window"])
            summary = request_summaries.get(request_id, _WindowUsageSummary())
            used_usd = _select_subscription_cost(
                summary.costs,
                basis=str(detail["basis"]),
            )
            warnings: list[dict[str, object]] = []
            if summary.message_count > 0 and used_usd == 0:
                for (model_provider, model_id), (model_count, model_costs) in sorted(
                    summary.by_model.items()
                ):
                    model_cost = _select_subscription_cost(
                        model_costs,
                        basis=str(detail["basis"]),
                    )
                    if model_cost == 0 and model_count > 0:
                        warnings.append(
                            {
                                "kind": "zero_cost_with_tokens",
                                "cost_basis": str(detail["basis"]),
                                "provider_id": model_provider,
                                "model_id": model_id,
                                "message_count": model_count,
                            }
                        )

            limit_usd = Decimal(str(window.limit_usd))
            remaining_usd = max(limit_usd - used_usd, Decimal(0))
            over_limit_usd = max(used_usd - limit_usd, Decimal(0))
            percent_used = (
                None if limit_usd == 0 else (used_usd / limit_usd) * Decimal(100)
            )
            periods.append(
                SubscriptionUsagePeriod(
                    period=window.period,
                    reset_mode=window.reset_mode,
                    reset_at=window.reset_at,
                    status=str(detail["status"]),
                    since_ms=cast(int | None, detail["since_ms"]),
                    until_ms=cast(int | None, detail["until_ms"]),
                    limit_usd=limit_usd,
                    used_usd=used_usd,
                    remaining_usd=remaining_usd,
                    over_limit_usd=over_limit_usd,
                    percent_used=percent_used,
                    message_count=summary.message_count,
                    tokens=summary.tokens,
                    costs=summary.costs,
                    last_since_ms=cast(int | None, detail["last_since_ms"]),
                    last_until_ms=cast(int | None, detail["last_until_ms"]),
                    last_usage_ms=cast(int | None, detail["last_usage_ms"]),
                    warnings=tuple(warnings),
                )
            )

        billing: SubscriptionBillingPeriod | None = None
        billing_request_id = billing_request_by_subscription.get(subscription.id)
        if billing_request_id is not None:
            detail = request_details[billing_request_id]
            summary = request_summaries.get(billing_request_id, _WindowUsageSummary())
            value_usd = _select_subscription_cost(
                summary.costs, basis=str(detail["basis"])
            )
            fixed_cost_usd = Decimal(str(detail["fixed_cost_usd"]))
            break_even_percent = (
                None
                if fixed_cost_usd == 0
                else (value_usd / fixed_cost_usd) * Decimal(100)
            )
            billing = SubscriptionBillingPeriod(
                period=str(detail["period"]),
                reset_at=str(detail["reset_at"]),
                since_ms=cast(int, detail["since_ms"]),
                until_ms=cast(int, detail["until_ms"]),
                billing_basis=str(detail["basis"]),
                fixed_cost_usd=fixed_cost_usd,
                value_usd=value_usd,
                net_savings_usd=value_usd - fixed_cost_usd,
                break_even_remaining_usd=max(fixed_cost_usd - value_usd, Decimal(0)),
                break_even_percent=break_even_percent,
                message_count=summary.message_count,
                tokens=summary.tokens,
                costs=summary.costs,
            )

        rows.append(
            SubscriptionUsageRow(
                subscription_id=subscription.id,
                display_name=subscription.label,
                timezone=subscription.timezone,
                usage_provider_ids=provider_ids,
                quota_cost_basis=subscription.quota_cost_basis,
                periods=tuple(periods),
                billing=billing,
            )
        )

    return SubscriptionUsageReport(
        generated_at_ms=generated_at_ms, subscriptions=tuple(rows)
    )


def _subscription_reset_at_from_windows(
    windows: tuple[SubscriptionWindowConfig, ...],
    *,
    period: str,
) -> str | None:
    for window in windows:
        if window.enabled and window.period == period:
            return window.reset_at
    return None


def _subscription_usage_provider_ids(
    subscription: SubscriptionConfig,
) -> tuple[str, ...]:
    return tuple(subscription.usage_providers)


def _select_subscription_cost(costs: CostTotals, *, basis: str) -> Decimal:
    if basis == "source":
        return costs.source_cost_usd
    if basis == "actual":
        return costs.actual_cost_usd
    if basis == "virtual":
        return costs.virtual_cost_usd
    msg = f"Unsupported subscription cost basis: {basis}"
    raise ValueError(msg)


def _provider_usage_timestamps(
    conn: sqlite3.Connection,
    *,
    provider_ids: tuple[str, ...],
    since_ms: int,
    until_ms: int,
) -> list[int]:
    if not provider_ids:
        return []
    placeholders = ", ".join("?" for _ in provider_ids)
    rows = conn.execute(
        f"""
        SELECT created_ms
        FROM usage_events
        WHERE provider_id IN ({placeholders})
          AND created_ms >= ?
          AND created_ms <= ?
        ORDER BY created_ms ASC
        """,
        (*provider_ids, since_ms, until_ms),
    ).fetchall()
    return [_required_int(row["created_ms"]) for row in rows]


def _summarize_window_requests(
    conn: sqlite3.Connection,
    *,
    requests: tuple[_SubscriptionWindowRequest, ...],
    runtime: CostingRuntime,
) -> dict[str, _WindowUsageSummary]:
    if not requests:
        return {}

    value_rows: list[tuple[str, str, int, int]] = []
    for request in requests:
        for provider_id in request.provider_ids:
            value_rows.append(
                (
                    request.request_id,
                    provider_id,
                    request.since_ms,
                    request.until_ms,
                )
            )
    if not value_rows:
        return {request.request_id: _WindowUsageSummary() for request in requests}

    values_sql = ", ".join("(?, ?, ?, ?)" for _ in value_rows)
    params: list[object] = []
    for row in value_rows:
        params.extend(row)

    rows = conn.execute(
        f"""
        WITH win(request_id, provider_id, since_ms, until_ms) AS (
            VALUES {values_sql}
        )
        SELECT
            win.request_id AS request_id,
            ue.harness AS harness,
            ue.provider_id AS provider_id,
            ue.model_id AS model_id,
            (
                ue.input_tokens + ue.cache_read_tokens + ue.cache_write_tokens
            ) AS context_tokens,
            COUNT(*) AS message_count,
            SUM(ue.input_tokens) AS input_tokens,
            SUM(ue.output_tokens) AS output_tokens,
            SUM(ue.reasoning_tokens) AS reasoning_tokens,
            SUM(ue.cache_read_tokens) AS cache_read_tokens,
            SUM(ue.cache_write_tokens) AS cache_write_tokens,
            SUM(ue.cache_output_tokens) AS cache_output_tokens,
            DECIMAL_SUM(ue.source_cost_usd) AS source_cost_usd
        FROM win
        JOIN usage_events AS ue
          ON ue.provider_id = win.provider_id
         AND ue.created_ms >= win.since_ms
         AND ue.created_ms < win.until_ms
        GROUP BY
            win.request_id,
            ue.harness,
            ue.provider_id,
            ue.model_id,
            context_tokens
        """,
        tuple(params),
    ).fetchall()

    summaries: dict[str, _WindowUsageSummary] = {}
    for request in requests:
        summaries[request.request_id] = _WindowUsageSummary()

    for row in rows:
        request_id = str(row["request_id"])
        summary = summaries.setdefault(request_id, _WindowUsageSummary())
        row_tokens = TokenBreakdown(
            input=_required_int(row["input_tokens"]),
            output=_required_int(row["output_tokens"]),
            reasoning=_required_int(row["reasoning_tokens"]),
            cache_read=_required_int(row["cache_read_tokens"]),
            cache_write=_required_int(row["cache_write_tokens"]),
            cache_output=_required_int(row["cache_output_tokens"]),
        )
        row_message_count = _required_int(row["message_count"])
        row_source_cost = _required_decimal(row["source_cost_usd"])
        row_breakdown = runtime.compute_costs(
            harness=str(row["harness"]),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            tokens=row_tokens,
            source_cost_usd=row_source_cost,
            message_count=row_message_count,
        )
        row_costs = CostTotals().add(
            source_cost_usd=row_breakdown.source_cost_usd,
            actual_cost_usd=row_breakdown.actual_cost_usd,
            virtual_cost_usd=row_breakdown.virtual_cost_usd,
            unpriced_count=row_breakdown.unpriced_count,
        )

        summary.message_count += row_message_count
        summary.tokens = _add_tokens(summary.tokens, row_tokens)
        summary.costs = summary.costs.add(
            source_cost_usd=row_breakdown.source_cost_usd,
            actual_cost_usd=row_breakdown.actual_cost_usd,
            virtual_cost_usd=row_breakdown.virtual_cost_usd,
            unpriced_count=row_breakdown.unpriced_count,
        )

        model_key = (str(row["provider_id"]), str(row["model_id"]))
        existing = summary.by_model.get(model_key)
        if existing is None:
            summary.by_model[model_key] = (row_message_count, row_costs)
        else:
            existing_count, existing_costs = existing
            summary.by_model[model_key] = (
                existing_count + row_message_count,
                existing_costs.add(
                    source_cost_usd=row_breakdown.source_cost_usd,
                    actual_cost_usd=row_breakdown.actual_cost_usd,
                    virtual_cost_usd=row_breakdown.virtual_cost_usd,
                    unpriced_count=row_breakdown.unpriced_count,
                ),
            )

    return summaries


def summarize_usage_series(
    conn: sqlite3.Connection,
    filters: UsageSeriesFilter,
    *,
    costing_config: CostingConfig | None = None,
) -> UsageSeriesReport:

    from toktrail.periods import (
        TimeBucket,
        iter_time_buckets,
        resolve_timezone,
    )

    tz = resolve_timezone(timezone_name=filters.timezone_name, utc=filters.utc)

    usage_filters, _ = _apply_tracking_session_time_window(
        conn,
        filters.to_usage_report_filter(),
    )
    usage_filters = _resolve_usage_area_filter(conn, usage_filters)
    where = _usage_where_parts(usage_filters)
    bounds = conn.execute(
        f"""
        SELECT
            MIN(ue.created_ms) AS min_created_ms,
            MAX(ue.created_ms) AS max_created_ms
        {where.source_clause}
        {where.where_clause}
        """,
        where.params,
    ).fetchone()
    if (
        bounds is None
        or bounds["min_created_ms"] is None
        or bounds["max_created_ms"] is None
    ):
        return UsageSeriesReport(
            granularity=filters.granularity,
            timezone=str(tz),
            locale=filters.locale,
            start_of_week=filters.start_of_week,
            filters={
                "since_ms": usage_filters.since_ms,
                "until_ms": usage_filters.until_ms,
                "machine_id": filters.machine_id,
                "harness": filters.harness,
                "provider_id": filters.provider_id,
                "model_id": filters.model_id,
                "thinking_level": filters.thinking_level,
                "agent": filters.agent,
                "area": usage_filters.area,
                "area_exact": usage_filters.area_exact,
                "unassigned_area": usage_filters.unassigned_area,
                "instances": filters.instances,
                "breakdown": filters.breakdown,
                "split_thinking": filters.split_thinking,
                "order": filters.order,
            },
            buckets=(),
            instances=(),
            totals=SessionTotals(tokens=TokenBreakdown(), costs=CostTotals()),
        )

    min_created_ms = _required_int(bounds["min_created_ms"])
    max_created_ms = _required_int(bounds["max_created_ms"])
    range_since_ms = (
        usage_filters.since_ms if usage_filters.since_ms is not None else min_created_ms
    )
    range_until_ms = (
        usage_filters.until_ms
        if usage_filters.until_ms is not None
        else max_created_ms + 1
    )
    if range_since_ms >= range_until_ms:
        return UsageSeriesReport(
            granularity=filters.granularity,
            timezone=str(tz),
            locale=filters.locale,
            start_of_week=filters.start_of_week,
            filters={
                "since_ms": usage_filters.since_ms,
                "until_ms": usage_filters.until_ms,
                "machine_id": filters.machine_id,
                "harness": filters.harness,
                "provider_id": filters.provider_id,
                "model_id": filters.model_id,
                "thinking_level": filters.thinking_level,
                "agent": filters.agent,
                "area": usage_filters.area,
                "area_exact": usage_filters.area_exact,
                "unassigned_area": usage_filters.unassigned_area,
                "instances": filters.instances,
                "breakdown": filters.breakdown,
                "split_thinking": filters.split_thinking,
                "order": filters.order,
            },
            buckets=(),
            instances=(),
            totals=SessionTotals(tokens=TokenBreakdown(), costs=CostTotals()),
        )

    time_buckets = iter_time_buckets(
        granularity=filters.granularity,
        since_ms=range_since_ms,
        until_ms=range_until_ms,
        tz=tz,
        start_of_week=filters.start_of_week,
        locale=filters.locale,
    )
    if not time_buckets:
        return UsageSeriesReport(
            granularity=filters.granularity,
            timezone=str(tz),
            locale=filters.locale,
            start_of_week=filters.start_of_week,
            filters={
                "since_ms": usage_filters.since_ms,
                "until_ms": usage_filters.until_ms,
                "machine_id": filters.machine_id,
                "harness": filters.harness,
                "provider_id": filters.provider_id,
                "model_id": filters.model_id,
                "thinking_level": filters.thinking_level,
                "agent": filters.agent,
                "area": usage_filters.area,
                "area_exact": usage_filters.area_exact,
                "unassigned_area": usage_filters.unassigned_area,
                "instances": filters.instances,
                "breakdown": filters.breakdown,
                "split_thinking": filters.split_thinking,
                "order": filters.order,
            },
            buckets=(),
            instances=(),
            totals=SessionTotals(tokens=TokenBreakdown(), costs=CostTotals()),
        )

    bucket_values_sql = ", ".join("(?, ?, ?, ?)" for _ in time_buckets)
    bucket_params: list[object] = []
    for bucket in time_buckets:
        bucket_params.extend(
            (bucket.key, bucket.label, bucket.since_ms, bucket.until_ms)
        )

    series_rows = conn.execute(
        f"""
        WITH bucket(key, label, since_ms, until_ms) AS (
            VALUES {bucket_values_sql}
        )
        SELECT
            bucket.key AS bucket_key,
            bucket.label AS bucket_label,
            bucket.since_ms AS bucket_since_ms,
            bucket.until_ms AS bucket_until_ms,
            ue.harness AS harness,
            ue.source_session_id AS source_session_id,
            ue.provider_id AS provider_id,
            ue.model_id AS model_id,
            CASE WHEN ? THEN ue.thinking_level ELSE NULL END AS thinking_level,
            ue.agent AS agent,
            (
                ue.input_tokens + ue.cache_read_tokens + ue.cache_write_tokens
            ) AS context_tokens,
            COUNT(*) AS message_count,
            SUM(ue.input_tokens) AS input_tokens,
            SUM(ue.output_tokens) AS output_tokens,
            SUM(ue.reasoning_tokens) AS reasoning_tokens,
            SUM(ue.cache_read_tokens) AS cache_read_tokens,
            SUM(ue.cache_write_tokens) AS cache_write_tokens,
            SUM(ue.cache_output_tokens) AS cache_output_tokens,
            DECIMAL_SUM(ue.source_cost_usd) AS source_cost_usd
        {where.source_clause}
        JOIN bucket
          ON ue.created_ms >= bucket.since_ms
         AND ue.created_ms < bucket.until_ms
        {where.where_clause}
        GROUP BY
            bucket.key,
            bucket.label,
            bucket.since_ms,
            bucket.until_ms,
            ue.harness,
            ue.source_session_id,
            ue.provider_id,
            ue.model_id,
            thinking_level,
            ue.agent,
            context_tokens
        ORDER BY bucket.since_ms ASC
        """,
        (*bucket_params, int(filters.split_thinking), *where.params),
    ).fetchall()

    config = costing_config or default_costing_config()
    runtime = compile_costing_config(config)

    bucket_data: dict[str, _SeriesBucketAccum] = {}
    model_bucket_data: dict[tuple[str, str, str, str | None], _SeriesModelAccum] = {}
    instance_data: dict[str, _SeriesInstanceAccum] = {}

    for row in series_rows:
        harness = str(row["harness"])
        source_session_id = str(row["source_session_id"])
        bucket = TimeBucket(
            key=str(row["bucket_key"]),
            label=str(row["bucket_label"]),
            since_ms=_required_int(row["bucket_since_ms"]),
            until_ms=_required_int(row["bucket_until_ms"]),
        )
        tokens = TokenBreakdown(
            input=_required_int(row["input_tokens"]),
            output=_required_int(row["output_tokens"]),
            reasoning=_required_int(row["reasoning_tokens"]),
            cache_read=_required_int(row["cache_read_tokens"]),
            cache_write=_required_int(row["cache_write_tokens"]),
            cache_output=_required_int(row["cache_output_tokens"]),
        )
        atom = UsageCostAtom(
            harness=harness,
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            thinking_level=(
                str(row["thinking_level"])
                if row["thinking_level"] is not None and filters.split_thinking
                else None
            ),
            agent=str(row["agent"]) if row["agent"] is not None else None,
            message_count=_required_int(row["message_count"]),
            tokens=tokens,
            source_cost_usd=_required_decimal(row["source_cost_usd"]),
        )
        breakdown = runtime.compute_costs(
            harness=atom.harness,
            provider_id=atom.provider_id,
            model_id=atom.model_id,
            tokens=atom.tokens,
            source_cost_usd=atom.source_cost_usd,
            message_count=atom.message_count,
        )
        model_key_str = f"{atom.provider_id}/{atom.model_id}"

        bucket_data.setdefault(bucket.key, _SeriesBucketAccum(bucket=bucket)).add(
            atom, breakdown, model_key_str
        )

        if filters.instances:
            inst_key = f"{harness}/{source_session_id}"
            inst_label = source_session_id

        if filters.breakdown:
            model_key = ("", bucket.key, atom.provider_id, atom.model_id)
            if filters.instances:
                model_key = (inst_key, bucket.key, atom.provider_id, atom.model_id)
            if atom.thinking_level is not None and filters.split_thinking:
                model_key = (
                    model_key[0],
                    bucket.key,
                    atom.provider_id,
                    f"{atom.model_id}[{atom.thinking_level}]",
                )
            model_bucket_data.setdefault(
                model_key, _SeriesModelAccum(bucket_key=bucket.key)
            ).add(atom, breakdown)

        if filters.instances:
            instance_data.setdefault(
                inst_key,
                _SeriesInstanceAccum(
                    instance_key=inst_key,
                    instance_label=inst_label,
                    harness=harness,
                    source_session_id=source_session_id,
                ),
            ).add(atom, breakdown, bucket, model_key_str, filters, config)

    totals_tokens = TokenBreakdown()
    totals_costs = CostTotals()
    for acc in bucket_data.values():
        totals_tokens = _add_tokens(totals_tokens, acc.tokens)
        totals_costs = totals_costs.add(
            source_cost_usd=acc.costs.source_cost_usd,
            actual_cost_usd=acc.costs.actual_cost_usd,
            virtual_cost_usd=acc.costs.virtual_cost_usd,
            unpriced_count=acc.costs.unpriced_count,
        )

    buckets_list = sorted(
        bucket_data.values(),
        key=lambda a: a.bucket.since_ms,
        reverse=(filters.order == "desc"),
    )
    series_buckets: list[UsageSeriesBucket] = []
    for acc in buckets_list:
        by_model_rows: list[ModelSummaryRow] = []
        if filters.breakdown:
            for (ikey, bkey, prov, mid), macc in sorted(model_bucket_data.items()):
                if ikey != "" or bkey != acc.bucket.key:
                    continue
                by_model_rows.append(macc.to_model_row(prov, mid or ""))
        series_buckets.append(
            UsageSeriesBucket(
                key=acc.bucket.key,
                label=acc.bucket.label,
                since_ms=acc.bucket.since_ms,
                until_ms=acc.bucket.until_ms,
                message_count=acc.message_count,
                tokens=acc.tokens,
                costs=acc.costs,
                models=tuple(sorted(acc.model_keys)),
                by_model=tuple(by_model_rows),
            )
        )

    instances: list[UsageSeriesInstance] = []
    for inst in sorted(instance_data.values(), key=lambda i: i.instance_key):
        inst_buckets = inst.build_buckets(model_bucket_data, filters)
        instances.append(
            UsageSeriesInstance(
                instance_key=inst.instance_key,
                instance_label=inst.instance_label,
                harness=inst.harness,
                source_session_id=inst.source_session_id,
                buckets=tuple(
                    sorted(
                        inst_buckets,
                        key=lambda b: b.since_ms,
                        reverse=(filters.order == "desc"),
                    )
                ),
                totals=SessionTotals(
                    tokens=inst.tokens,
                    costs=inst.costs,
                ),
            )
        )

    report_filters: dict[str, object] = {
        "since_ms": usage_filters.since_ms,
        "until_ms": usage_filters.until_ms,
        "machine_id": filters.machine_id,
        "harness": filters.harness,
        "provider_id": filters.provider_id,
        "model_id": filters.model_id,
        "thinking_level": filters.thinking_level,
        "agent": filters.agent,
        "area": usage_filters.area,
        "area_exact": usage_filters.area_exact,
        "unassigned_area": usage_filters.unassigned_area,
        "instances": filters.instances,
        "breakdown": filters.breakdown,
        "split_thinking": filters.split_thinking,
        "order": filters.order,
    }

    return UsageSeriesReport(
        granularity=filters.granularity,
        timezone=str(tz),
        locale=filters.locale,
        start_of_week=filters.start_of_week,
        filters=report_filters,
        buckets=tuple(series_buckets),
        instances=tuple(instances),
        totals=SessionTotals(tokens=totals_tokens, costs=totals_costs),
    )


def summarize_usage_sessions(
    conn: sqlite3.Connection,
    filters: UsageSessionsFilter,
    *,
    costing_config: CostingConfig | None = None,
) -> UsageSessionsReport:
    if filters.order not in ("asc", "desc"):
        msg = f"Invalid order: {filters.order!r}. Use asc or desc."
        raise ValueError(msg)
    if filters.limit is not None and filters.limit < 0:
        msg = f"Invalid limit: {filters.limit}. Must be non-negative."
        raise ValueError(msg)

    usage_filters, _ = _apply_tracking_session_time_window(
        conn,
        filters.to_usage_report_filter(),
    )
    usage_filters = _resolve_usage_area_filter(conn, usage_filters)
    aggregate_rows = _aggregate_usage_rows(
        conn,
        usage_filters,
        group_by=(
            "origin_machine_id",
            "harness",
            "source_session_id",
            "provider_id",
            "model_id",
            "thinking_level",
            "agent",
            "context_tokens",
        ),
        split_thinking=filters.split_thinking,
        include_range=True,
    )

    config = costing_config or default_costing_config()
    runtime = compile_costing_config(config)
    session_area_rows = conn.execute(
        """
        SELECT
            asa.origin_machine_id,
            asa.harness,
            asa.source_session_id,
            a.id AS area_id,
            a.path AS area_path,
            a.name AS area_name
        FROM area_session_assignments AS asa
        JOIN areas AS a ON a.id = asa.area_id
        """
    ).fetchall()
    session_area_lookup = {
        (
            str(row["origin_machine_id"]),
            str(row["harness"]),
            str(row["source_session_id"]),
        ): (
            _required_int(row["area_id"]),
            str(row["area_path"]),
            str(row["area_name"]),
        )
        for row in session_area_rows
    }

    # Per-session aggregation keyed by (origin_machine_id, harness, source_session_id).
    session_atoms: dict[tuple[str, str, str], list[_SessionAtom]] = {}
    machine_labels = machine_label_map(conn)
    machines_by_id = {machine.machine_id: machine for machine in list_machines(conn)}

    for row in aggregate_rows:
        origin_machine_id = row.origin_machine_id
        harness = row.harness
        source_session_id = row.source_session_id
        key = (origin_machine_id or "", harness, source_session_id)

        atom = UsageCostAtom(
            harness=harness,
            provider_id=row.provider_id,
            model_id=row.model_id,
            thinking_level=row.thinking_level if filters.split_thinking else None,
            agent=row.agent,
            message_count=row.message_count,
            tokens=row.tokens,
            source_cost_usd=row.source_cost_usd,
        )
        cost_breakdown = runtime.compute_costs(
            harness=atom.harness,
            provider_id=atom.provider_id,
            model_id=atom.model_id,
            tokens=atom.tokens,
            source_cost_usd=atom.source_cost_usd,
            message_count=atom.message_count,
        )
        session_atoms.setdefault(key, []).append(
            _SessionAtom(
                first_ms=row.first_created_ms
                if row.first_created_ms is not None
                else 0,
                last_ms=row.last_created_ms if row.last_created_ms is not None else 0,
                atom=atom,
                breakdown=cost_breakdown,
            )
        )

    # Build session rows
    session_rows: list[UsageSessionRow] = []
    for (
        origin_machine_id_value,
        harness,
        source_session_id,
    ), atoms in session_atoms.items():
        origin_machine_id = origin_machine_id_value or None
        machine_name = (
            machines_by_id[origin_machine_id].name
            if origin_machine_id is not None and origin_machine_id in machines_by_id
            else None
        )
        machine_label = (
            machine_labels.get(origin_machine_id, f"machine:{origin_machine_id[:8]}")
            if origin_machine_id is not None
            else "unknown"
        )
        session_key = f"{machine_label}/{harness}/{source_session_id}"
        first_ms = min(a.first_ms for a in atoms)
        last_ms = max(a.last_ms for a in atoms)
        message_count = sum(a.atom.message_count for a in atoms)
        tokens = TokenBreakdown()
        costs = CostTotals()
        models: set[str] = set()
        providers: set[str] = set()
        by_model_list: list[ModelSummaryRow] = []
        by_model_accum: dict[tuple[str, str | None], _SessionModelAccum] = {}
        area_id: int | None = None
        area_path: str | None = None
        area_name: str | None = None
        if origin_machine_id is not None:
            area_entry = session_area_lookup.get(
                (origin_machine_id, harness, source_session_id)
            )
            if area_entry is not None:
                area_id, area_path, area_name = area_entry

        for sa in atoms:
            tokens = _add_tokens(tokens, sa.atom.tokens)
            costs = costs.add(
                source_cost_usd=sa.breakdown.source_cost_usd,
                actual_cost_usd=sa.breakdown.actual_cost_usd,
                virtual_cost_usd=sa.breakdown.virtual_cost_usd,
                unpriced_count=sa.breakdown.unpriced_count,
            )
            model_key_str = f"{sa.atom.provider_id}/{sa.atom.model_id}"
            models.add(model_key_str)
            providers.add(sa.atom.provider_id)

            if filters.breakdown:
                mk = (sa.atom.provider_id, sa.atom.model_id)
                by_model_accum.setdefault(mk, _SessionModelAccum()).add(sa)

        if filters.breakdown:
            for (prov, mid), accum in sorted(by_model_accum.items()):
                by_model_list.append(
                    ModelSummaryRow(
                        provider_id=prov,
                        model_id=mid or "",
                        thinking_level=None,
                        message_count=accum.message_count,
                        tokens=accum.tokens,
                        costs=accum.costs,
                    )
                )

        session_rows.append(
            UsageSessionRow(
                key=session_key,
                origin_machine_id=origin_machine_id,
                machine_name=machine_name,
                machine_label=machine_label,
                harness=harness,
                source_session_id=source_session_id,
                area_id=area_id,
                area_path=area_path,
                area_name=area_name,
                first_ms=first_ms,
                last_ms=last_ms,
                message_count=message_count,
                tokens=tokens,
                costs=costs,
                models=tuple(sorted(models)),
                providers=tuple(sorted(providers)),
                by_model=tuple(by_model_list),
            )
        )

    # Sort and limit
    reverse = filters.order == "desc"
    session_rows = sorted(
        session_rows,
        key=lambda r: (r.last_ms, r.harness, r.source_session_id),
        reverse=reverse,
    )
    if filters.limit is not None:
        session_rows = session_rows[: filters.limit]

    # Totals from returned rows only
    totals_tokens = TokenBreakdown()
    totals_costs = CostTotals()
    for session_row in session_rows:
        totals_tokens = _add_tokens(totals_tokens, session_row.tokens)
        totals_costs = totals_costs.add(
            source_cost_usd=session_row.costs.source_cost_usd,
            actual_cost_usd=session_row.costs.actual_cost_usd,
            virtual_cost_usd=session_row.costs.virtual_cost_usd,
            unpriced_count=session_row.costs.unpriced_count,
        )

    report_filters: dict[str, object] = {
        "since_ms": usage_filters.since_ms,
        "until_ms": usage_filters.until_ms,
        "machine_id": filters.machine_id,
        "harness": filters.harness,
        "source_session_id": filters.source_session_id,
        "provider_id": filters.provider_id,
        "model_id": filters.model_id,
        "thinking_level": filters.thinking_level,
        "agent": filters.agent,
        "split_thinking": filters.split_thinking,
        "area": usage_filters.area,
        "area_exact": usage_filters.area_exact,
        "unassigned_area": usage_filters.unassigned_area,
        "limit": filters.limit,
        "order": filters.order,
        "breakdown": filters.breakdown,
    }

    return UsageSessionsReport(
        filters=report_filters,
        sessions=tuple(session_rows),
        totals=SessionTotals(tokens=totals_tokens, costs=totals_costs),
    )


def summarize_usage_areas(
    conn: sqlite3.Connection,
    filters: UsageReportFilter,
    *,
    costing_config: CostingConfig | None = None,
) -> UsageAreasReport:
    usage_filters, _ = _apply_tracking_session_time_window(conn, filters)
    usage_filters = _resolve_usage_area_filter(conn, usage_filters)
    aggregate_rows = _aggregate_usage_rows(
        conn,
        usage_filters,
        group_by=(
            "area_id",
            "harness",
            "provider_id",
            "model_id",
            "thinking_level",
            "agent",
            "context_tokens",
        ),
        split_thinking=usage_filters.split_thinking,
    )
    config = costing_config or default_costing_config()
    runtime = compile_costing_config(config)
    areas_by_id = _areas_by_id(conn)
    allowed_area_ids = set(usage_filters.area_ids)
    root_depth = usage_filters.area.count("/") if usage_filters.area is not None else 0
    buckets: dict[int | None, _ReportBucket] = {}
    totals_tokens = TokenBreakdown()
    totals_costs = CostTotals()

    for row in aggregate_rows:
        atom = UsageCostAtom(
            harness=row.harness,
            provider_id=row.provider_id,
            model_id=row.model_id,
            thinking_level=row.thinking_level if usage_filters.split_thinking else None,
            agent=row.agent,
            message_count=row.message_count,
            tokens=row.tokens,
            source_cost_usd=row.source_cost_usd,
        )
        breakdown = runtime.compute_costs(
            harness=atom.harness,
            provider_id=atom.provider_id,
            model_id=atom.model_id,
            tokens=atom.tokens,
            source_cost_usd=atom.source_cost_usd,
            message_count=atom.message_count,
        )
        totals_tokens = _add_tokens(totals_tokens, atom.tokens)
        totals_costs = _add_cost_breakdown(totals_costs, breakdown)
        if row.area_id is None:
            buckets.setdefault(None, _ReportBucket()).add(atom, breakdown)
            continue
        current_area_id: int | None = row.area_id
        while current_area_id is not None:
            if allowed_area_ids and current_area_id not in allowed_area_ids:
                break
            buckets.setdefault(current_area_id, _ReportBucket()).add(atom, breakdown)
            area = areas_by_id.get(current_area_id)
            if area is None:
                break
            current_area_id = area.parent_id

    area_rows: list[AreaSummaryRow] = []
    for area_id, bucket in buckets.items():
        if area_id is None:
            area_rows.append(
                AreaSummaryRow(
                    area_id=None,
                    path=None,
                    name="unassigned",
                    depth=0,
                    message_count=bucket.message_count,
                    tokens=bucket.tokens,
                    costs=bucket.costs,
                )
            )
            continue
        area = areas_by_id.get(area_id)
        if area is None:
            continue
        area_rows.append(
            AreaSummaryRow(
                area_id=area.id,
                path=area.path,
                name=area.name,
                depth=max(area.path.count("/") - root_depth, 0),
                message_count=bucket.message_count,
                tokens=bucket.tokens,
                costs=bucket.costs,
            )
        )

    area_rows = sorted(
        area_rows,
        key=lambda row: (row.path is None, row.path or "unassigned"),
    )
    report_filters: dict[str, object] = {
        "since_ms": usage_filters.since_ms,
        "until_ms": usage_filters.until_ms,
        "machine_id": usage_filters.machine_id,
        "harness": usage_filters.harness,
        "source_session_id": usage_filters.source_session_id,
        "provider_id": usage_filters.provider_id,
        "model_id": usage_filters.model_id,
        "thinking_level": usage_filters.thinking_level,
        "agent": usage_filters.agent,
        "area": usage_filters.area,
        "area_exact": usage_filters.area_exact,
        "unassigned_area": usage_filters.unassigned_area,
        "split_thinking": usage_filters.split_thinking,
    }
    return UsageAreasReport(
        filters=report_filters,
        areas=tuple(area_rows),
        totals=SessionTotals(tokens=totals_tokens, costs=totals_costs),
    )


def _apply_tracking_session_time_window(
    conn: sqlite3.Connection,
    filters: UsageReportFilter,
) -> tuple[UsageReportFilter, TrackingSession | None]:
    """Apply the run lifetime as the default report window for run-scoped reports."""
    if filters.tracking_session_id is None:
        return filters, None

    session = get_tracking_session(conn, filters.tracking_session_id)
    if session is None:
        msg = f"Tracking session not found: {filters.tracking_session_id}"
        raise ValueError(msg)

    since_ms = (
        max(filters.since_ms, session.started_at_ms)
        if filters.since_ms is not None
        else session.started_at_ms
    )
    until_ms = filters.until_ms
    if session.ended_at_ms is not None:
        until_ms = (
            min(until_ms, session.ended_at_ms)
            if until_ms is not None
            else session.ended_at_ms
        )

    return replace(filters, since_ms=since_ms, until_ms=until_ms), session


def _resolve_usage_area_filter(
    conn: sqlite3.Connection,
    filters: UsageReportFilter,
) -> UsageReportFilter:
    if filters.area is not None and filters.unassigned_area:
        msg = "area and unassigned_area cannot both be set."
        raise ValueError(msg)
    if filters.area is None:
        return filters
    normalized_area, _ = normalize_area_path(filters.area)
    area_ids = resolve_area_ids(
        conn,
        normalized_area,
        include_descendants=not filters.area_exact,
    )
    return replace(filters, area=normalized_area, area_ids=area_ids)


def _usage_where_parts(
    filters: UsageReportFilter,
    *,
    alias: str = "ue",
) -> _UsageWhere:
    clauses: list[str] = []
    params: list[object] = []

    source_clause = f" FROM usage_events AS {alias}"
    if filters.tracking_session_id is not None:
        source_clause += f" JOIN run_events AS tse ON tse.usage_event_id = {alias}.id"
        clauses.append("tse.tracking_session_id = ?")
        params.append(filters.tracking_session_id)
    if filters.harness is not None:
        clauses.append(f"{alias}.harness = ?")
        params.append(filters.harness)
    if filters.source_session_id is not None:
        clauses.append(f"{alias}.source_session_id = ?")
        params.append(filters.source_session_id)
    if filters.machine_id is not None:
        clauses.append(f"{alias}.origin_machine_id = ?")
        params.append(filters.machine_id)
    if filters.provider_id is not None and filters.provider_ids:
        msg = "provider_id and provider_ids cannot both be set."
        raise ValueError(msg)
    if filters.provider_id is not None:
        clauses.append(f"{alias}.provider_id = ?")
        params.append(filters.provider_id)
    elif filters.provider_ids:
        normalized_provider_ids = tuple(
            normalize_identity(provider_id) for provider_id in filters.provider_ids
        )
        placeholders = ", ".join("?" for _ in normalized_provider_ids)
        clauses.append(f"{alias}.provider_id IN ({placeholders})")
        params.extend(normalized_provider_ids)
    if filters.model_id is not None:
        clauses.append(f"{alias}.model_id = ?")
        params.append(filters.model_id)
    if filters.thinking_level is not None:
        clauses.append(f"COALESCE({alias}.thinking_level, '') = ?")
        params.append(filters.thinking_level)
    if filters.agent is not None:
        clauses.append(f"{alias}.agent = ?")
        params.append(filters.agent)
    if filters.since_ms is not None:
        clauses.append(f"{alias}.created_ms >= ?")
        params.append(filters.since_ms)
    if filters.until_ms is not None:
        clauses.append(f"{alias}.created_ms < ?")
        params.append(filters.until_ms)
    if filters.unassigned_area:
        clauses.append(f"{alias}.area_id IS NULL")
    elif filters.area is not None:
        if not filters.area_ids:
            clauses.append("1 = 0")
        elif len(filters.area_ids) == 1:
            clauses.append(f"{alias}.area_id = ?")
            params.append(filters.area_ids[0])
        else:
            placeholders = ", ".join("?" for _ in filters.area_ids)
            clauses.append(f"{alias}.area_id IN ({placeholders})")
            params.extend(filters.area_ids)

    where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return _UsageWhere(
        source_clause=source_clause,
        where_clause=where_clause,
        params=tuple(params),
    )


def _usage_report_query_parts(
    filters: UsageReportFilter,
) -> tuple[str, str, list[object]]:
    where = _usage_where_parts(filters)
    return where.source_clause, where.where_clause, list(where.params)


def _aggregate_usage_rows(
    conn: sqlite3.Connection,
    filters: UsageReportFilter,
    *,
    group_by: tuple[str, ...],
    split_thinking: bool,
    include_range: bool = False,
) -> list[_AggregateRow]:
    where = _usage_where_parts(filters)
    column_map = {
        "area_id": "ue.area_id",
        "origin_machine_id": "ue.origin_machine_id",
        "harness": "ue.harness",
        "source_session_id": "ue.source_session_id",
        "provider_id": "ue.provider_id",
        "model_id": "ue.model_id",
        "thinking_level": ("ue.thinking_level" if split_thinking else "NULL"),
        "agent": "ue.agent",
        # Context tokens are needed for exact context-tier pricing when rows
        # are aggregated.
        "context_tokens": (
            "(ue.input_tokens + ue.cache_read_tokens + ue.cache_write_tokens)"
        ),
    }
    select_parts: list[str] = []
    group_parts: list[str] = []
    for key in group_by:
        expr = column_map[key]
        select_parts.append(f"{expr} AS {key}")
        group_parts.append(expr)
    select_sql = ",\n            ".join(select_parts)
    group_sql = ", ".join(group_parts)
    range_select = ""
    if include_range:
        range_select = (
            ", MIN(ue.created_ms) AS first_created_ms, "
            "MAX(ue.created_ms) AS last_created_ms"
        )

    rows = conn.execute(
        f"""
        SELECT
            {select_sql},
            COUNT(*) AS message_count,
            SUM(ue.input_tokens) AS input_tokens,
            SUM(ue.output_tokens) AS output_tokens,
            SUM(ue.reasoning_tokens) AS reasoning_tokens,
            SUM(ue.cache_read_tokens) AS cache_read_tokens,
            SUM(ue.cache_write_tokens) AS cache_write_tokens,
            SUM(ue.cache_output_tokens) AS cache_output_tokens,
            DECIMAL_SUM(ue.source_cost_usd) AS source_cost_usd
            {range_select}
        {where.source_clause}
        {where.where_clause}
        GROUP BY {group_sql}
        """,
        where.params,
    ).fetchall()

    result: list[_AggregateRow] = []
    for row in rows:
        group_values = tuple(row[key] for key in group_by)
        origin_machine_id = (
            str(row["origin_machine_id"])
            if "origin_machine_id" in row.keys()
            and row["origin_machine_id"] is not None
            else None
        )
        result.append(
            _AggregateRow(
                group=group_values,
                area_id=(
                    _required_int(row["area_id"])
                    if "area_id" in row.keys() and row["area_id"] is not None
                    else None
                ),
                origin_machine_id=origin_machine_id,
                harness=(
                    str(row["harness"])
                    if "harness" in row.keys() and row["harness"] is not None
                    else "unknown"
                ),
                source_session_id=(
                    str(row["source_session_id"])
                    if "source_session_id" in row.keys()
                    and row["source_session_id"] is not None
                    else "unknown"
                ),
                provider_id=(
                    str(row["provider_id"])
                    if "provider_id" in row.keys() and row["provider_id"] is not None
                    else "unknown"
                ),
                model_id=(
                    str(row["model_id"])
                    if "model_id" in row.keys() and row["model_id"] is not None
                    else ""
                ),
                thinking_level=(
                    str(row["thinking_level"])
                    if "thinking_level" in row.keys()
                    and row["thinking_level"] is not None
                    else None
                ),
                agent=(
                    str(row["agent"])
                    if "agent" in row.keys() and row["agent"] is not None
                    else None
                ),
                context_tokens=(
                    _required_int(row["context_tokens"])
                    if "context_tokens" in row.keys()
                    else 0
                ),
                message_count=_required_int(row["message_count"]),
                input_tokens=_required_int(row["input_tokens"]),
                output_tokens=_required_int(row["output_tokens"]),
                reasoning_tokens=_required_int(row["reasoning_tokens"]),
                cache_read_tokens=_required_int(row["cache_read_tokens"]),
                cache_write_tokens=_required_int(row["cache_write_tokens"]),
                cache_output_tokens=_required_int(row["cache_output_tokens"]),
                source_cost_usd=_required_decimal(row["source_cost_usd"]),
                first_created_ms=(
                    _required_int(row["first_created_ms"])
                    if include_range and row["first_created_ms"] is not None
                    else None
                ),
                last_created_ms=(
                    _required_int(row["last_created_ms"])
                    if include_range and row["last_created_ms"] is not None
                    else None
                ),
            )
        )
    return result


def _tracking_session_from_row(row: sqlite3.Row) -> TrackingSession:
    return TrackingSession(
        id=_required_int(row["id"]),
        sync_id=str(row["sync_id"]),
        name=str(row["name"]) if row["name"] is not None else None,
        started_at_ms=_required_int(row["started_at_ms"]),
        ended_at_ms=_optional_int(row["ended_at_ms"]),
        scope=normalize_run_scope(
            RunScope(
                harnesses=_json_tuple(
                    row["scope_harnesses_json"]
                    if row["scope_harnesses_json"] is not None
                    else None
                ),
                provider_ids=_json_tuple(
                    row["scope_provider_ids_json"]
                    if row["scope_provider_ids_json"] is not None
                    else None
                ),
                model_ids=_json_tuple(
                    row["scope_model_ids_json"]
                    if row["scope_model_ids_json"] is not None
                    else None
                ),
                source_session_ids=_json_tuple(
                    row["scope_source_session_ids_json"]
                    if row["scope_source_session_ids_json"] is not None
                    else None
                ),
                thinking_levels=_json_tuple(
                    row["scope_thinking_levels_json"]
                    if row["scope_thinking_levels_json"] is not None
                    else None
                ),
                agents=_json_tuple(
                    row["scope_agents_json"]
                    if row["scope_agents_json"] is not None
                    else None
                ),
            )
        ),
        archived_at_ms=_optional_int(row["archived_at_ms"]),
    )


def _group_event_ranges(
    events: list[UsageEvent],
) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    grouped: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for event in events:
        key = (event.harness, event.source_session_id)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = (event.created_ms, event.created_ms)
            continue
        grouped[key] = (
            _min_optional_int(existing[0], event.created_ms),
            _max_optional_int(existing[1], event.created_ms),
        )
    return grouped


def _min_optional_int(left: int | None, right: int | None) -> int | None:
    values = [value for value in (left, right) if value is not None]
    if not values:
        return None
    return min(values)


def _max_optional_int(left: int | None, right: int | None) -> int | None:
    values = [value for value in (left, right) if value is not None]
    if not values:
        return None
    return max(values)


def _row_tokens(row: sqlite3.Row) -> TokenBreakdown:
    return TokenBreakdown(
        input=_required_int(row["input_tokens"]),
        output=_required_int(row["output_tokens"]),
        reasoning=_required_int(row["reasoning_tokens"]),
        cache_read=_required_int(row["cache_read_tokens"]),
        cache_write=_required_int(row["cache_write_tokens"]),
        cache_output=_required_int(row["cache_output_tokens"]),
    )


def _usage_event_from_row(row: sqlite3.Row) -> UsageEvent:
    return UsageEvent(
        harness=str(row["harness"]),
        source_session_id=str(row["source_session_id"]),
        source_row_id=(
            str(row["source_row_id"]) if row["source_row_id"] is not None else None
        ),
        source_message_id=(
            str(row["source_message_id"])
            if row["source_message_id"] is not None
            else None
        ),
        source_dedup_key=str(row["source_dedup_key"]),
        global_dedup_key=str(row["global_dedup_key"]),
        fingerprint_hash=str(row["fingerprint_hash"]),
        provider_id=str(row["provider_id"]),
        model_id=str(row["model_id"]),
        thinking_level=(
            str(row["thinking_level"]) if row["thinking_level"] is not None else None
        ),
        agent=str(row["agent"]) if row["agent"] is not None else None,
        created_ms=_required_int(row["created_ms"]),
        completed_ms=_optional_int(row["completed_ms"]),
        tokens=_row_tokens(row),
        source_cost_usd=_required_decimal(row["source_cost_usd"]),
        raw_json=str(row["raw_json"]) if row["raw_json"] is not None else None,
        origin_machine_id=(
            str(row["origin_machine_id"])
            if "origin_machine_id" in row.keys()
            and row["origin_machine_id"] is not None
            else None
        ),
    )


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"Expected int value, got {value!r}"
        raise TypeError(msg)
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _required_int(value)


def _required_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"Expected numeric value, got {value!r}"
        raise TypeError(msg)
    return float(value)


def _required_decimal(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        msg = f"Expected numeric value, got {value!r}"
        raise TypeError(msg)
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        return Decimal(value)
    if isinstance(value, Decimal):
        return value
    msg = f"Expected numeric value, got {value!r}"
    raise TypeError(msg)


def _source_cost_to_storage(value: Decimal) -> str:
    return str(value)


class _DecimalSum:
    def __init__(self) -> None:
        self.total = Decimal(0)

    def step(self, *values: object) -> None:
        if not values:
            return
        value = values[0]
        if value is None:
            return
        self.total += _required_decimal(value)

    def finalize(self) -> str:
        return str(self.total)


@dataclass
class _ReportBucket:
    message_count: int = 0
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    costs: CostTotals = field(default_factory=CostTotals)

    def add(self, atom: UsageCostAtom, breakdown: CostBreakdown) -> None:
        self.message_count += atom.message_count
        self.tokens = _add_tokens(self.tokens, atom.tokens)
        self.costs = _add_cost_breakdown(self.costs, breakdown)


@dataclass
class _UnconfiguredBucket:
    message_count: int = 0
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)

    def add(self, atom: UsageCostAtom) -> None:
        self.message_count += atom.message_count
        self.tokens = _add_tokens(self.tokens, atom.tokens)


def _add_tokens(left: TokenBreakdown, right: TokenBreakdown) -> TokenBreakdown:
    return TokenBreakdown(
        input=left.input + right.input,
        output=left.output + right.output,
        reasoning=left.reasoning + right.reasoning,
        cache_read=left.cache_read + right.cache_read,
        cache_write=left.cache_write + right.cache_write,
        cache_output=left.cache_output + right.cache_output,
    )


def _add_cost_breakdown(costs: CostTotals, breakdown: CostBreakdown) -> CostTotals:
    return costs.add(
        source_cost_usd=breakdown.source_cost_usd,
        actual_cost_usd=breakdown.actual_cost_usd,
        virtual_cost_usd=breakdown.virtual_cost_usd,
        unpriced_count=breakdown.unpriced_count,
    )


def _required_lastrowid(value: int | None) -> int:
    if value is None:
        msg = "SQLite insert did not return a row id."
        raise TypeError(msg)
    return value


class _SeriesBucketAccum:
    __slots__ = ("bucket", "message_count", "tokens", "costs", "model_keys")

    def __init__(self, bucket: object) -> None:
        from toktrail.periods import TimeBucket

        assert isinstance(bucket, TimeBucket)
        self.bucket = bucket
        self.message_count = 0
        self.tokens = TokenBreakdown()
        self.costs = CostTotals()
        self.model_keys: set[str] = set()

    def add(
        self,
        atom: UsageCostAtom,
        breakdown: CostBreakdown,
        model_key: str,
    ) -> None:
        self.message_count += atom.message_count
        self.tokens = _add_tokens(self.tokens, atom.tokens)
        self.costs = _add_cost_breakdown(self.costs, breakdown)
        self.model_keys.add(model_key)


class _SeriesModelAccum:
    __slots__ = ("bucket_key", "message_count", "tokens", "costs")

    def __init__(self, bucket_key: str) -> None:
        self.bucket_key = bucket_key
        self.message_count = 0
        self.tokens = TokenBreakdown()
        self.costs = CostTotals()

    def add(self, atom: UsageCostAtom, breakdown: CostBreakdown) -> None:
        self.message_count += atom.message_count
        self.tokens = _add_tokens(self.tokens, atom.tokens)
        self.costs = _add_cost_breakdown(self.costs, breakdown)

    def to_model_row(
        self,
        provider_id: str,
        model_id: str,
    ) -> ModelSummaryRow:
        return ModelSummaryRow(
            provider_id=provider_id,
            model_id=model_id,
            thinking_level=None,
            message_count=self.message_count,
            tokens=self.tokens,
            costs=self.costs,
        )


class _SeriesInstanceAccum:
    __slots__ = (
        "instance_key",
        "instance_label",
        "harness",
        "source_session_id",
        "message_count",
        "tokens",
        "costs",
        "bucket_data",
        "model_keys",
    )

    def __init__(
        self,
        instance_key: str,
        instance_label: str,
        harness: str,
        source_session_id: str,
    ) -> None:
        self.instance_key = instance_key
        self.instance_label = instance_label
        self.harness = harness
        self.source_session_id = source_session_id
        self.message_count = 0
        self.tokens = TokenBreakdown()
        self.costs = CostTotals()
        self.bucket_data: dict[str, _SeriesBucketAccum] = {}
        self.model_keys: set[str] = set()

    def add(
        self,
        atom: UsageCostAtom,
        breakdown: CostBreakdown,
        bucket: object,
        model_key: str,
        filters: UsageSeriesFilter,
        config: CostingConfig,
    ) -> None:
        from toktrail.periods import TimeBucket

        assert isinstance(bucket, TimeBucket)
        self.message_count += atom.message_count
        self.tokens = _add_tokens(self.tokens, atom.tokens)
        self.costs = _add_cost_breakdown(self.costs, breakdown)
        self.model_keys.add(model_key)
        bacc = self.bucket_data.setdefault(
            bucket.key, _SeriesBucketAccum(bucket=bucket)
        )
        bacc.add(atom, breakdown, model_key)

    def build_buckets(
        self,
        model_bucket_data: dict[tuple[str, str, str, str | None], _SeriesModelAccum],
        filters: UsageSeriesFilter,
    ) -> list[UsageSeriesBucket]:
        result: list[UsageSeriesBucket] = []
        for acc in self.bucket_data.values():
            by_model_rows: list[ModelSummaryRow] = []
            if filters.breakdown:
                for (ikey, bkey, prov, mid), macc in sorted(model_bucket_data.items()):
                    if ikey != self.instance_key or bkey != acc.bucket.key:
                        continue
                    by_model_rows.append(macc.to_model_row(prov, mid or ""))
            result.append(
                UsageSeriesBucket(
                    key=acc.bucket.key,
                    label=acc.bucket.label,
                    since_ms=acc.bucket.since_ms,
                    until_ms=acc.bucket.until_ms,
                    message_count=acc.message_count,
                    tokens=acc.tokens,
                    costs=acc.costs,
                    models=tuple(sorted(acc.model_keys)),
                    by_model=tuple(by_model_rows),
                )
            )
        return result


@dataclass(frozen=True)
class _SessionAtom:
    first_ms: int
    last_ms: int
    atom: UsageCostAtom
    breakdown: CostBreakdown


@dataclass(frozen=True)
class _RunAtom:
    run_name: str | None
    origin_machine_id: str | None
    started_at_ms: int
    ended_at_ms: int | None
    atom: UsageCostAtom
    breakdown: CostBreakdown


class _SessionModelAccum:
    __slots__ = ("message_count", "tokens", "costs")

    def __init__(self) -> None:
        self.message_count = 0
        self.tokens = TokenBreakdown()
        self.costs = CostTotals()

    def add(self, sa: _SessionAtom) -> None:
        self.message_count += sa.atom.message_count
        self.tokens = _add_tokens(self.tokens, sa.atom.tokens)
        self.costs = _add_cost_breakdown(self.costs, sa.breakdown)


@dataclass(frozen=True)
class _SubscriptionWindowRequest:
    request_id: str
    subscription_id: str
    kind: str
    period: str
    provider_ids: tuple[str, ...]
    since_ms: int
    until_ms: int
    quota_cost_basis: str


@dataclass
class _WindowUsageSummary:
    message_count: int = 0
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    costs: CostTotals = field(default_factory=CostTotals)
    by_model: dict[tuple[str, str], tuple[int, CostTotals]] = field(
        default_factory=dict
    )


def _run_archive_filter_sql(filters: UsageRunsFilter, where_clause: str) -> str:
    run_filter_clauses: list[str] = []
    if filters.archived_only:
        run_filter_clauses.append("r.archived_at_ms IS NOT NULL")
    elif not filters.include_archived:
        run_filter_clauses.append("r.archived_at_ms IS NULL")
    if not run_filter_clauses:
        return ""
    if where_clause:
        return " AND " + " AND ".join(run_filter_clauses)
    return " WHERE " + " AND ".join(run_filter_clauses)


def _build_runs_where_clause(
    usage_filters: UsageReportFilter,
) -> tuple[str, list[object]]:

    clauses: list[str] = []
    params: list[object] = []
    if usage_filters.harness is not None:
        clauses.append("ue.harness = ?")
        params.append(usage_filters.harness)
    if usage_filters.machine_id is not None:
        clauses.append("ue.origin_machine_id = ?")
        params.append(usage_filters.machine_id)
    if usage_filters.provider_id is not None:
        clauses.append("ue.provider_id = ?")
        params.append(usage_filters.provider_id)
    elif usage_filters.provider_ids:
        normalized = tuple(
            normalize_identity(pid) for pid in usage_filters.provider_ids
        )
        ph = ", ".join("?" for _ in normalized)
        clauses.append(f"ue.provider_id IN ({ph})")
        params.extend(normalized)
    if usage_filters.model_id is not None:
        clauses.append("ue.model_id = ?")
        params.append(usage_filters.model_id)
    if usage_filters.thinking_level is not None:
        clauses.append("COALESCE(ue.thinking_level, '') = ?")
        params.append(usage_filters.thinking_level)
    if usage_filters.agent is not None:
        clauses.append("ue.agent = ?")
        params.append(usage_filters.agent)
    if usage_filters.since_ms is not None:
        clauses.append("ue.created_ms >= ?")
        params.append(usage_filters.since_ms)
    if usage_filters.until_ms is not None:
        clauses.append("ue.created_ms < ?")
        params.append(usage_filters.until_ms)
    if usage_filters.unassigned_area:
        clauses.append("ue.area_id IS NULL")
    elif usage_filters.area is not None:
        if len(usage_filters.area_ids) == 1:
            clauses.append("ue.area_id = ?")
            params.append(usage_filters.area_ids[0])
        elif usage_filters.area_ids:
            ph = ", ".join("?" for _ in usage_filters.area_ids)
            clauses.append(f"ue.area_id IN ({ph})")
            params.extend(usage_filters.area_ids)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def summarize_usage_runs(
    conn: sqlite3.Connection,
    filters: UsageRunsFilter,
    *,
    costing_config: CostingConfig | None = None,
) -> UsageRunsReport:
    from toktrail.reporting import UsageRunRow, UsageRunsReport

    if filters.order not in ("asc", "desc"):
        msg = f"Invalid order: {filters.order!r}. Use asc or desc."
        raise ValueError(msg)
    if filters.limit is not None and filters.limit < 0:
        msg = f"Invalid limit: {filters.limit}. Must be non-negative."
        raise ValueError(msg)

    usage_filters, _ = _apply_tracking_session_time_window(
        conn,
        filters.to_usage_report_filter(),
    )
    usage_filters = _resolve_usage_area_filter(conn, usage_filters)
    where_clause, params = _build_runs_where_clause(usage_filters)
    run_filter_sql = _run_archive_filter_sql(filters, where_clause)

    thinking_select = (
        "ue.thinking_level AS thinking_level"
        if filters.split_thinking
        else "NULL AS thinking_level"
    )

    atom_rows = conn.execute(
        """
        SELECT
            r.id AS run_id,
            r.name AS run_name,
            r.origin_machine_id AS run_origin_machine_id,
            r.started_at_ms,
            r.ended_at_ms,
            ue.harness,
            ue.provider_id,
            ue.model_id,
        """
        + thinking_select
        + """
            ,
            ue.agent AS agent,
            ue.created_ms,
            ue.completed_ms,
            ue.input_tokens,
            ue.output_tokens,
            ue.reasoning_tokens,
            ue.cache_read_tokens,
            ue.cache_write_tokens,
            ue.cache_output_tokens,
            ue.source_cost_usd
        FROM usage_events AS ue
        JOIN run_events AS re ON re.usage_event_id = ue.id
        JOIN runs AS r ON r.id = re.tracking_session_id
        """
        + where_clause
        + run_filter_sql
        + """
        ORDER BY r.started_at_ms DESC, r.id DESC, ue.created_ms ASC, ue.id ASC
        """,
        params,
    ).fetchall()

    config = costing_config or default_costing_config()
    runtime = compile_costing_config(config)
    machine_labels = machine_label_map(conn)
    machines_by_id = {machine.machine_id: machine for machine in list_machines(conn)}

    run_atoms: dict[int, list[_RunAtom]] = {}

    for row in atom_rows:
        run_id = _required_int(row["run_id"])
        atom = UsageCostAtom(
            harness=str(row["harness"]),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            thinking_level=(
                str(row["thinking_level"])
                if row["thinking_level"] is not None
                else None
            ),
            agent=row["agent"],
            message_count=1,
            tokens=_row_tokens(row),
            source_cost_usd=_required_decimal(row["source_cost_usd"]),
        )
        breakdown = runtime.compute_costs(
            harness=atom.harness,
            provider_id=atom.provider_id,
            model_id=atom.model_id,
            tokens=atom.tokens,
            source_cost_usd=atom.source_cost_usd,
            message_count=atom.message_count,
        )
        run_atoms.setdefault(run_id, []).append(
            _RunAtom(
                run_name=row["run_name"],
                origin_machine_id=(
                    str(row["run_origin_machine_id"])
                    if row["run_origin_machine_id"] is not None
                    else None
                ),
                started_at_ms=_required_int(row["started_at_ms"]),
                ended_at_ms=(
                    _required_int(row["ended_at_ms"])
                    if row["ended_at_ms"] is not None
                    else None
                ),
                atom=atom,
                breakdown=breakdown,
            )
        )

    rows: list[UsageRunRow] = []
    for run_id, atoms in run_atoms.items():
        name = atoms[0].run_name
        origin_machine_id = atoms[0].origin_machine_id
        machine_name = (
            machines_by_id[origin_machine_id].name
            if origin_machine_id is not None and origin_machine_id in machines_by_id
            else None
        )
        machine_label = (
            machine_labels.get(origin_machine_id, f"machine:{origin_machine_id[:8]}")
            if origin_machine_id is not None
            else "unknown"
        )
        started_at_ms = min(a.started_at_ms for a in atoms)
        ended_ms_values = [a.ended_at_ms for a in atoms if a.ended_at_ms is not None]
        ended_at_ms = max(ended_ms_values) if ended_ms_values else None
        message_count = sum(a.atom.message_count for a in atoms)
        tokens = TokenBreakdown()
        costs = CostTotals()
        models: set[str] = set()
        providers: set[str] = set()
        for a in atoms:
            tokens = _add_tokens(tokens, a.atom.tokens)
            costs = costs.add(
                source_cost_usd=a.breakdown.source_cost_usd,
                actual_cost_usd=a.breakdown.actual_cost_usd,
                virtual_cost_usd=a.breakdown.virtual_cost_usd,
                unpriced_count=a.breakdown.unpriced_count,
            )
            models.add(f"{a.atom.provider_id}/{a.atom.model_id}")
            providers.add(a.atom.provider_id)
        rows.append(
            UsageRunRow(
                run_id=run_id,
                name=name,
                origin_machine_id=origin_machine_id,
                machine_name=machine_name,
                machine_label=machine_label,
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
                message_count=message_count,
                tokens=tokens,
                costs=costs,
                models=tuple(sorted(models)),
                providers=tuple(sorted(providers)),
            )
        )

    reverse = filters.order == "desc"
    rows = sorted(rows, key=lambda r: r.started_at_ms, reverse=reverse)
    if filters.last:
        rows = rows[:1]
    elif filters.limit is not None:
        rows = rows[: filters.limit]

    totals_tokens = TokenBreakdown()
    totals_costs = CostTotals()
    for row in rows:
        totals_tokens = _add_tokens(totals_tokens, row.tokens)
        totals_costs = totals_costs.add(
            source_cost_usd=row.costs.source_cost_usd,
            actual_cost_usd=row.costs.actual_cost_usd,
            virtual_cost_usd=row.costs.virtual_cost_usd,
            unpriced_count=row.costs.unpriced_count,
        )

    report_filters: dict[str, object] = {
        "since_ms": usage_filters.since_ms,
        "until_ms": usage_filters.until_ms,
        "machine_id": usage_filters.machine_id,
        "provider_id": filters.provider_id,
        "model_id": filters.model_id,
        "thinking_level": filters.thinking_level,
        "agent": filters.agent,
        "area": usage_filters.area,
        "area_exact": usage_filters.area_exact,
        "unassigned_area": usage_filters.unassigned_area,
        "split_thinking": filters.split_thinking,
        "limit": filters.limit,
        "order": filters.order,
        "include_archived": filters.include_archived,
        "archived_only": filters.archived_only,
    }

    return UsageRunsReport(
        filters=report_filters,
        runs=tuple(rows),
        totals=SessionTotals(tokens=totals_tokens, costs=totals_costs),
    )
