from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Literal, cast

TuiMode = Literal["auto", "full", "compact", "micro"]
ResolvedTuiMode = Literal["full", "compact", "micro"]

COMPACT_MAX_COLUMNS = 96
COMPACT_MAX_ROWS = 30
MICRO_MAX_COLUMNS = 68
MICRO_MAX_ROWS = 20


@dataclass(frozen=True)
class TuiDisplay:
    mode: ResolvedTuiMode
    columns: int
    rows: int

    @property
    def compact(self) -> bool:
        return self.mode in {"compact", "micro"}

    @property
    def micro(self) -> bool:
        return self.mode == "micro"


def is_termux() -> bool:
    return bool(os.environ.get("TERMUX_VERSION")) or (
        "/com.termux/files/usr" in os.environ.get("PREFIX", "")
    )


def normalize_tui_mode(value: str | None) -> TuiMode:
    raw = (value or "auto").strip().lower()
    if raw not in {"auto", "full", "compact", "micro"}:
        raise ValueError("TUI mode must be one of: auto, full, compact, micro")
    return cast(TuiMode, raw)


def resolve_tui_display(
    requested: TuiMode = "auto",
    *,
    columns: int | None = None,
    rows: int | None = None,
) -> TuiDisplay:
    if columns is None or rows is None:
        size = shutil.get_terminal_size((80, 24))
        columns = size.columns if columns is None else columns
        rows = size.lines if rows is None else rows
    assert columns is not None
    assert rows is not None

    if requested != "auto":
        return TuiDisplay(mode=requested, columns=columns, rows=rows)

    if columns < MICRO_MAX_COLUMNS or rows < MICRO_MAX_ROWS:
        return TuiDisplay(mode="micro", columns=columns, rows=rows)

    if is_termux() or columns < COMPACT_MAX_COLUMNS or rows < COMPACT_MAX_ROWS:
        return TuiDisplay(mode="compact", columns=columns, rows=rows)

    return TuiDisplay(mode="full", columns=columns, rows=rows)
