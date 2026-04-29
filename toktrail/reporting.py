from __future__ import annotations

from dataclasses import dataclass, field

from toktrail.models import TokenBreakdown, TrackingSession


@dataclass(frozen=True)
class UsageReportFilter:
    tracking_session_id: int | None = None
    harness: str | None = None
    source_session_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    agent: str | None = None
    since_ms: int | None = None
    until_ms: int | None = None

    def as_dict(
        self,
        *,
        include_tracking_session: bool = False,
    ) -> dict[str, int | str]:
        values: dict[str, int | str] = {}
        if include_tracking_session and self.tracking_session_id is not None:
            values["tracking_session_id"] = self.tracking_session_id
        if self.harness is not None:
            values["harness"] = self.harness
        if self.source_session_id is not None:
            values["source_session_id"] = self.source_session_id
        if self.provider_id is not None:
            values["provider_id"] = self.provider_id
        if self.model_id is not None:
            values["model_id"] = self.model_id
        if self.agent is not None:
            values["agent"] = self.agent
        if self.since_ms is not None:
            values["since_ms"] = self.since_ms
        if self.until_ms is not None:
            values["until_ms"] = self.until_ms
        return values


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
    filters: UsageReportFilter = field(default_factory=UsageReportFilter)

    def as_dict(self) -> dict[str, object]:
        return {
            "session": {
                "id": self.session.id,
                "name": self.session.name,
                "started_at_ms": self.session.started_at_ms,
                "ended_at_ms": self.session.ended_at_ms,
            },
            "filters": self.filters.as_dict(),
            "totals": self.totals.as_dict(),
            "by_harness": [row.as_dict() for row in self.by_harness],
            "by_model": [row.as_dict() for row in self.by_model],
            "by_agent": [row.as_dict() for row in self.by_agent],
        }
