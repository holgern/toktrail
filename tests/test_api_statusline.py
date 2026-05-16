from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from toktrail.api import statusline_report
from toktrail.cli import _should_skip_statusline_auto_refresh
from toktrail.db import (
    connect,
    create_tracking_session,
    ensure_area,
    insert_usage_events,
    migrate,
    set_active_area,
)
from toktrail.models import RunScope, TokenBreakdown, UsageEvent
from toktrail.statusline import (
    StatuslineRequest,
    load_statusline_output_cache,
    statusline_cache_key,
    write_statusline_output_cache,
)


def make_statusline_event(
    dedup_suffix: str,
    *,
    harness: str = "codex",
    source_session_id: str = "session-1",
    provider_id: str = "openai",
    model_id: str = "gpt-5.3-codex",
    created_ms: int,
    source_cost_usd: str = "0",
    tokens: TokenBreakdown | None = None,
) -> UsageEvent:
    return UsageEvent(
        harness=harness,
        source_session_id=source_session_id,
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"dedup-{dedup_suffix}",
        global_dedup_key=f"global-{dedup_suffix}",
        fingerprint_hash=f"fp-{dedup_suffix}",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=None,
        agent="build",
        created_ms=created_ms,
        completed_ms=created_ms + 1,
        tokens=tokens or TokenBreakdown(input=1_000, output=200, cache_read=500),
        source_cost_usd=Decimal(source_cost_usd),
        raw_json=None,
    )


def test_statusline_report_selects_latest_source_session(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_statusline_event(
                    "old",
                    source_session_id="codex/old",
                    created_ms=1_000,
                ),
                make_statusline_event(
                    "new",
                    source_session_id="codex/new",
                    created_ms=2_000,
                    tokens=TokenBreakdown(input=2_000, output=300, cache_read=700),
                ),
            ],
        )
    finally:
        conn.close()

    report = statusline_report(state_db, now_ms=2_500)

    assert report.harness == "codex"
    assert report.source_session_id == "codex/new"
    assert report.model_id == "gpt-5.3-codex"
    assert report.tokens.total == 2_300
    assert "codex" in report.line


def test_statusline_report_exposes_active_area_path(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        area = ensure_area(conn, "work/odoo")
        set_active_area(conn, area.id)
        insert_usage_events(
            conn,
            None,
            [make_statusline_event("area", created_ms=2_000)],
        )
    finally:
        conn.close()

    report = statusline_report(state_db, now_ms=2_500, elements=("area", "tokens"))
    assert report.area_path == "work/odoo"
    assert "area work/odoo" in report.line


def test_statusline_report_prefers_explicit_source_session(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_statusline_event(
                    "first",
                    source_session_id="codex/first",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=1_000, output=100),
                ),
                make_statusline_event(
                    "second",
                    source_session_id="codex/second",
                    created_ms=2_000,
                    tokens=TokenBreakdown(input=2_000, output=100),
                ),
            ],
        )
    finally:
        conn.close()

    report = statusline_report(
        state_db,
        harness="codex",
        source_session_id="codex/first",
        now_ms=2_500,
    )

    assert report.source_session_id == "codex/first"
    assert report.tokens.total == 1_100


def test_statusline_report_selects_primary_quota_window(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_statusline_event(
                    "quota",
                    harness="opencode",
                    source_session_id="opencode/1",
                    provider_id="opencode-go",
                    model_id="deepseek-v4-pro",
                    created_ms=1777801200000,
                    source_cost_usd="18",
                ),
            ],
        )
    finally:
        conn.close()
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 20
reset_mode = "fixed"
reset_at = "2026-05-03T08:00:00+00:00"

[[subscriptions.windows]]
period = "weekly"
limit_usd = 100
reset_mode = "fixed"
reset_at = "2026-05-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    report = statusline_report(
        state_db,
        harness="opencode",
        config_path=config_path,
        now_ms=1777802400000,
    )

    assert report.quota is not None
    assert report.quota.period == "5h"
    assert report.quota.remaining_usd == Decimal("2.0")
    assert "5h 90%" in report.line


def test_statusline_report_burn_rate_over_limit(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_statusline_event(
                    "over",
                    harness="opencode",
                    source_session_id="opencode/1",
                    provider_id="opencode-go",
                    model_id="deepseek-v4-pro",
                    created_ms=1777801200000,
                    source_cost_usd="12",
                ),
            ],
        )
    finally:
        conn.close()
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "fixed"
reset_at = "2026-05-03T08:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    report = statusline_report(
        state_db,
        harness="opencode",
        config_path=config_path,
        now_ms=1777802400000,
    )

    assert report.quota is not None
    assert report.quota.over_limit_usd == Decimal("2.0")
    assert report.burn is not None
    assert report.burn.label == "limit"
    assert "over $2.00" in report.line


def test_statusline_report_burn_rate_active_window(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_statusline_event(
                    "burn",
                    harness="opencode",
                    source_session_id="opencode/1",
                    provider_id="opencode-go",
                    model_id="deepseek-v4-pro",
                    created_ms=1777801200000,
                    source_cost_usd="5",
                ),
            ],
        )
    finally:
        conn.close()
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "fixed"
reset_at = "2026-05-03T08:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    report = statusline_report(
        state_db,
        harness="opencode",
        config_path=config_path,
        now_ms=1777802400000,
    )

    assert report.burn is not None
    assert round(report.burn.ratio, 2) == 1.5
    assert report.burn.label == "150% 3h"
    assert "burn 150% 3h" in report.line


def test_statusline_report_session_mode_latest_ignores_active_scope(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_statusline_event(
                    "old",
                    source_session_id="codex/old",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=1_000, output=100),
                ),
                make_statusline_event(
                    "new",
                    source_session_id="codex/new",
                    created_ms=2_000,
                    tokens=TokenBreakdown(input=2_000, output=100),
                ),
            ],
        )
        create_tracking_session(
            conn,
            "statusline",
            started_at_ms=1_500,
            scope=RunScope(
                harnesses=("codex",),
                source_session_ids=("codex/old",),
            ),
        )
    finally:
        conn.close()

    auto_report = statusline_report(state_db, harness="codex", now_ms=2_500)
    latest_report = statusline_report(
        state_db,
        harness="codex",
        session_mode="latest",
        now_ms=2_500,
    )

    assert auto_report.source_session_id == "codex/old"
    assert latest_report.source_session_id == "codex/new"
    assert latest_report.tokens.total == 2_100


def test_statusline_report_context_from_stdin_payload(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_statusline_event(
                    "ctx",
                    created_ms=1_000,
                ),
            ],
        )
    finally:
        conn.close()

    report = statusline_report(
        state_db,
        stdin_payload={
            "context": {
                "used_tokens": 120_000,
                "limit_tokens": 272_000,
            }
        },
        now_ms=2_000,
    )

    assert report.context is not None
    assert report.context.used_tokens == 120_000
    assert report.context.limit_tokens == 272_000
    assert round(report.context.percentage, 1) == 44.1


def test_statusline_report_hides_unknown_context(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_statusline_event(
                    "no-ctx",
                    created_ms=1_000,
                ),
            ],
        )
    finally:
        conn.close()

    report = statusline_report(state_db, now_ms=2_000)

    assert report.context is None


def test_statusline_output_cache_hit(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [make_statusline_event("cache-hit", created_ms=1_000)],
        )
    finally:
        conn.close()

    report = statusline_report(state_db, now_ms=2_000)
    request = StatuslineRequest()
    cache_dir = tmp_path / "cache"
    cache_key = statusline_cache_key(state_db, request=request, json_output=False)

    write_statusline_output_cache(
        cache_dir=cache_dir,
        cache_key=cache_key,
        report=report,
        state_db_path=state_db,
        config_path=None,
        source_path=None,
    )
    cached = load_statusline_output_cache(
        cache_dir=cache_dir,
        cache_key=cache_key,
        state_db_path=state_db,
        config_path=None,
        source_path=None,
        max_age_seconds=10,
    )

    assert cached is not None
    assert cached.line == report.line
    assert cached.cache is not None
    assert cached.cache.output_cache == "hit"


def test_statusline_output_cache_invalidates_on_state_db_mtime(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [make_statusline_event("cache-db", created_ms=1_000)],
        )
    finally:
        conn.close()

    report = statusline_report(state_db, now_ms=2_000)
    request = StatuslineRequest()
    cache_dir = tmp_path / "cache"
    cache_key = statusline_cache_key(state_db, request=request, json_output=False)
    write_statusline_output_cache(
        cache_dir=cache_dir,
        cache_key=cache_key,
        report=report,
        state_db_path=state_db,
        config_path=None,
        source_path=None,
    )
    state_db.touch()

    cached = load_statusline_output_cache(
        cache_dir=cache_dir,
        cache_key=cache_key,
        state_db_path=state_db,
        config_path=None,
        source_path=None,
        max_age_seconds=10,
    )

    assert cached is None


def test_statusline_output_cache_invalidates_on_config_change(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [make_statusline_event("cache-config", created_ms=1_000)],
        )
    finally:
        conn.close()

    report = statusline_report(state_db, now_ms=2_000)
    request = StatuslineRequest()
    cache_dir = tmp_path / "cache"
    cache_key = statusline_cache_key(state_db, request=request, json_output=False)
    write_statusline_output_cache(
        cache_dir=cache_dir,
        cache_key=cache_key,
        report=report,
        state_db_path=state_db,
        config_path=config_path,
        source_path=None,
    )
    config_path.write_text(
        'config_version = 1\n\n[statusline]\nbasis = "source"\n',
        encoding="utf-8",
    )

    cached = load_statusline_output_cache(
        cache_dir=cache_dir,
        cache_key=cache_key,
        state_db_path=state_db,
        config_path=config_path,
        source_path=None,
        max_age_seconds=10,
    )

    assert cached is None


def test_statusline_cache_atomic_write(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [make_statusline_event("cache-atomic", created_ms=1_000)],
        )
    finally:
        conn.close()

    report = statusline_report(state_db, now_ms=2_000)
    request = StatuslineRequest()
    cache_dir = tmp_path / "cache"
    cache_key = statusline_cache_key(state_db, request=request, json_output=False)

    write_statusline_output_cache(
        cache_dir=cache_dir,
        cache_key=cache_key,
        report=report,
        state_db_path=state_db,
        config_path=None,
        source_path=None,
    )

    assert (cache_dir / f"{cache_key}.json").exists()
    assert not (cache_dir / f"{cache_key}.tmp").exists()


def test_statusline_refresh_auto_checks_directory_sources_without_recent_cache(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "sessions"
    source_dir.mkdir()
    state_db.write_text("", encoding="utf-8")

    assert (
        _should_skip_statusline_auto_refresh(
            state_db_path=state_db,
            source_path=source_dir,
            cache_metadata=None,
            min_refresh_interval_secs=5,
        )
        is False
    )

    assert (
        _should_skip_statusline_auto_refresh(
            state_db_path=state_db,
            source_path=source_dir,
            cache_metadata={"created_ms": int(time.time() * 1000)},
            min_refresh_interval_secs=5,
        )
        is True
    )
