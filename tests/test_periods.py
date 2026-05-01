from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from toktrail.periods import (
    bucket_for_timestamp,
    parse_cli_boundary,
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
