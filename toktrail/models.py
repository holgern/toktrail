from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenBreakdown:
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def total(self) -> int:
        return (
            self.input
            + self.output
            + self.reasoning
            + self.cache_read
            + self.cache_write
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "output": self.output,
            "reasoning": self.reasoning,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "total": self.total,
        }


@dataclass(frozen=True)
class UsageEvent:
    harness: str
    source_session_id: str
    source_row_id: str | None
    source_message_id: str | None
    source_dedup_key: str
    global_dedup_key: str
    fingerprint_hash: str
    provider_id: str
    model_id: str
    agent: str | None
    created_ms: int
    completed_ms: int | None
    tokens: TokenBreakdown
    cost_usd: float
    raw_json: str | None

    @property
    def source_cost_usd(self) -> float:
        return self.cost_usd


@dataclass(frozen=True)
class TrackingSession:
    id: int
    name: str | None
    started_at_ms: int
    ended_at_ms: int | None


@dataclass(frozen=True)
class OpenCodeSessionSummary:
    source_session_id: str
    first_created_ms: int
    last_created_ms: int
    assistant_message_count: int
    tokens: TokenBreakdown
    cost_usd: float

    @property
    def source_cost_usd(self) -> float:
        return self.cost_usd
