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
    machine_id: str | None = None
    harness: str | None = None
    source_session_id: str | None = None
    provider_id: str | None = None
    provider_ids: tuple[str, ...] = ()
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
    ) -> dict[str, object]:
        values: dict[str, object] = {}
        if include_tracking_session and self.tracking_session_id is not None:
            values["run_id"] = self.tracking_session_id
        if self.machine_id is not None:
            values["machine_id"] = self.machine_id
        if self.harness is not None:
            values["harness"] = self.harness
        if self.source_session_id is not None:
            values["source_session_id"] = self.source_session_id
        if self.provider_id is not None:
            values["provider_id"] = self.provider_id
        if self.provider_ids:
            values["provider_ids"] = list(self.provider_ids)
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
            "cache_output": self.tokens.cache_output,
            "total": self.tokens.total,
            "prompt_total": self.tokens.prompt_total,
            "output_total": self.tokens.output_total,
            "accounting_total": self.tokens.accounting_total,
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class HarnessSummaryRow:
    harness: str
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

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "harness": self.harness,
            "message_count": self.message_count,
            **self.tokens.as_dict(),
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class MachineSummaryRow:
    machine_id: str | None
    machine_name: str | None
    machine_label: str
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals

    @property
    def total_tokens(self) -> int:
        return self.tokens.total

    def as_dict(self) -> dict[str, int | float | str | None]:
        return {
            "machine_id": self.machine_id,
            "machine_name": self.machine_name,
            "machine_label": self.machine_label,
            "message_count": self.message_count,
            **self.tokens.as_dict(),
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class ProviderSummaryRow:
    provider_id: str
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

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "provider_id": self.provider_id,
            "message_count": self.message_count,
            **self.tokens.as_dict(),
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
            "agent": self.agent,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            **self.tokens.as_dict(),
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
    cache_output_tokens: int
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
            "cache_output_tokens": self.cache_output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": str(self.cost_usd),
            "baseline_virtual_usd": str(self.baseline_virtual_usd),
            "delta_vs_virtual_usd": str(self.delta_vs_virtual_usd),
        }


@dataclass(frozen=True)
class RunReport:
    session: Run | None
    totals: SessionTotals
    by_provider: list[ProviderSummaryRow]
    by_harness: list[HarnessSummaryRow]
    by_machine: list[MachineSummaryRow]
    by_model: list[ModelSummaryRow]
    by_activity: list[ActivitySummaryRow]
    unconfigured_models: list[UnconfiguredModelRow] = field(default_factory=list)
    simulations: list[SimulationSummaryRow] = field(default_factory=list)
    filters: UsageReportFilter = field(default_factory=UsageReportFilter)

    def as_dict(self) -> dict[str, object]:
        return {
            "session": None if self.session is None else self.session.as_dict(),
            "filters": self.filters.as_dict(),
            "totals": self.totals.as_dict(),
            "by_provider": [row.as_dict() for row in self.by_provider],
            "by_harness": [row.as_dict() for row in self.by_harness],
            "by_machine": [row.as_dict() for row in self.by_machine],
            "by_model": [row.as_dict() for row in self.by_model],
            "by_activity": [row.as_dict() for row in self.by_activity],
            "simulations": [row.as_dict() for row in self.simulations],
            "unconfigured_models": [row.as_dict() for row in self.unconfigured_models],
        }


@dataclass(frozen=True)
class SubscriptionUsagePeriod:
    period: str
    reset_mode: str
    reset_at: str
    status: str
    since_ms: int | None
    until_ms: int | None
    limit_usd: Decimal
    used_usd: Decimal
    remaining_usd: Decimal
    over_limit_usd: Decimal
    percent_used: Decimal | None
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals
    last_since_ms: int | None = None
    last_until_ms: int | None = None
    last_usage_ms: int | None = None
    warnings: tuple[dict[str, object], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "period": self.period,
            "reset_mode": self.reset_mode,
            "reset_at": self.reset_at,
            "status": self.status,
            "since_ms": self.since_ms,
            "until_ms": self.until_ms,
            "last_since_ms": self.last_since_ms,
            "last_until_ms": self.last_until_ms,
            "last_usage_ms": self.last_usage_ms,
            "limit_usd": str(self.limit_usd),
            "used_usd": str(self.used_usd),
            "remaining_usd": str(self.remaining_usd),
            "over_limit_usd": str(self.over_limit_usd),
            "percent_used": None
            if self.percent_used is None
            else str(self.percent_used),
            "message_count": self.message_count,
            "tokens": self.tokens.as_dict(),
            "costs": self.costs.as_dict(),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SubscriptionBillingPeriod:
    period: str
    reset_at: str
    since_ms: int
    until_ms: int
    billing_basis: str
    fixed_cost_usd: Decimal
    value_usd: Decimal
    net_savings_usd: Decimal
    break_even_remaining_usd: Decimal
    break_even_percent: Decimal | None
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals

    def as_dict(self) -> dict[str, object]:
        return {
            "period": self.period,
            "reset_at": self.reset_at,
            "since_ms": self.since_ms,
            "until_ms": self.until_ms,
            "billing_basis": self.billing_basis,
            "fixed_cost_usd": str(self.fixed_cost_usd),
            "value_usd": str(self.value_usd),
            "net_savings_usd": str(self.net_savings_usd),
            "break_even_remaining_usd": str(self.break_even_remaining_usd),
            "break_even_percent": None
            if self.break_even_percent is None
            else str(self.break_even_percent),
            "message_count": self.message_count,
            "tokens": self.tokens.as_dict(),
            "costs": self.costs.as_dict(),
        }


@dataclass(frozen=True)
class SubscriptionUsageRow:
    subscription_id: str
    display_name: str
    timezone: str | None
    usage_provider_ids: tuple[str, ...]
    quota_cost_basis: str
    periods: tuple[SubscriptionUsagePeriod, ...]
    billing: SubscriptionBillingPeriod | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "subscription_id": self.subscription_id,
            "display_name": self.display_name,
            "timezone": self.timezone,
            "usage_provider_ids": list(self.usage_provider_ids),
            "quota_cost_basis": self.quota_cost_basis,
            "periods": [period.as_dict() for period in self.periods],
        }
        if self.billing is not None:
            payload["billing"] = self.billing.as_dict()
        return payload


@dataclass(frozen=True)
class SubscriptionUsageReport:
    generated_at_ms: int
    subscriptions: tuple[SubscriptionUsageRow, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at_ms": self.generated_at_ms,
            "subscriptions": [row.as_dict() for row in self.subscriptions],
        }


@dataclass(frozen=True)
class UsageSeriesFilter:
    granularity: str = "daily"
    tracking_session_id: int | None = None
    machine_id: str | None = None
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
    timezone_name: str | None = None
    utc: bool = False

    def to_usage_report_filter(self) -> UsageReportFilter:
        return UsageReportFilter(
            tracking_session_id=self.tracking_session_id,
            machine_id=self.machine_id,
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


@dataclass(frozen=True)
class UsageSessionsFilter:
    tracking_session_id: int | None = None
    machine_id: str | None = None
    harness: str | None = None
    source_session_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    thinking_level: str | None = None
    agent: str | None = None
    since_ms: int | None = None
    until_ms: int | None = None
    split_thinking: bool = False
    limit: int | None = 10
    order: str = "desc"
    breakdown: bool = False

    def to_usage_report_filter(self) -> UsageReportFilter:
        return UsageReportFilter(
            tracking_session_id=self.tracking_session_id,
            machine_id=self.machine_id,
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
class UsageSessionRow:
    key: str
    origin_machine_id: str | None
    machine_name: str | None
    machine_label: str
    harness: str
    source_session_id: str
    first_ms: int
    last_ms: int
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals
    models: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    by_model: tuple[ModelSummaryRow, ...] = ()

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "key": self.key,
            "origin_machine_id": self.origin_machine_id,
            "machine_name": self.machine_name,
            "machine_label": self.machine_label,
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "first_ms": self.first_ms,
            "last_ms": self.last_ms,
            "message_count": self.message_count,
            "providers": list(self.providers),
            "models": list(self.models),
            "tokens": self.tokens.as_dict(),
            "costs": self.costs.as_dict(),
        }
        if self.by_model:
            result["by_model"] = [row.as_dict() for row in self.by_model]
        return result


@dataclass(frozen=True)
class UsageSessionsReport:
    filters: dict[str, object]
    sessions: tuple[UsageSessionRow, ...]
    totals: SessionTotals

    def as_dict(self) -> dict[str, object]:
        return {
            "type": "usage_sessions",
            "order": self.filters.get("order", "desc"),
            "filters": dict(self.filters),
            "sessions": [row.as_dict() for row in self.sessions],
            "totals": self.totals.as_dict(),
        }


@dataclass(frozen=True)
class UsageRunsFilter:
    tracking_session_id: int | None = None
    machine_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    thinking_level: str | None = None
    agent: str | None = None
    since_ms: int | None = None
    until_ms: int | None = None
    split_thinking: bool = False
    limit: int | None = 10
    order: str = "desc"
    last: bool = False
    include_archived: bool = False
    archived_only: bool = False

    def to_usage_report_filter(self) -> UsageReportFilter:
        return UsageReportFilter(
            tracking_session_id=self.tracking_session_id,
            machine_id=self.machine_id,
            provider_id=self.provider_id,
            model_id=self.model_id,
            thinking_level=self.thinking_level,
            agent=self.agent,
            since_ms=self.since_ms,
            until_ms=self.until_ms,
            split_thinking=self.split_thinking,
        )


@dataclass(frozen=True)
class UsageRunRow:
    run_id: int
    name: str | None
    origin_machine_id: str | None
    machine_name: str | None
    machine_label: str
    started_at_ms: int
    ended_at_ms: int | None
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals
    models: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "name": self.name,
            "origin_machine_id": self.origin_machine_id,
            "machine_name": self.machine_name,
            "machine_label": self.machine_label,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "message_count": self.message_count,
            "providers": list(self.providers),
            "models": list(self.models),
            "tokens": self.tokens.as_dict(),
            "costs": self.costs.as_dict(),
        }


@dataclass(frozen=True)
class UsageRunsReport:
    filters: dict[str, object]
    runs: tuple[UsageRunRow, ...]
    totals: SessionTotals

    def as_dict(self) -> dict[str, object]:
        return {
            "type": "usage_runs",
            "order": self.filters.get("order", "desc"),
            "filters": dict(self.filters),
            "runs": [row.as_dict() for row in self.runs],
            "totals": self.totals.as_dict(),
        }
