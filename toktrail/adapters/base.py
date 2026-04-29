from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from toktrail.config import CostingConfig
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import CostTotals


@dataclass(frozen=True)
class ScanResult:
    source_path: Path
    rows_seen: int
    rows_skipped: int
    events: list[UsageEvent]
    files_seen: int | None = None


@dataclass(frozen=True)
class SourceSessionSummary:
    harness: str
    source_session_id: str
    first_created_ms: int
    last_created_ms: int
    assistant_message_count: int
    tokens: TokenBreakdown
    costs: CostTotals
    models: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()

    @property
    def source_cost_usd(self) -> float:
        return self.costs.source_cost_usd

    @property
    def actual_cost_usd(self) -> float:
        return self.costs.actual_cost_usd

    @property
    def virtual_cost_usd(self) -> float:
        return self.costs.virtual_cost_usd

    @property
    def savings_usd(self) -> float:
        return self.costs.savings_usd

    @property
    def unpriced_count(self) -> int:
        return self.costs.unpriced_count


class HarnessAdapter(Protocol):
    name: str
    display_name: str

    def scan(
        self,
        source_path: Path,
        *,
        source_session_id: str | None = None,
        include_raw_json: bool = True,
    ) -> ScanResult: ...

    def list_sessions(
        self,
        source_path: Path,
        *,
        costing_config: CostingConfig | None = None,
    ) -> list[SourceSessionSummary]: ...

    def parse(self, source_path: Path) -> list[UsageEvent]: ...
