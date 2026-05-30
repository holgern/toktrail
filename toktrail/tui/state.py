from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from toktrail.tui.layout import TuiMode


@dataclass(frozen=True)
class ToktrailTuiState:
    db_path: Path
    config_path: Path
    prices_path: Path
    prices_dir: Path
    subscriptions_path: Path
    initial_area: str | None = None
    timezone_name: str | None = None
    utc: bool = False
    refresh_on_start: bool = True
    tui_mode: TuiMode = "auto"
