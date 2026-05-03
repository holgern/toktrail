from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from time import time

from toktrail.config import CostingConfig, default_costing_config, normalize_identity
from toktrail.costing import (
    CostBreakdown,
    SimulationTarget,
    UsageCostAtom,
    resolve_price_resolution,
    simulate_cost,
)
from toktrail.models import Run as TrackingSession
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import (
    ActivitySummaryRow,
    CostTotals,
    HarnessSummaryRow,
    ModelSummaryRow,
    ProviderSummaryRow,
    RunReport,
    SessionTotals,
    SimulationSummaryRow,
    SubscriptionUsagePeriod,
    SubscriptionUsageReport,
    SubscriptionUsageRow,
    UnconfiguredModelRow,
    UsageReportFilter,
    UsageSeriesBucket,
    UsageSeriesFilter,
    UsageSeriesInstance,
    UsageSeriesReport,
)

SCHEMA_VERSION = 3
_PERIOD_SORT: dict[str, int] = {"5h": 0, "daily": 1, "weekly": 2, "monthly": 3}


@dataclass(frozen=True)
class InsertUsageResult:
    rows_inserted: int
    rows_linked: int = 0
    rows_skipped: int = 0


def _now_ms() -> int:
    return int(time() * 1000)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.create_aggregate("DECIMAL_SUM", 1, _DecimalSum)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current_version = _read_user_version(conn)
    if current_version == 0:
        _create_schema(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return
    if current_version == 1:
        _migrate_v1_to_v2(conn)
        if SCHEMA_VERSION >= 3:
            _migrate_v2_to_v3(conn)
            conn.execute("PRAGMA user_version = 3")
        else:
            conn.execute("PRAGMA user_version = 2")
        conn.commit()
        return
    if current_version == 2 and SCHEMA_VERSION == 3:
        _migrate_v2_to_v3(conn)
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        return
    if current_version == SCHEMA_VERSION:
        return
    msg = (
        f"Unsupported pre-release toktrail schema version {current_version}; "
        "delete the state DB or export/import manually before first release."
    )
    raise ValueError(msg)


def _read_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    if row is None:
        msg = "Could not read SQLite user_version."
        raise ValueError(msg)
    return _required_int(row[0])


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            started_at_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_runs_started
        ON runs(started_at_ms);

        CREATE TABLE IF NOT EXISTS source_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            role TEXT NOT NULL DEFAULT 'assistant',
            provider_id TEXT NOT NULL DEFAULT 'unknown',
            model_id TEXT NOT NULL,
            thinking_level TEXT,
            agent TEXT,
            created_ms INTEGER NOT NULL,
            completed_ms INTEGER,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            source_cost_usd TEXT NOT NULL DEFAULT '0',
            raw_json TEXT,
            imported_at_ms INTEGER NOT NULL,
            UNIQUE(harness, global_dedup_key)
        );

        CREATE INDEX IF NOT EXISTS idx_usage_events_tracking_session
        ON usage_events(tracking_session_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_harness_session
        ON usage_events(harness, source_session_id, created_ms);

        CREATE INDEX IF NOT EXISTS idx_usage_events_fingerprint
        ON usage_events(harness, fingerprint_hash);

        CREATE INDEX IF NOT EXISTS idx_usage_events_model_thinking
        ON usage_events(provider_id, model_id, thinking_level);

        CREATE INDEX IF NOT EXISTS idx_usage_events_provider_created
        ON usage_events(provider_id, created_ms);

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


def create_tracking_session(
    conn: sqlite3.Connection,
    name: str | None,
    *,
    started_at_ms: int | None = None,
) -> int:
    active_session_id = get_active_tracking_session(conn)
    if active_session_id is not None:
        msg = f"Tracking session {active_session_id} is already active."
        raise ValueError(msg)
    now_ms = started_at_ms if started_at_ms is not None else _now_ms()
    cursor = conn.execute(
        """
        INSERT INTO runs (
            name,
            started_at_ms,
            created_at_ms,
            updated_at_ms
        )
        VALUES (?, ?, ?, ?)
        """,
        (name, now_ms, now_ms, now_ms),
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
        SELECT id, name, started_at_ms, ended_at_ms
        FROM runs
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _tracking_session_from_row(row)


def list_tracking_sessions(conn: sqlite3.Connection) -> list[TrackingSession]:
    rows = conn.execute(
        """
        SELECT id, name, started_at_ms, ended_at_ms
        FROM runs
        ORDER BY started_at_ms DESC, id DESC
        """
    ).fetchall()
    return [_tracking_session_from_row(row) for row in rows]


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
    existing = conn.execute(
        """
        SELECT id, first_seen_ms, last_seen_ms
        FROM source_sessions
        WHERE tracking_session_id = ? AND harness = ? AND source_session_id = ?
        """,
        (tracking_session_id, harness, source_session_id),
    ).fetchone()
    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO source_sessions (
                tracking_session_id,
                harness,
                source_session_id,
                first_seen_ms,
                last_seen_ms,
                created_at_ms,
                updated_at_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
        SET first_seen_ms = ?, last_seen_ms = ?, updated_at_ms = ?
        WHERE id = ?
        """,
        (merged_first, merged_last, now_ms, _required_int(existing["id"])),
    )
    return _required_int(existing["id"])


def insert_usage_events(
    conn: sqlite3.Connection,
    tracking_session_id: int | None,
    events: list[UsageEvent],
    *,
    since_ms: int | None = None,
) -> InsertUsageResult:
    filtered_events = [
        event for event in events if since_ms is None or event.created_ms >= since_ms
    ]
    harness_session_ids: dict[tuple[str, str], int] = {}
    rows_inserted = 0
    rows_linked = 0
    imported_at_ms = _now_ms()

    with conn:
        if tracking_session_id is not None:
            grouped_ranges = _group_event_ranges(filtered_events)
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

        for event in filtered_events:
            harness_session_id = harness_session_ids.get(
                (event.harness, event.source_session_id)
            )
            cursor = conn.execute(
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
                    source_cost_usd,
                    raw_json,
                    imported_at_ms
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, 'assistant',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    tracking_session_id,
                    harness_session_id,
                    event.harness,
                    event.source_session_id,
                    event.source_row_id,
                    event.source_message_id,
                    event.source_dedup_key,
                    event.global_dedup_key,
                    event.fingerprint_hash,
                    event.provider_id,
                    event.model_id,
                    event.thinking_level,
                    event.agent,
                    event.created_ms,
                    event.completed_ms,
                    event.tokens.input,
                    event.tokens.output,
                    event.tokens.reasoning,
                    event.tokens.cache_read,
                    event.tokens.cache_write,
                    _source_cost_to_storage(event.source_cost_usd),
                    event.raw_json,
                    imported_at_ms,
                ),
            )
            rows_inserted += cursor.rowcount
            if tracking_session_id is None:
                continue
            event_row = conn.execute(
                """
                SELECT id
                FROM usage_events
                WHERE harness = ? AND global_dedup_key = ?
                """,
                (event.harness, event.global_dedup_key),
            ).fetchone()
            if event_row is None:
                msg = (
                    "Inserted usage event row could not be reloaded for "
                    f"{event.harness}:{event.global_dedup_key}"
                )
                raise ValueError(msg)
            link_cursor = conn.execute(
                """
                INSERT OR IGNORE INTO run_events (
                    tracking_session_id,
                    usage_event_id,
                    created_at_ms
                )
                VALUES (?, ?, ?)
                """,
                (
                    tracking_session_id,
                    _required_int(event_row["id"]),
                    imported_at_ms,
                ),
            )
            rows_linked += link_cursor.rowcount

    return InsertUsageResult(
        rows_inserted=rows_inserted,
        rows_linked=rows_linked,
        rows_skipped=len(filtered_events) - rows_inserted,
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


def summarize_usage(
    conn: sqlite3.Connection,
    filters: UsageReportFilter,
    *,
    costing_config: CostingConfig | None = None,
    simulation_targets: tuple[SimulationTarget, ...] = (),
) -> RunReport:
    filters, session = _apply_tracking_session_time_window(conn, filters)
    source_clause, where_clause, params = _usage_report_query_parts(filters)
    group_by_columns = ["ue.harness", "ue.provider_id", "ue.model_id"]
    if filters.split_thinking:
        group_by_columns.append("ue.thinking_level")
    group_by_columns.append("ue.agent")
    thinking_select = (
        "ue.thinking_level AS thinking_level"
        if filters.split_thinking
        else "NULL AS thinking_level"
    )
    atom_rows = conn.execute(
        """
        SELECT
            ue.harness,
            ue.provider_id,
            ue.model_id,
            """
        + thinking_select
        + """
            ,
            ue.agent AS agent,
            COUNT(*) AS message_count,
            COALESCE(SUM(ue.input_tokens), 0) AS input_tokens,
            COALESCE(SUM(ue.output_tokens), 0) AS output_tokens,
            COALESCE(SUM(ue.reasoning_tokens), 0) AS reasoning_tokens,
            COALESCE(SUM(ue.cache_read_tokens), 0) AS cache_read_tokens,
            COALESCE(SUM(ue.cache_write_tokens), 0) AS cache_write_tokens,
            COALESCE(DECIMAL_SUM(ue.source_cost_usd), '0') AS source_cost_usd
        """
        + source_clause
        + where_clause
        + """
        GROUP BY """
        + ", ".join(group_by_columns),
        params,
    ).fetchall()

    config = costing_config or default_costing_config()
    totals_tokens = TokenBreakdown()
    totals_costs = CostTotals()
    by_provider: dict[str, _ReportBucket] = {}
    by_harness: dict[str, _ReportBucket] = {}
    by_model: dict[tuple[str, str, str | None], _ReportBucket] = {}
    by_agent: dict[str, _ReportBucket] = {}
    unconfigured: dict[
        tuple[str, str, str, str | None, tuple[str, ...]], _UnconfiguredBucket
    ] = {}

    for row in atom_rows:
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
            message_count=_required_int(row["message_count"]),
            tokens=_row_tokens(row),
            source_cost_usd=_required_decimal(row["source_cost_usd"]),
        )
        resolution = resolve_price_resolution(
            harness=atom.harness,
            provider_id=atom.provider_id,
            model_id=atom.model_id,
            config=config,
        )
        breakdown = atom.compute_costs(config)
        totals_tokens = _add_tokens(totals_tokens, atom.tokens)
        totals_costs = _add_cost_breakdown(totals_costs, breakdown)

        by_provider.setdefault(atom.provider_id, _ReportBucket()).add(atom, config)
        by_harness.setdefault(atom.harness, _ReportBucket()).add(atom, config)
        by_model.setdefault(
            (atom.provider_id, atom.model_id, atom.thinking_level),
            _ReportBucket(),
        ).add(atom, config)
        by_agent.setdefault(atom.agent or "unknown", _ReportBucket()).add(atom, config)
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
                total_tokens=totals_tokens.total,
                cost_usd=sim_result.cost_usd,
                baseline_virtual_usd=sim_result.baseline_virtual_usd,
                delta_vs_virtual_usd=sim_result.delta_vs_virtual_usd,
            )
        )
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
                total_tokens=bucket.tokens.total,
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
        and (provider_filter is None or subscription.provider == provider_filter)
    ]

    rows: list[SubscriptionUsageRow] = []
    for subscription in sorted(subscriptions, key=lambda item: item.provider):
        periods: list[SubscriptionUsagePeriod] = []
        for window_config in sorted(
            subscription.windows,
            key=lambda item: (_PERIOD_SORT.get(item.period, 99), item.period),
        ):
            if not window_config.enabled:
                continue

            status = "active"
            since_ms: int | None
            until_ms: int | None
            if window_config.reset_mode == "fixed":
                window = resolve_fixed_subscription_window(
                    period=window_config.period,
                    reset_at=window_config.reset_at,
                    timezone_name=subscription.timezone,
                    now_ms=generated_at_ms,
                )
                since_ms = window.since_ms
                until_ms = window.until_ms
            else:
                reset_anchor = resolve_fixed_subscription_window(
                    period="daily",
                    reset_at=window_config.reset_at,
                    timezone_name=subscription.timezone,
                    now_ms=0,
                )
                usage_timestamps = _provider_usage_timestamps(
                    conn,
                    provider_id=subscription.provider,
                    since_ms=reset_anchor.since_ms,
                    until_ms=generated_at_ms,
                )
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

            if since_ms is not None and until_ms is not None:
                report = summarize_usage(
                    conn,
                    UsageReportFilter(
                        provider_id=subscription.provider,
                        since_ms=since_ms,
                        until_ms=until_ms,
                    ),
                    costing_config=config,
                )
                message_count = sum(row.message_count for row in report.by_harness)
                tokens = report.totals.tokens
                costs = report.totals.costs
            else:
                message_count = 0
                tokens = TokenBreakdown()
                costs = CostTotals()

            if subscription.cost_basis == "source":
                used_usd = costs.source_cost_usd
            elif subscription.cost_basis == "actual":
                used_usd = costs.actual_cost_usd
            else:
                used_usd = costs.virtual_cost_usd

            limit_usd = Decimal(str(window_config.limit_usd))
            remaining_usd = max(limit_usd - used_usd, Decimal(0))
            over_limit_usd = max(used_usd - limit_usd, Decimal(0))
            percent_used = (
                None if limit_usd == 0 else (used_usd / limit_usd) * Decimal(100)
            )

            periods.append(
                SubscriptionUsagePeriod(
                    period=window_config.period,
                    reset_mode=window_config.reset_mode,
                    reset_at=window_config.reset_at,
                    status=status,
                    since_ms=since_ms,
                    until_ms=until_ms,
                    limit_usd=limit_usd,
                    used_usd=used_usd,
                    remaining_usd=remaining_usd,
                    over_limit_usd=over_limit_usd,
                    percent_used=percent_used,
                    message_count=message_count,
                    tokens=tokens,
                    costs=costs,
                )
            )

        rows.append(
            SubscriptionUsageRow(
                provider_id=subscription.provider,
                display_name=subscription.label,
                timezone=subscription.timezone,
                cost_basis=subscription.cost_basis,
                periods=tuple(periods),
            )
        )

    return SubscriptionUsageReport(
        generated_at_ms=generated_at_ms,
        subscriptions=tuple(rows),
    )


def _provider_usage_timestamps(
    conn: sqlite3.Connection,
    *,
    provider_id: str,
    since_ms: int,
    until_ms: int,
) -> list[int]:
    rows = conn.execute(
        """
        SELECT created_ms
        FROM usage_events
        WHERE provider_id = ?
          AND created_ms >= ?
          AND created_ms <= ?
        ORDER BY created_ms ASC
        """,
        (provider_id, since_ms, until_ms),
    ).fetchall()
    return [_required_int(row["created_ms"]) for row in rows]


def summarize_usage_series(
    conn: sqlite3.Connection,
    filters: UsageSeriesFilter,
    *,
    costing_config: CostingConfig | None = None,
) -> UsageSeriesReport:
    from datetime import tzinfo as _tzinfo
    from zoneinfo import ZoneInfo

    from toktrail.periods import (
        bucket_for_timestamp,
    )

    tz: _tzinfo = ZoneInfo("UTC")

    usage_filters, _ = _apply_tracking_session_time_window(
        conn,
        filters.to_usage_report_filter(),
    )
    source_clause, where_clause, params = _usage_report_query_parts(usage_filters)
    thinking_select = (
        "ue.thinking_level AS thinking_level"
        if filters.split_thinking
        else "NULL AS thinking_level"
    )
    atom_rows = conn.execute(
        """
        SELECT
            ue.created_ms,
            ue.harness,
            ue.source_session_id,
            ue.provider_id,
            ue.model_id,
        """
        + thinking_select
        + """
            ,
            ue.agent AS agent,
            COUNT(*) AS message_count,
            COALESCE(SUM(ue.input_tokens), 0) AS input_tokens,
            COALESCE(SUM(ue.output_tokens), 0) AS output_tokens,
            COALESCE(SUM(ue.reasoning_tokens), 0) AS reasoning_tokens,
            COALESCE(SUM(ue.cache_read_tokens), 0) AS cache_read_tokens,
            COALESCE(SUM(ue.cache_write_tokens), 0) AS cache_write_tokens,
            COALESCE(DECIMAL_SUM(ue.source_cost_usd), '0') AS source_cost_usd
        """
        + source_clause
        + where_clause
        + """
        GROUP BY
            ue.created_ms,
            ue.harness,
            ue.source_session_id,
            ue.provider_id,
            ue.model_id,
        """
        + ("ue.thinking_level," if filters.split_thinking else "")
        + """
            ue.agent
        """,
        params,
    ).fetchall()

    config = costing_config or default_costing_config()

    bucket_data: dict[str, _SeriesBucketAccum] = {}
    model_bucket_data: dict[tuple[str, str, str | None], _SeriesModelAccum] = {}
    instance_data: dict[str, _SeriesInstanceAccum] = {}

    for row in atom_rows:
        created_ms = _required_int(row["created_ms"])
        harness = str(row["harness"])
        source_session_id = str(row["source_session_id"])

        bucket = bucket_for_timestamp(
            created_ms,
            granularity=filters.granularity,
            tz=tz,
            start_of_week=filters.start_of_week,
        )

        atom = UsageCostAtom(
            harness=harness,
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            thinking_level=(
                str(row["thinking_level"])
                if row["thinking_level"] is not None
                else None
            ),
            agent=row["agent"],
            message_count=_required_int(row["message_count"]),
            tokens=_row_tokens(row),
            source_cost_usd=_required_decimal(row["source_cost_usd"]),
        )
        breakdown = atom.compute_costs(config)
        model_key_str = f"{atom.provider_id}/{atom.model_id}"

        bucket_data.setdefault(bucket.key, _SeriesBucketAccum(bucket=bucket)).add(
            atom, breakdown, model_key_str
        )

        if filters.breakdown:
            model_key = (bucket.key, atom.provider_id, atom.model_id)
            if atom.thinking_level is not None and filters.split_thinking:
                model_key = (
                    bucket.key,
                    atom.provider_id,
                    f"{atom.model_id}[{atom.thinking_level}]",
                )
            model_bucket_data.setdefault(
                model_key, _SeriesModelAccum(bucket_key=bucket.key)
            ).add(atom, breakdown)

        if filters.instances:
            inst_key = f"{harness}/{source_session_id}"
            inst_label = source_session_id
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
    buckets: list[UsageSeriesBucket] = []
    for acc in buckets_list:
        by_model_rows: list[ModelSummaryRow] = []
        if filters.breakdown:
            for (bkey, prov, mid), macc in sorted(model_bucket_data.items()):
                if bkey != acc.bucket.key:
                    continue
                by_model_rows.append(macc.to_model_row(prov, mid or ""))
        buckets.append(
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
        "harness": filters.harness,
        "provider_id": filters.provider_id,
        "model_id": filters.model_id,
        "thinking_level": filters.thinking_level,
        "agent": filters.agent,
        "project": filters.project,
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
        buckets=tuple(buckets),
        instances=tuple(instances),
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


def _usage_report_query_parts(
    filters: UsageReportFilter,
) -> tuple[str, str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    source_clause = " FROM usage_events AS ue"
    if filters.tracking_session_id is not None:
        source_clause += " JOIN run_events AS tse ON tse.usage_event_id = ue.id"
        clauses.append("tse.tracking_session_id = ?")
        params.append(filters.tracking_session_id)
    if filters.harness is not None:
        clauses.append("ue.harness = ?")
        params.append(filters.harness)
    if filters.source_session_id is not None:
        clauses.append("ue.source_session_id = ?")
        params.append(filters.source_session_id)
    if filters.provider_id is not None:
        clauses.append("ue.provider_id = ?")
        params.append(filters.provider_id)
    if filters.model_id is not None:
        clauses.append("ue.model_id = ?")
        params.append(filters.model_id)
    if filters.thinking_level is not None:
        clauses.append("COALESCE(ue.thinking_level, '') = ?")
        params.append(filters.thinking_level)
    if filters.agent is not None:
        clauses.append("ue.agent = ?")
        params.append(filters.agent)
    if filters.since_ms is not None:
        clauses.append("ue.created_ms >= ?")
        params.append(filters.since_ms)
    if filters.until_ms is not None:
        clauses.append("ue.created_ms < ?")
        params.append(filters.until_ms)

    where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return source_clause, where_clause, params


def _tracking_session_from_row(row: sqlite3.Row) -> TrackingSession:
    return TrackingSession(
        id=_required_int(row["id"]),
        name=str(row["name"]) if row["name"] is not None else None,
        started_at_ms=_required_int(row["started_at_ms"]),
        ended_at_ms=_optional_int(row["ended_at_ms"]),
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

    def step(self, value: object) -> None:
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

    def add(self, atom: UsageCostAtom, config: CostingConfig) -> None:
        self.message_count += atom.message_count
        self.tokens = _add_tokens(self.tokens, atom.tokens)
        self.costs = _add_cost_breakdown(self.costs, atom.compute_costs(config))


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
        model_bucket_data: dict[tuple[str, str, str | None], _SeriesModelAccum],
        filters: UsageSeriesFilter,
    ) -> list[UsageSeriesBucket]:
        result: list[UsageSeriesBucket] = []
        for acc in self.bucket_data.values():
            by_model_rows: list[ModelSummaryRow] = []
            if filters.breakdown:
                for (bkey, prov, mid), macc in sorted(model_bucket_data.items()):
                    if bkey != acc.bucket.key:
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
