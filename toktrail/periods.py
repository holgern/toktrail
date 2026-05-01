from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Granularity = Literal["daily", "weekly", "monthly"]
Weekday = Literal[
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]
_VALID_WEEKDAYS: frozenset[str] = frozenset(
    {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
)
_YYYYMMDD_RE = re.compile(r"^\d{8}$")


@dataclass(frozen=True)
class ResolvedTimeRange:
    since_ms: int | None
    until_ms: int | None
    period: str | None
    timezone: str


@dataclass(frozen=True)
class TimeBucket:
    key: str
    label: str
    since_ms: int
    until_ms: int


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
    iso_text = _yyyymmdd_to_iso(raw) if _YYYYMMDD_RE.match(raw) else raw
    normalized = iso_text[:-1] + "+00:00" if iso_text.endswith("Z") else iso_text
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


def _yyyymmdd_to_iso(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _is_date_only(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    has_time_sep = "T" in stripped.upper()
    if has_time_sep:
        return False
    if _YYYYMMDD_RE.match(stripped):
        return True
    separators = stripped.count("-")
    return separators <= 2


def parse_cli_boundary(
    value: str | None,
    *,
    tz: tzinfo,
    is_until: bool = False,
) -> int | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        msg = "Time boundary must not be empty."
        raise ValueError(msg)
    date_only = _is_date_only(raw)
    iso_text = _yyyymmdd_to_iso(raw) if _YYYYMMDD_RE.match(raw) else raw
    normalized = iso_text[:-1] + "+00:00" if iso_text.endswith("Z") else iso_text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        msg = f"Invalid time boundary: {value}"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    if date_only and is_until:
        parsed = parsed + timedelta(days=1)
    return _datetime_to_ms(parsed)


def _weekday_offset(weekday: str) -> int:
    mapping = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    return mapping[weekday]


def bucket_for_timestamp(
    timestamp_ms: int,
    *,
    granularity: str,
    tz: tzinfo,
    start_of_week: str = "monday",
    locale: str | None = None,
) -> TimeBucket:
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=tz)
    if granularity == "daily":
        start = _start_of_day(dt)
        end = start + timedelta(days=1)
        key = start.strftime("%Y-%m-%d")
        label = key
    elif granularity == "weekly":
        start = _start_of_day(dt)
        weekday_offset = _weekday_offset(start_of_week)
        days_since = (start.weekday() - weekday_offset) % 7
        start = start - timedelta(days=days_since)
        end = start + timedelta(days=7)
        key = start.strftime("%Y-%m-%d")
        label = key
    elif granularity == "monthly":
        start = _start_of_month(dt)
        end = _start_of_next_month(start)
        key = start.strftime("%Y-%m")
        label = key
    else:
        msg = f"Unsupported granularity: {granularity}"
        raise ValueError(msg)
    return TimeBucket(
        key=key,
        label=label,
        since_ms=_datetime_to_ms(start),
        until_ms=_datetime_to_ms(end),
    )


def iter_time_buckets(
    *,
    granularity: str,
    since_ms: int,
    until_ms: int,
    tz: tzinfo,
    start_of_week: str = "monday",
    locale: str | None = None,
) -> tuple[TimeBucket, ...]:
    if since_ms >= until_ms:
        return ()
    first = bucket_for_timestamp(
        since_ms,
        granularity=granularity,
        tz=tz,
        start_of_week=start_of_week,
        locale=locale,
    )
    buckets = [first]
    while buckets[-1].until_ms < until_ms:
        prev = buckets[-1]
        next_dt = datetime.fromtimestamp(prev.until_ms / 1000, tz=tz)
        if granularity in ("daily", "weekly"):
            next_start = _start_of_day(next_dt)
        else:
            next_start = _start_of_month(next_dt)
        next_bucket = bucket_for_timestamp(
            _datetime_to_ms(next_start),
            granularity=granularity,
            tz=tz,
            start_of_week=start_of_week,
            locale=locale,
        )
        if next_bucket.since_ms >= until_ms:
            break
        if next_bucket.key == buckets[-1].key:
            break
        buckets.append(next_bucket)
    return tuple(buckets)
