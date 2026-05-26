from __future__ import annotations

from toktrail.formatting import format_duration_seconds


class TestFormatDurationSeconds:
    def test_compact_hours_minutes(self) -> None:
        # 2h 19m = 2*3600 + 19*60 = 8340
        assert format_duration_seconds(8340) == "2h 19m"

    def test_long_hours_minutes(self) -> None:
        assert format_duration_seconds(8340, compact=False) == "2 hours 19 minutes"

    def test_zero_compact(self) -> None:
        assert format_duration_seconds(0) == "now"

    def test_zero_long(self) -> None:
        assert format_duration_seconds(0, compact=False) == "now"

    def test_compact_hours_only(self) -> None:
        assert format_duration_seconds(3600) == "1h"

    def test_long_hour_singular(self) -> None:
        assert format_duration_seconds(3600, compact=False) == "1 hour"

    def test_compact_minutes_only(self) -> None:
        assert format_duration_seconds(60) == "1m"

    def test_long_minute_singular(self) -> None:
        assert format_duration_seconds(60, compact=False) == "1 minute"

    def test_compact_seconds_only(self) -> None:
        assert format_duration_seconds(12) == "12s"

    def test_long_seconds(self) -> None:
        assert format_duration_seconds(12, compact=False) == "12 seconds"

    def test_compact_days_hours(self) -> None:
        # 2d 5h = 2*86400 + 5*3600 = 190800
        assert format_duration_seconds(190800) == "2d 5h"

    def test_long_days_hours(self) -> None:
        assert format_duration_seconds(190800, compact=False) == "2 days 5 hours"

    def test_negative_clamped_to_zero(self) -> None:
        assert format_duration_seconds(-100) == "now"

    def test_max_parts_one(self) -> None:
        # 8340 = 2h 19m, max_parts=1 => just "2h"
        assert format_duration_seconds(8340, max_parts=1) == "2h"

    def test_compact_45_minutes(self) -> None:
        assert format_duration_seconds(45 * 60) == "45m"

    def test_long_plural_minutes(self) -> None:
        assert format_duration_seconds(45 * 60, compact=False) == "45 minutes"

    def test_days_only(self) -> None:
        assert format_duration_seconds(86400) == "1d"

    def test_long_day_singular(self) -> None:
        assert format_duration_seconds(86400, compact=False) == "1 day"

    def test_mixed_days_minutes_truncated_by_max_parts(self) -> None:
        # 1d 0h 1m = 86460, max_parts=2 => days + minutes
        # days=1, hours=0, minutes=1 => parts = [(1,'d'), (1,'m')]
        # max_parts=2 => "1d 1m"
        assert format_duration_seconds(86460) == "1d 1m"
