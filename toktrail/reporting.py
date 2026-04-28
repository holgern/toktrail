from __future__ import annotations

from dataclasses import dataclass

from toktrail.models import TokenBreakdown, TrackingSession


@dataclass(frozen=True)
class SessionTotals:
    tokens: TokenBreakdown
    cost_usd: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "input": self.tokens.input,
            "output": self.tokens.output,
            "reasoning": self.tokens.reasoning,
            "cache_read": self.tokens.cache_read,
            "cache_write": self.tokens.cache_write,
            "total": self.tokens.total,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class HarnessSummaryRow:
    harness: str
    message_count: int
    total_tokens: int
    cost_usd: float

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "harness": self.harness,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class ModelSummaryRow:
    provider_id: str
    model_id: str
    message_count: int
    tokens: TokenBreakdown
    cost_usd: float

    @property
    def total_tokens(self) -> int:
        return self.tokens.total

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "message_count": self.message_count,
            **self.tokens.as_dict(),
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class AgentSummaryRow:
    agent: str
    message_count: int
    total_tokens: int
    cost_usd: float

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "agent": self.agent,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class TrackingSessionReport:
    session: TrackingSession
    totals: SessionTotals
    by_harness: list[HarnessSummaryRow]
    by_model: list[ModelSummaryRow]
    by_agent: list[AgentSummaryRow]

    def as_dict(self) -> dict[str, object]:
        return {
            "session": {
                "id": self.session.id,
                "name": self.session.name,
                "started_at_ms": self.session.started_at_ms,
                "ended_at_ms": self.session.ended_at_ms,
            },
            "totals": self.totals.as_dict(),
            "by_harness": [row.as_dict() for row in self.by_harness],
            "by_model": [row.as_dict() for row in self.by_model],
            "by_agent": [row.as_dict() for row in self.by_agent],
        }
