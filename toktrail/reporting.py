from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from toktrail.models import Run, TokenBreakdown


@dataclass(frozen=True)
class CostTotals:
    source_cost_usd: Decimal = Decimal(0)
    actual_cost_usd: Decimal = Decimal(0)
    virtual_cost_usd: Decimal = Decimal(0)
    unpriced_count: int = 0

    @property
    def savings_usd(self) -> Decimal:
        return self.virtual_cost_usd - self.actual_cost_usd

    def add(
        self,
        *,
        source_cost_usd: Decimal = Decimal(0),
        actual_cost_usd: Decimal = Decimal(0),
        virtual_cost_usd: Decimal = Decimal(0),
        unpriced_count: int = 0,
    ) -> CostTotals:
        return CostTotals(
            source_cost_usd=self.source_cost_usd + source_cost_usd,
            actual_cost_usd=self.actual_cost_usd + actual_cost_usd,
            virtual_cost_usd=self.virtual_cost_usd + virtual_cost_usd,
            unpriced_count=self.unpriced_count + unpriced_count,
        )

    def as_dict(self) -> dict[str, str | int]:
        return {
            "source_cost_usd": str(self.source_cost_usd),
            "actual_cost_usd": str(self.actual_cost_usd),
            "virtual_cost_usd": str(self.virtual_cost_usd),
            "savings_usd": str(self.savings_usd),
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
    split_thinking: bool = False

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
        if self.split_thinking:
            values["split_thinking"] = True
        return values


@dataclass(frozen=True)
class SessionTotals:
    tokens: TokenBreakdown
    costs: CostTotals

    @property
    def source_cost_usd(self) -> Decimal:
        return self.costs.source_cost_usd

    @property
    def actual_cost_usd(self) -> Decimal:
        return self.costs.actual_cost_usd

    @property
    def virtual_cost_usd(self) -> Decimal:
        return self.costs.virtual_cost_usd

    @property
    def savings_usd(self) -> Decimal:
        return self.costs.savings_usd

    @property
    def unpriced_count(self) -> int:
        return self.costs.unpriced_count

    def as_dict(self) -> dict[str, object]:
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
    def source_cost_usd(self) -> Decimal:
        return self.costs.source_cost_usd

    @property
    def actual_cost_usd(self) -> Decimal:
        return self.costs.actual_cost_usd

    @property
    def virtual_cost_usd(self) -> Decimal:
        return self.costs.virtual_cost_usd

    @property
    def savings_usd(self) -> Decimal:
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
    def source_cost_usd(self) -> Decimal:
        return self.costs.source_cost_usd

    @property
    def actual_cost_usd(self) -> Decimal:
        return self.costs.actual_cost_usd

    @property
    def virtual_cost_usd(self) -> Decimal:
        return self.costs.virtual_cost_usd

    @property
    def savings_usd(self) -> Decimal:
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
class ActivitySummaryRow:
    agent: str | None

    message_count: int
    total_tokens: int
    costs: CostTotals

    @property
    def source_cost_usd(self) -> Decimal:
        return self.costs.source_cost_usd

    @property
    def actual_cost_usd(self) -> Decimal:
        return self.costs.actual_cost_usd

    @property
    def virtual_cost_usd(self) -> Decimal:
        return self.costs.virtual_cost_usd

    @property
    def savings_usd(self) -> Decimal:
        return self.costs.savings_usd

    @property
    def unpriced_count(self) -> int:
        return self.costs.unpriced_count

    def as_dict(self) -> dict[str, int | float | str | None]:
        return {
            "agent": self.agent,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class SimulationSummaryRow:
    target_provider: str
    target_model: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    cost_usd: Decimal
    baseline_virtual_usd: Decimal
    delta_vs_virtual_usd: Decimal

    def as_dict(self) -> dict[str, object]:
        return {
            "target_provider": self.target_provider,
            "target_model": self.target_model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": str(self.cost_usd),
            "baseline_virtual_usd": str(self.baseline_virtual_usd),
            "delta_vs_virtual_usd": str(self.delta_vs_virtual_usd),
        }


@dataclass(frozen=True)
class RunReport:
    session: Run | None
    totals: SessionTotals
    by_harness: list[HarnessSummaryRow]
    by_model: list[ModelSummaryRow]
    by_activity: list[ActivitySummaryRow]
    unconfigured_models: list[UnconfiguredModelRow] = field(default_factory=list)
    simulations: list[SimulationSummaryRow] = field(default_factory=list)
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
            "by_activity": [row.as_dict() for row in self.by_activity],
            "simulations": [row.as_dict() for row in self.simulations],
            "unconfigured_models": [row.as_dict() for row in self.unconfigured_models],
        }


@dataclass(frozen=True)
class UsageSeriesFilter:
    granularity: str = "daily"
    tracking_session_id: int | None = None
    harness: str | None = None
    source_session_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    thinking_level: str | None = None
    agent: str | None = None
    since_ms: int | None = None
    until_ms: int | None = None
    split_thinking: bool = False
    project: str | None = None
    instances: bool = False
    breakdown: bool = False
    start_of_week: str = "monday"
    locale: str | None = None
    order: str = "desc"

    def to_usage_report_filter(self) -> UsageReportFilter:
        return UsageReportFilter(
            tracking_session_id=self.tracking_session_id,
            harness=self.harness,
            source_session_id=self.source_session_id,
            provider_id=self.provider_id,
            model_id=self.model_id,
            thinking_level=self.thinking_level,
            agent=self.agent,
            since_ms=self.since_ms,
            until_ms=self.until_ms,
            split_thinking=self.split_thinking,
        )


@dataclass(frozen=True)
class UsageSeriesBucket:
    key: str
    label: str
    since_ms: int
    until_ms: int
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals
    models: tuple[str, ...] = ()
    by_model: tuple[ModelSummaryRow, ...] = ()
    simulations: tuple[SimulationSummaryRow, ...] = ()

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "key": self.key,
            "label": self.label,
            "since_ms": self.since_ms,
            "until_ms": self.until_ms,
            "message_count": self.message_count,
            "models": list(self.models),
            "tokens": self.tokens.as_dict(),
            "costs": self.costs.as_dict(),
        }
        if self.by_model:
            result["by_model"] = [row.as_dict() for row in self.by_model]
        if self.simulations:
            result["simulations"] = [row.as_dict() for row in self.simulations]
            result["by_model"] = [row.as_dict() for row in self.by_model]
        return result


@dataclass(frozen=True)
class UsageSeriesInstance:
    instance_key: str
    instance_label: str
    harness: str | None
    source_session_id: str | None
    buckets: tuple[UsageSeriesBucket, ...]
    totals: SessionTotals

    def as_dict(self) -> dict[str, object]:
        return {
            "instance_key": self.instance_key,
            "instance_label": self.instance_label,
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "buckets": [b.as_dict() for b in self.buckets],
            "totals": self.totals.as_dict(),
        }


@dataclass(frozen=True)
class UsageSeriesReport:
    granularity: str
    timezone: str
    locale: str | None
    start_of_week: str | None
    filters: dict[str, object]
    buckets: tuple[UsageSeriesBucket, ...] = ()
    instances: tuple[UsageSeriesInstance, ...] = ()
    totals: SessionTotals = field(
        default_factory=lambda: SessionTotals(
            tokens=TokenBreakdown(),
            costs=CostTotals(),
        )
    )

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "type": "usage_series",
            "granularity": self.granularity,
            "timezone": self.timezone,
            "locale": self.locale,
            "start_of_week": self.start_of_week,
            "order": self.filters.get("order", "desc"),
            "filters": dict(self.filters),
        }
        if self.instances:
            result["instances"] = [inst.as_dict() for inst in self.instances]
        else:
            result["buckets"] = [b.as_dict() for b in self.buckets]
        result["totals"] = self.totals.as_dict()
        return result
