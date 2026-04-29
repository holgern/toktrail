from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class ResolvedTimeRange:
    since_ms: int | None
    until_ms: int | None
    period: str | None
    timezone: str


def current_time_in_zone(tz: tzinfo) -> datetime:
    return datetime.now(tz)


def resolve_time_range(
    *,
    period: str | None = None,
    timezone_name: str | None = None,
    utc: bool = False,
    since_text: str | None = None,
    until_text: str | None = None,
) -> ResolvedTimeRange:
    if period is not None and (since_text is not None or until_text is not None):
        msg = "Use either a named period or --since/--until, not both."
        raise ValueError(msg)

    tz = _resolve_timezone(timezone_name=timezone_name, utc=utc)
    timezone_label = "UTC" if tz is timezone.utc else getattr(tz, "key", str(tz))

    if period is not None:
        normalized_period = _normalize_period(period)
        since_ms, until_ms = _period_bounds_ms(normalized_period, tz=tz)
        return ResolvedTimeRange(
            since_ms=since_ms,
            until_ms=until_ms,
            period=normalized_period,
            timezone=timezone_label,
        )

    return ResolvedTimeRange(
        since_ms=_parse_boundary_ms(since_text, tz=tz),
        until_ms=_parse_boundary_ms(until_text, tz=tz),
        period=None,
        timezone=timezone_label,
    )


def _resolve_timezone(*, timezone_name: str | None, utc: bool) -> tzinfo:
    if utc:
        return timezone.utc
    if timezone_name is not None:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            msg = f"Unknown timezone: {timezone_name}"
            raise ValueError(msg) from exc
    return datetime.now().astimezone().tzinfo or timezone.utc


def _normalize_period(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {
        "today",
        "yesterday",
        "this-week",
        "last-week",
        "this-month",
        "last-month",
    }:
        msg = (
            "Unsupported period. Use today, yesterday, this-week, last-week, "
            "this-month, or last-month."
        )
        raise ValueError(msg)
    return normalized


def _period_bounds_ms(period: str, *, tz: tzinfo) -> tuple[int, int]:
    now = current_time_in_zone(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    if period == "today":
        start = _start_of_day(now)
        end = start + timedelta(days=1)
    elif period == "yesterday":
        end = _start_of_day(now)
        start = end - timedelta(days=1)
    elif period == "this-week":
        start = _start_of_week(now)
        end = start + timedelta(days=7)
    elif period == "last-week":
        end = _start_of_week(now)
        start = end - timedelta(days=7)
    elif period == "this-month":
        start = _start_of_month(now)
        end = _start_of_next_month(start)
    else:
        end = _start_of_month(now)
        start = _start_of_month(end - timedelta(days=1))

    return _datetime_to_ms(start), _datetime_to_ms(end)


def _start_of_day(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_week(value: datetime) -> datetime:
    start_of_day = _start_of_day(value)
    return start_of_day - timedelta(days=start_of_day.weekday())


def _start_of_month(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _start_of_next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1)
    return value.replace(month=value.month + 1, day=1)


def _parse_boundary_ms(value: str | None, *, tz: tzinfo) -> int | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        msg = "Time boundary must not be empty."
        raise ValueError(msg)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        msg = f"Invalid time boundary: {value}"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return _datetime_to_ms(parsed)


def _datetime_to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)
