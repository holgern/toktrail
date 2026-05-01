from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from toktrail.periods import (
    bucket_for_timestamp,
    parse_cli_boundary,
    resolve_subscription_cycle_window,
    resolve_time_range,
)


def test_resolve_time_range_today_uses_half_open_day_bounds(monkeypatch) -> None:
    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2026, 5, 10, 12, 30, tzinfo=tz),
    )

    result = resolve_time_range(period="today", timezone_name="UTC")

    assert result.period == "today"
    assert result.timezone == "UTC"
    assert result.since_ms == 1778371200000
    assert result.until_ms == 1778457600000


def test_resolve_time_range_rejects_period_and_explicit_boundaries() -> None:
    with pytest.raises(ValueError, match="either a named period or --since/--until"):
        resolve_time_range(
            period="today",
            timezone_name="UTC",
            since_text="2026-05-01",
        )


def test_parse_cli_boundary_accepts_yyyymmdd_in_timezone() -> None:
    tz = ZoneInfo("Europe/Berlin")

    assert parse_cli_boundary("20250525", tz=tz) == 1748124000000


def test_parse_cli_boundary_date_only_until_is_inclusive() -> None:
    tz = ZoneInfo("UTC")

    assert parse_cli_boundary("20250530", tz=tz, is_until=True) == 1748649600000


def test_bucket_for_timestamp_daily_weekly_monthly() -> None:
    tz = ZoneInfo("UTC")
    timestamp = 1748174400000  # 2025-05-25 12:00:00 UTC

    assert (
        bucket_for_timestamp(timestamp, granularity="daily", tz=tz).key == "2025-05-25"
    )
    assert (
        bucket_for_timestamp(timestamp, granularity="weekly", tz=tz).key == "2025-05-19"
    )
    assert (
        bucket_for_timestamp(
            timestamp,
            granularity="weekly",
            tz=tz,
            start_of_week="sunday",
        ).key
        == "2025-05-25"
    )
    assert (
        bucket_for_timestamp(timestamp, granularity="monthly", tz=tz).key == "2025-05"
    )


def test_parse_cli_boundary_rejects_invalid_date() -> None:
    with pytest.raises(ValueError, match="Invalid time boundary"):
        parse_cli_boundary("2025-99-99", tz=ZoneInfo("UTC"))


def test_resolve_subscription_cycle_window_daily_from_cycle_start_timezone() -> None:
    tz = ZoneInfo("Europe/Berlin")
    now_ms = int(datetime(2026, 5, 3, 10, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_subscription_cycle_window(
        period="daily",
        cycle_start="2026-05-01",
        timezone_name="Europe/Berlin",
        now_ms=now_ms,
    )

    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 5, 3, 0, 0, tzinfo=tz)
    assert until == datetime(2026, 5, 4, 0, 0, tzinfo=tz)


def test_resolve_subscription_cycle_window_weekly_anchors_to_cycle_start() -> None:
    tz = ZoneInfo("UTC")
    now_ms = int(datetime(2026, 5, 10, 12, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_subscription_cycle_window(
        period="weekly",
        cycle_start="2026-05-01",
        timezone_name="UTC",
        now_ms=now_ms,
    )

    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 5, 8, 0, 0, tzinfo=tz)
    assert until == datetime(2026, 5, 15, 0, 0, tzinfo=tz)


def test_resolve_subscription_cycle_window_monthly_anchors_day_of_month() -> None:
    tz = ZoneInfo("UTC")
    now_ms = int(datetime(2026, 7, 20, 12, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_subscription_cycle_window(
        period="monthly",
        cycle_start="2026-05-15",
        timezone_name="UTC",
        now_ms=now_ms,
    )

    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 7, 15, 0, 0, tzinfo=tz)
    assert until == datetime(2026, 8, 15, 0, 0, tzinfo=tz)


def test_resolve_subscription_cycle_window_monthly_clamps_day_31() -> None:
    tz = ZoneInfo("UTC")
    now_ms = int(datetime(2026, 3, 1, 12, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_subscription_cycle_window(
        period="monthly",
        cycle_start="2026-01-31",
        timezone_name="UTC",
        now_ms=now_ms,
    )

    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 2, 28, 0, 0, tzinfo=tz)
    assert until == datetime(2026, 3, 28, 0, 0, tzinfo=tz)


def test_resolve_subscription_cycle_window_date_only_uses_midnight() -> None:
    tz = ZoneInfo("Europe/Berlin")
    now_ms = int(datetime(2026, 5, 1, 12, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_subscription_cycle_window(
        period="daily",
        cycle_start="2026-05-01",
        timezone_name="Europe/Berlin",
        now_ms=now_ms,
    )

    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    assert since.hour == 0
    assert since.minute == 0


def test_subscription_cycle_window_before_start_returns_first_window() -> None:
    tz = ZoneInfo("UTC")
    now_ms = int(datetime(2026, 4, 1, 0, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_subscription_cycle_window(
        period="monthly",
        cycle_start="2026-05-01",
        timezone_name="UTC",
        now_ms=now_ms,
    )

    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 5, 1, 0, 0, tzinfo=tz)
    assert until == datetime(2026, 6, 1, 0, 0, tzinfo=tz)
