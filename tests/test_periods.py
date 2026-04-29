from __future__ import annotations

from datetime import datetime

import pytest

from toktrail.periods import resolve_time_range


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
