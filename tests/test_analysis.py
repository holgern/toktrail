from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from toktrail.api.analysis import session_cache_analysis
from toktrail.db import (
    connect,
    create_tracking_session,
    insert_usage_events,
    list_usage_events,
    migrate,
)
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import UsageReportFilter


def _event(
    suffix: str,
    *,
    source_session_id: str = "ses-cache",
    created_ms: int = 1_000,
    tokens: TokenBreakdown | None = None,
    source_cost_usd: str = "0.0",
) -> UsageEvent:
    return UsageEvent(
        harness="opencode",
        source_session_id=source_session_id,
        source_row_id=f"row-{suffix}",
        source_message_id=f"msg-{suffix}",
        source_dedup_key=f"msg-{suffix}",
        global_dedup_key=f"opencode:msg-{suffix}",
        fingerprint_hash=f"fp-{suffix}",
        provider_id="opencode-go",
        model_id="glm-5.1",
        thinking_level=None,
        agent="build",
        created_ms=created_ms,
        completed_ms=created_ms + 1,
        tokens=tokens or TokenBreakdown(input=100, output=10),
        source_cost_usd=Decimal(source_cost_usd),
        raw_json=None,
    )


def _new_state_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "toktrail.db"
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    return db_path


def test_list_usage_events_filters_source_session_and_orders_chronologically(
    tmp_path: Path,
) -> None:
    db_path = _new_state_db(tmp_path)
    conn = connect(db_path)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                _event("a", source_session_id="ses-a", created_ms=2_000),
                _event("b", source_session_id="ses-a", created_ms=1_000),
                _event("c", source_session_id="ses-b", created_ms=3_000),
            ],
        )
        rows = list_usage_events(
            conn,
            UsageReportFilter(harness="opencode", source_session_id="ses-a"),
            order="created",
        )
        descending = list_usage_events(
            conn,
            UsageReportFilter(harness="opencode", source_session_id="ses-a"),
            order="created_desc",
        )
    finally:
        conn.close()

    assert [row.created_ms for row in rows] == [1_000, 2_000]
    assert [row.created_ms for row in descending] == [2_000, 1_000]


def test_session_cache_analysis_reports_per_call_rows(tmp_path: Path) -> None:
    db_path = _new_state_db(tmp_path)
    conn = connect(db_path)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                _event(
                    "1",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=30_000, cache_read=120_000, output=50),
                    source_cost_usd="0.04",
                ),
                _event(
                    "2",
                    created_ms=2_000,
                    tokens=TokenBreakdown(input=150_000, cache_read=0, output=50),
                    source_cost_usd="0.21",
                ),
            ],
        )
    finally:
        conn.close()

    report = session_cache_analysis(
        db_path=db_path,
        harness="opencode",
        source_session_id="ses-cache",
        refresh=False,
        use_active_run=False,
    )

    assert report.call_count == 2
    assert len(report.calls) == 2
    assert report.calls[0].cache_status == "hit"
    assert report.calls[1].cache_status == "miss"
    assert report.prompt_like_tokens == 300_000


def test_session_cache_analysis_flags_suspicious_misses(tmp_path: Path) -> None:
    db_path = _new_state_db(tmp_path)
    conn = connect(db_path)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                _event(
                    "1",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=30_000, cache_read=120_000, output=50),
                    source_cost_usd="0.04",
                ),
                _event(
                    "2",
                    created_ms=2_000,
                    tokens=TokenBreakdown(input=150_000, cache_read=0, output=50),
                    source_cost_usd="0.21",
                ),
            ],
        )
    finally:
        conn.close()

    report = session_cache_analysis(
        db_path=db_path,
        harness="opencode",
        source_session_id="ses-cache",
        refresh=False,
        use_active_run=False,
    )

    flags = set(report.calls[1].flags)
    assert "suspicious_miss" in flags
    assert "cache_cliff" in flags
    assert "cost_outlier" in flags


def test_session_cache_analysis_estimates_source_loss_from_cluster_hit_median(
    tmp_path: Path,
) -> None:
    db_path = _new_state_db(tmp_path)
    conn = connect(db_path)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                _event(
                    "1",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=30_000, cache_read=120_000, output=50),
                    source_cost_usd="0.04",
                ),
                _event(
                    "2",
                    created_ms=2_000,
                    tokens=TokenBreakdown(input=150_000, cache_read=0, output=50),
                    source_cost_usd="0.21",
                ),
            ],
        )
    finally:
        conn.close()

    report = session_cache_analysis(
        db_path=db_path,
        harness="opencode",
        source_session_id="ses-cache",
        refresh=False,
        use_active_run=False,
    )

    assert report.estimated_source_cache_loss_usd == Decimal("0.17")
    assert report.clusters[0].estimated_source_loss_usd == Decimal("0.17")


def test_session_cache_analysis_handles_cache_only_event(tmp_path: Path) -> None:
    db_path = _new_state_db(tmp_path)
    conn = connect(db_path)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                _event(
                    "cache-only",
                    tokens=TokenBreakdown(cache_output=12),
                )
            ],
        )
    finally:
        conn.close()

    report = session_cache_analysis(
        db_path=db_path,
        harness="opencode",
        source_session_id="ses-cache",
        refresh=False,
        use_active_run=False,
    )

    assert report.calls[0].cache_status == "unknown"
    assert "cache_only" in report.calls[0].flags


def test_session_cache_analysis_does_not_require_tracking_run(tmp_path: Path) -> None:
    db_path = _new_state_db(tmp_path)
    conn = connect(db_path)
    try:
        migrate(conn)
        insert_usage_events(conn, None, [_event("1", source_session_id="ses-free")])
    finally:
        conn.close()

    report = session_cache_analysis(
        db_path=db_path,
        harness="opencode",
        source_session_id="ses-free",
        refresh=False,
        use_active_run=True,
    )

    assert report.call_count == 1


def test_session_cache_analysis_bounds_to_tracking_run_when_requested(
    tmp_path: Path,
) -> None:
    db_path = _new_state_db(tmp_path)
    conn = connect(db_path)
    try:
        migrate(conn)
        run_id = create_tracking_session(conn, "bounded", started_at_ms=0)
        insert_usage_events(
            conn,
            run_id,
            [_event("tracked", source_session_id="ses-bounded", created_ms=1_000)],
        )
        insert_usage_events(
            conn,
            None,
            [_event("global", source_session_id="ses-bounded", created_ms=2_000)],
        )
    finally:
        conn.close()

    bounded = session_cache_analysis(
        db_path=db_path,
        harness="opencode",
        source_session_id="ses-bounded",
        refresh=False,
        use_active_run=True,
    )
    unbounded = session_cache_analysis(
        db_path=db_path,
        harness="opencode",
        source_session_id="ses-bounded",
        refresh=False,
        use_active_run=False,
    )

    assert bounded.call_count == 1
    assert unbounded.call_count == 2
