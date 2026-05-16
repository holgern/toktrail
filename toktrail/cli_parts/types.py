from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from toktrail.models import TokenBreakdown
from toktrail.reporting import CostTotals


@dataclass(frozen=True)
class ImportExecutionResult:
    harness: str
    source_path: Path
    run_id: int | None
    rows_seen: int
    rows_imported: int
    rows_skipped: int


@dataclass(frozen=True)
class ReportDisplayFilter:
    price_state: str = "all"
    min_messages: int | None = None
    min_tokens: int | None = None
    sort: str = "actual"
    limit: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "price_state": self.price_state,
            "min_messages": self.min_messages,
            "min_tokens": self.min_tokens,
            "sort": self.sort,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class PriceDisplayFilter:
    table: str = "virtual"
    provider: str | None = None
    model: str | None = None
    query: str | None = None
    category: str | None = None
    release_status: str | None = None
    sort: str = "provider"
    limit: int | None = None


@dataclass(frozen=True)
class WatchTotals:
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals


@dataclass(frozen=True)
class WatchDelta:
    totals: WatchTotals
    by_harness: dict[str, WatchTotals]
