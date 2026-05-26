from __future__ import annotations

from datetime import datetime, timezone


def format_epoch_ms(value: int | None, *, utc: bool = False) -> str:
    if value is None:
        return "-"
    dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    if utc:
        return dt.isoformat(timespec="seconds")
    return dt.astimezone().isoformat(timespec="seconds")


def format_epoch_ms_compact(value: int | None, *, utc: bool = False) -> str:
    if value is None:
        return "-"
    dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    if utc:
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def format_duration_seconds(
    value: int,
    *,
    compact: bool = True,
    max_parts: int = 2,
) -> str:
    """Format a non-negative duration into a human-readable string.

    Args:
        value: Duration in seconds. Negative values are clamped to zero.
        compact: If True, use short unit labels (2h 19m). If False, use
            long labels (2 hours 19 minutes).
        max_parts: Maximum number of non-zero unit parts to include.

    Returns:
        A formatted duration string. Returns "now" for zero.
    """
    seconds = max(0, value)
    if seconds == 0:
        return "now"

    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    parts: list[tuple[int, str, str]] = []
    if days:
        parts.append((days, "d", "day"))
    if hours:
        parts.append((hours, "h", "hour"))
    if minutes:
        parts.append((minutes, "m", "minute"))
    if secs:
        parts.append((secs, "s", "second"))

    parts = parts[:max_parts]

    if compact:
        return " ".join(f"{v}{u}" for v, u, _ in parts)

    long_parts: list[str] = []
    for v, _, long_u in parts:
        long_parts.append(f"{v} {long_u + ('s' if v != 1 else '')}")
    return " ".join(long_parts)
