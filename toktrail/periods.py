from __future__ import annotations

import calendar
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Granularity = Literal["daily", "weekly", "monthly"]
SubscriptionWindowPeriod = Literal["5h", "daily", "weekly", "monthly"]
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


@dataclass(frozen=True)
class SubscriptionCycleWindow:
    period: str
    since_ms: int
    until_ms: int
    label: str


@dataclass(frozen=True)
class ResolvedFirstUseWindow:
    status: str
    since_ms: int | None
    until_ms: int | None


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

    tz = resolve_timezone(timezone_name=timezone_name, utc=utc)
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


def resolve_timezone(*, timezone_name: str | None, utc: bool) -> tzinfo:
    if utc:
        return timezone.utc
    if timezone_name is not None:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            msg = f"Unknown timezone: {timezone_name}"
            raise ValueError(msg) from exc
    return datetime.now().astimezone().tzinfo or timezone.utc


def _resolve_timezone(*, timezone_name: str | None, utc: bool) -> tzinfo:
    return resolve_timezone(timezone_name=timezone_name, utc=utc)


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


def resolve_fixed_subscription_window(
    *,
    period: SubscriptionWindowPeriod,
    reset_at: str,
    timezone_name: str | None,
    now_ms: int | None = None,
) -> SubscriptionCycleWindow:
    normalized_period = period.strip().lower()
    if normalized_period not in {"5h", "daily", "weekly", "monthly"}:
        msg = "period must be one of: 5h, daily, weekly, monthly."
        raise ValueError(msg)

    tz = resolve_timezone(timezone_name=timezone_name, utc=False)
    reset_at_dt = _parse_subscription_reset_at(reset_at, tz=tz)
    if now_ms is None:
        now = current_time_in_zone(tz)
    else:
        now = datetime.fromtimestamp(now_ms / 1000, tz=tz)

    if now < reset_at_dt:
        since = reset_at_dt
    elif normalized_period == "5h":
        elapsed = now - reset_at_dt
        offset = int(elapsed // timedelta(hours=5))
        since = reset_at_dt + timedelta(hours=offset * 5)
    elif normalized_period == "daily":
        elapsed = now - reset_at_dt
        offset = int(elapsed // timedelta(days=1))
        since = reset_at_dt + timedelta(days=offset)
    elif normalized_period == "weekly":
        elapsed = now - reset_at_dt
        offset = int(elapsed // timedelta(days=7))
        since = reset_at_dt + timedelta(days=offset * 7)
    else:
        month_offset = _month_offset_for_timestamp(reset_at_dt, now)
        since = _add_months_with_clamp(reset_at_dt, month_offset)

    if normalized_period == "5h":
        until = since + timedelta(hours=5)
    elif normalized_period == "daily":
        until = since + timedelta(days=1)
    elif normalized_period == "weekly":
        until = since + timedelta(days=7)
    else:
        until = _add_months_with_clamp(since, 1)

    return SubscriptionCycleWindow(
        period=normalized_period,
        since_ms=_datetime_to_ms(since),
        until_ms=_datetime_to_ms(until),
        label=f"{since.date().isoformat()}..{until.date().isoformat()}",
    )


def resolve_subscription_cycle_window(
    *,
    period: str,
    cycle_start: str,
    timezone_name: str | None,
    now_ms: int | None = None,
) -> SubscriptionCycleWindow:
    normalized_period = period.strip().lower()
    return resolve_fixed_subscription_window(
        period=cast(SubscriptionWindowPeriod, normalized_period),
        reset_at=cycle_start,
        timezone_name=timezone_name,
        now_ms=now_ms,
    )


def resolve_first_use_subscription_window(
    *,
    period: SubscriptionWindowPeriod,
    reset_at: str,
    timezone_name: str | None,
    usage_timestamps_ms: Iterable[int],
    now_ms: int | None = None,
) -> ResolvedFirstUseWindow:
    normalized_period = period.strip().lower()
    if normalized_period not in {"5h", "daily", "weekly", "monthly"}:
        msg = "period must be one of: 5h, daily, weekly, monthly."
        raise ValueError(msg)

    tz = resolve_timezone(timezone_name=timezone_name, utc=False)
    reset_at_dt = _parse_subscription_reset_at(reset_at, tz=tz)
    reset_at_ms = _datetime_to_ms(reset_at_dt)
    if now_ms is None:
        now_dt = current_time_in_zone(tz)
        now_limit_ms = _datetime_to_ms(now_dt)
    else:
        now_limit_ms = now_ms
        now_dt = datetime.fromtimestamp(now_ms / 1000, tz=tz)

    relevant = sorted(
        timestamp
        for timestamp in usage_timestamps_ms
        if timestamp >= reset_at_ms and timestamp <= now_limit_ms
    )
    current_start: datetime | None = None
    for timestamp in relevant:
        candidate = datetime.fromtimestamp(timestamp / 1000, tz=tz)
        if current_start is None:
            current_start = candidate
            continue
        if candidate >= _window_end(current_start, normalized_period):
            current_start = candidate

    if current_start is None:
        return ResolvedFirstUseWindow(
            status="waiting_for_first_use",
            since_ms=None,
            until_ms=None,
        )

    current_until = _window_end(current_start, normalized_period)
    if now_dt >= current_until:
        return ResolvedFirstUseWindow(
            status="expired_waiting_for_next_use",
            since_ms=None,
            until_ms=None,
        )

    return ResolvedFirstUseWindow(
        status="active",
        since_ms=_datetime_to_ms(current_start),
        until_ms=_datetime_to_ms(current_until),
    )


def _parse_subscription_reset_at(value: str, *, tz: tzinfo) -> datetime:
    raw = value.strip()
    if not raw:
        msg = "reset_at must not be empty."
        raise ValueError(msg)

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        msg = f"Invalid reset_at: {value}"
        raise ValueError(msg) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed


def _window_end(start: datetime, period: str) -> datetime:
    if period == "5h":
        return start + timedelta(hours=5)
    if period == "daily":
        return start + timedelta(days=1)
    if period == "weekly":
        return start + timedelta(days=7)
    if period == "monthly":
        return _add_months_with_clamp(start, 1)
    msg = f"Unsupported period: {period}"
    raise ValueError(msg)


def _add_months_with_clamp(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(value.day, last_day)
    return value.replace(year=year, month=month, day=day)


def _month_offset_for_timestamp(start: datetime, now: datetime) -> int:
    offset = (now.year - start.year) * 12 + (now.month - start.month)
    candidate = _add_months_with_clamp(start, offset)
    if candidate > now:
        while candidate > now:
            offset -= 1
            candidate = _add_months_with_clamp(start, offset)
    else:
        while _add_months_with_clamp(start, offset + 1) <= now:
            offset += 1
    return offset


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
