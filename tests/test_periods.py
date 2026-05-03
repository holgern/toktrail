from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from toktrail.periods import (
    bucket_for_timestamp,
    parse_cli_boundary,
    resolve_first_use_subscription_window,
    resolve_fixed_subscription_window,
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


def test_resolve_fixed_subscription_window_5h() -> None:
    tz = ZoneInfo("Europe/Berlin")
    now_ms = int(datetime(2026, 5, 3, 10, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_fixed_subscription_window(
        period="5h",
        reset_at="2026-05-03T08:00:00+02:00",
        timezone_name="Europe/Berlin",
        now_ms=now_ms,
    )

    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 5, 3, 8, 0, tzinfo=tz)
    assert until == datetime(2026, 5, 3, 13, 0, tzinfo=tz)


def test_resolve_fixed_subscription_window_5h_before_reset_returns_first() -> None:
    tz = ZoneInfo("UTC")
    now_ms = int(datetime(2026, 5, 3, 7, 30, tzinfo=tz).timestamp() * 1000)

    window = resolve_fixed_subscription_window(
        period="5h",
        reset_at="2026-05-03T08:00:00+00:00",
        timezone_name="UTC",
        now_ms=now_ms,
    )

    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 5, 3, 8, 0, tzinfo=tz)
    assert until == datetime(2026, 5, 3, 13, 0, tzinfo=tz)


def test_resolve_first_use_subscription_window_waits_without_usage() -> None:
    window = resolve_first_use_subscription_window(
        period="5h",
        reset_at="2026-05-03T08:00:00+00:00",
        timezone_name="UTC",
        usage_timestamps_ms=[],
        now_ms=1777798800000,
    )
    assert window.status == "waiting_for_first_use"
    assert window.since_ms is None
    assert window.until_ms is None


def test_resolve_first_use_subscription_window_starts_at_first_usage() -> None:
    tz = ZoneInfo("UTC")
    first_usage = int(datetime(2026, 5, 3, 9, 0, tzinfo=tz).timestamp() * 1000)
    now_ms = int(datetime(2026, 5, 3, 10, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_first_use_subscription_window(
        period="5h",
        reset_at="2026-05-03T08:00:00+00:00",
        timezone_name="UTC",
        usage_timestamps_ms=[first_usage],
        now_ms=now_ms,
    )

    assert window.status == "active"
    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 5, 3, 9, 0, tzinfo=tz)
    assert until == datetime(2026, 5, 3, 14, 0, tzinfo=tz)


def test_resolve_first_use_window_rolls_to_next_usage_after_expiry() -> None:
    tz = ZoneInfo("UTC")
    first_usage = int(datetime(2026, 5, 3, 9, 0, tzinfo=tz).timestamp() * 1000)
    second_usage = int(datetime(2026, 5, 3, 15, 30, tzinfo=tz).timestamp() * 1000)
    now_ms = int(datetime(2026, 5, 3, 16, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_first_use_subscription_window(
        period="5h",
        reset_at="2026-05-03T08:00:00+00:00",
        timezone_name="UTC",
        usage_timestamps_ms=[first_usage, second_usage],
        now_ms=now_ms,
    )

    assert window.status == "active"
    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 5, 3, 15, 30, tzinfo=tz)
    assert until == datetime(2026, 5, 3, 20, 30, tzinfo=tz)


def test_resolve_first_use_subscription_window_expired_waiting_for_next_use() -> None:
    usage = int(datetime(2026, 5, 3, 9, 0, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    now_ms = int(datetime(2026, 5, 3, 15, 0, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)

    window = resolve_first_use_subscription_window(
        period="5h",
        reset_at="2026-05-03T08:00:00+00:00",
        timezone_name="UTC",
        usage_timestamps_ms=[usage],
        now_ms=now_ms,
    )

    assert window.status == "expired_waiting_for_next_use"
    assert window.since_ms is None
    assert window.until_ms is None


def test_resolve_first_use_subscription_window_monthly_clamps_day() -> None:
    tz = ZoneInfo("UTC")
    usage = int(datetime(2026, 1, 31, 9, 0, tzinfo=tz).timestamp() * 1000)
    now_ms = int(datetime(2026, 2, 20, 9, 0, tzinfo=tz).timestamp() * 1000)

    window = resolve_first_use_subscription_window(
        period="monthly",
        reset_at="2026-01-01T00:00:00+00:00",
        timezone_name="UTC",
        usage_timestamps_ms=[usage],
        now_ms=now_ms,
    )

    assert window.status == "active"
    since = datetime.fromtimestamp(window.since_ms / 1000, tz=tz)
    until = datetime.fromtimestamp(window.until_ms / 1000, tz=tz)
    assert since == datetime(2026, 1, 31, 9, 0, tzinfo=tz)
    assert until == datetime(2026, 2, 28, 9, 0, tzinfo=tz)
