from __future__ import annotations

from dataclasses import dataclass, field

from toktrail.models import TokenBreakdown, TrackingSession


@dataclass(frozen=True)
class CostTotals:
    source_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    virtual_cost_usd: float = 0.0
    unpriced_count: int = 0

    @property
    def savings_usd(self) -> float:
        return self.virtual_cost_usd - self.actual_cost_usd

    def add(
        self,
        *,
        source_cost_usd: float = 0.0,
        actual_cost_usd: float = 0.0,
        virtual_cost_usd: float = 0.0,
        unpriced_count: int = 0,
    ) -> CostTotals:
        return CostTotals(
            source_cost_usd=self.source_cost_usd + source_cost_usd,
            actual_cost_usd=self.actual_cost_usd + actual_cost_usd,
            virtual_cost_usd=self.virtual_cost_usd + virtual_cost_usd,
            unpriced_count=self.unpriced_count + unpriced_count,
        )

    def as_dict(self) -> dict[str, float | int]:
        return {
            "source_cost_usd": self.source_cost_usd,
            "actual_cost_usd": self.actual_cost_usd,
            "virtual_cost_usd": self.virtual_cost_usd,
            "savings_usd": self.savings_usd,
            "unpriced_count": self.unpriced_count,
        }


@dataclass(frozen=True)
class UsageReportFilter:
    tracking_session_id: int | None = None
    harness: str | None = None
    source_session_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    thinking_level: str | None = None
    agent: str | None = None
    since_ms: int | None = None
    until_ms: int | None = None
    split_thinking: bool = True

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
        if self.thinking_level is not None:
            values["thinking_level"] = self.thinking_level
        if self.agent is not None:
            values["agent"] = self.agent
        if self.since_ms is not None:
            values["since_ms"] = self.since_ms
        if self.until_ms is not None:
            values["until_ms"] = self.until_ms
        if not self.split_thinking:
            values["split_thinking"] = False
        return values


@dataclass(frozen=True)
class SessionTotals:
    tokens: TokenBreakdown
    costs: CostTotals

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

    def as_dict(self) -> dict[str, int | float]:
        return {
            "input": self.tokens.input,
            "output": self.tokens.output,
            "reasoning": self.tokens.reasoning,
            "cache_read": self.tokens.cache_read,
            "cache_write": self.tokens.cache_write,
            "total": self.tokens.total,
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class HarnessSummaryRow:
    harness: str
    message_count: int
    total_tokens: int
    costs: CostTotals

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

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "harness": self.harness,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class ModelSummaryRow:
    provider_id: str
    model_id: str
    thinking_level: str | None
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals

    @property
    def total_tokens(self) -> int:
        return self.tokens.total

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

    def as_dict(self) -> dict[str, int | float | str | None]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "thinking_level": self.thinking_level,
            "message_count": self.message_count,
            **self.tokens.as_dict(),
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class UnconfiguredModelRow:
    required: tuple[str, ...]
    harness: str
    provider_id: str
    model_id: str
    thinking_level: str | None
    message_count: int
    tokens: TokenBreakdown

    @property
    def total_tokens(self) -> int:
        return self.tokens.total

    def as_dict(self) -> dict[str, object]:
        return {
            "required": list(self.required),
            "harness": self.harness,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "thinking_level": self.thinking_level,
            "message_count": self.message_count,
            **self.tokens.as_dict(),
        }


@dataclass(frozen=True)
class AgentSummaryRow:
    agent: str
    message_count: int
    total_tokens: int
    costs: CostTotals

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

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "agent": self.agent,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class TrackingSessionReport:
    session: TrackingSession | None
    totals: SessionTotals
    by_harness: list[HarnessSummaryRow]
    by_model: list[ModelSummaryRow]
    by_agent: list[AgentSummaryRow]
    unconfigured_models: list[UnconfiguredModelRow] = field(default_factory=list)
    filters: UsageReportFilter = field(default_factory=UsageReportFilter)

    def as_dict(self) -> dict[str, object]:
        return {
            "session": None
            if self.session is None
            else {
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
            "unconfigured_models": [
                row.as_dict() for row in self.unconfigured_models
            ],
        }
