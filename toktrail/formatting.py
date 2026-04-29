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
