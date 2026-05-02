from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from toktrail.errors import AmbiguousSourceSessionError, SourcePathError


def _require_non_negative_int(name: str, value: int) -> None:
    if value < 0:
        msg = f"{name} must be non-negative, got {value!r}"
        raise ValueError(msg)


def _require_non_negative_decimal(name: str, value: Decimal) -> None:
    if value < Decimal(0):
        msg = f"{name} must be non-negative, got {value!r}"
        raise ValueError(msg)


def _path_text(value: Path | None) -> str | None:
    return str(value) if value is not None else None


@dataclass(frozen=True)
class TokenBreakdown:
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __post_init__(self) -> None:
        _require_non_negative_int("input", self.input)
        _require_non_negative_int("output", self.output)
        _require_non_negative_int("reasoning", self.reasoning)
        _require_non_negative_int("cache_read", self.cache_read)
        _require_non_negative_int("cache_write", self.cache_write)

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
class CostTotals:
    source_cost_usd: Decimal = Decimal(0)
    actual_cost_usd: Decimal = Decimal(0)
    virtual_cost_usd: Decimal = Decimal(0)
    unpriced_count: int = 0

    def __post_init__(self) -> None:
        _require_non_negative_decimal("source_cost_usd", self.source_cost_usd)
        _require_non_negative_decimal("actual_cost_usd", self.actual_cost_usd)
        _require_non_negative_decimal("virtual_cost_usd", self.virtual_cost_usd)
        _require_non_negative_int("unpriced_count", self.unpriced_count)

    @property
    def savings_usd(self) -> Decimal:
        return self.virtual_cost_usd - self.actual_cost_usd

    def as_dict(self) -> dict[str, str | int]:
        return {
            "source_cost_usd": str(self.source_cost_usd),
            "actual_cost_usd": str(self.actual_cost_usd),
            "virtual_cost_usd": str(self.virtual_cost_usd),
            "savings_usd": str(self.savings_usd),
            "unpriced_count": self.unpriced_count,
        }


@dataclass(frozen=True)
class Run:
    id: int
    name: str | None
    started_at_ms: int
    ended_at_ms: int | None

    @property
    def active(self) -> bool:
        return self.ended_at_ms is None

    @property
    def started_ms(self) -> int:
        return self.started_at_ms

    @property
    def ended_ms(self) -> int | None:
        return self.ended_at_ms

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "active": self.active,
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
    thinking_level: str | None
    agent: str | None
    created_ms: int
    completed_ms: int | None
    tokens: TokenBreakdown
    source_cost_usd: Decimal = field(default_factory=lambda: Decimal(0))
    raw_json: str | None = None

    def __post_init__(self) -> None:
        _require_non_negative_decimal("source_cost_usd", self.source_cost_usd)

    def as_dict(self, *, include_raw_json: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "source_row_id": self.source_row_id,
            "source_message_id": self.source_message_id,
            "source_dedup_key": self.source_dedup_key,
            "global_dedup_key": self.global_dedup_key,
            "fingerprint_hash": self.fingerprint_hash,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "thinking_level": self.thinking_level,
            "agent": self.agent,
            "created_ms": self.created_ms,
            "completed_ms": self.completed_ms,
            "tokens": self.tokens.as_dict(),
            "source_cost_usd": self.source_cost_usd,
        }
        if include_raw_json:
            payload["raw_json"] = self.raw_json
        return payload


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
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "first_created_ms": self.first_created_ms,
            "last_created_ms": self.last_created_ms,
            "assistant_message_count": self.assistant_message_count,
            "tokens": self.tokens.as_dict(),
            **self.costs.as_dict(),
            "models": list(self.models),
            "providers": list(self.providers),
            "source_paths": list(self.source_paths),
        }


@dataclass(frozen=True)
class SourceSessionSnapshot:
    harness: str
    source_path: Path | None
    captured_ms: int
    sessions: tuple[SourceSessionSummary, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "harness": self.harness,
            "source_path": _path_text(self.source_path),
            "captured_ms": self.captured_ms,
            "sessions": [session.as_dict() for session in self.sessions],
        }


@dataclass(frozen=True)
class SourceSessionDiff:
    harness: str
    before_count: int
    after_count: int
    new_sessions: tuple[SourceSessionSummary, ...]
    updated_sessions: tuple[SourceSessionSummary, ...]
    unchanged_sessions: tuple[SourceSessionSummary, ...]

    @property
    def candidates(self) -> tuple[SourceSessionSummary, ...]:
        return self.new_sessions + self.updated_sessions

    def require_single_candidate(self) -> SourceSessionSummary:
        candidates = self.candidates
        if not candidates:
            msg = f"No new or updated source sessions found for harness {self.harness}."
            raise SourcePathError(msg)
        if len(candidates) > 1:
            candidate_ids = ", ".join(
                sorted(summary.source_session_id for summary in candidates)
            )
            msg = (
                f"Multiple source sessions changed for harness {self.harness}: "
                f"{candidate_ids}"
            )
            raise AmbiguousSourceSessionError(msg)
        return next(iter(candidates))

    def as_dict(self) -> dict[str, object]:
        return {
            "harness": self.harness,
            "before_count": self.before_count,
            "after_count": self.after_count,
            "new_sessions": [session.as_dict() for session in self.new_sessions],
            "updated_sessions": [
                session.as_dict() for session in self.updated_sessions
            ],
            "unchanged_sessions": [
                session.as_dict() for session in self.unchanged_sessions
            ],
            "candidates": [session.as_dict() for session in self.candidates],
        }


@dataclass(frozen=True)
class HarnessDefinition:
    name: str
    display_name: str
    supports_watch: bool
    supports_environment: bool
    default_source_path: Path | None
    source_path_env_vars: tuple[str, ...] = ()
    source_path_kind: str = "path"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "supports_watch": self.supports_watch,
            "supports_environment": self.supports_environment,
            "default_source_path": _path_text(self.default_source_path),
            "source_path_env_vars": list(self.source_path_env_vars),
            "source_path_kind": self.source_path_kind,
        }


@dataclass(frozen=True)
class ImportUsageResult:
    tracking_session_id: int | None
    harness: str
    source_path: Path | None
    source_session_id: str | None
    rows_seen: int
    rows_imported: int
    rows_skipped: int
    events_seen: int
    events_imported: int
    events_skipped: int
    files_seen: int | None = None
    since_ms: int | None = None
    first_event_ms: int | None = None
    last_event_ms: int | None = None
    rows_linked: int = 0
    status: str = "ok"
    error_message: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "tracking_session_id": self.tracking_session_id,
            "harness": self.harness,
            "source_path": _path_text(self.source_path),
            "source_session_id": self.source_session_id,
            "rows_seen": self.rows_seen,
            "rows_imported": self.rows_imported,
            "rows_linked": self.rows_linked,
            "rows_skipped": self.rows_skipped,
            "events_seen": self.events_seen,
            "events_imported": self.events_imported,
            "events_skipped": self.events_skipped,
            "status": self.status,
            "error_message": self.error_message,
            "files_seen": self.files_seen,
            "since_ms": self.since_ms,
            "first_event_ms": self.first_event_ms,
            "last_event_ms": self.last_event_ms,
        }


@dataclass(frozen=True)
class SessionTotals:
    tokens: TokenBreakdown
    costs: CostTotals
    message_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            **self.tokens.as_dict(),
            **self.costs.as_dict(),
            "message_count": self.message_count,
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

    def as_dict(self) -> dict[str, object]:
        return {
            "harness": self.harness,
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

    def as_dict(self) -> dict[str, object]:
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

    def as_dict(self) -> dict[str, object]:
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

    def as_dict(self) -> dict[str, object]:
        return {
            "agent": self.agent,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            **self.costs.as_dict(),
        }


@dataclass(frozen=True)
class RunReport:
    session: Run | None
    totals: SessionTotals
    by_provider: tuple[ProviderSummaryRow, ...]
    by_harness: tuple[HarnessSummaryRow, ...]
    by_model: tuple[ModelSummaryRow, ...]
    by_activity: tuple[ActivitySummaryRow, ...]
    unconfigured_models: tuple[UnconfiguredModelRow, ...] = ()
    filters: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "filters", dict(self.filters))

    def as_dict(self) -> dict[str, object]:
        return {
            "session": None if self.session is None else self.session.as_dict(),
            "filters": dict(self.filters),
            "totals": self.totals.as_dict(),
            "by_provider": [row.as_dict() for row in self.by_provider],
            "by_harness": [row.as_dict() for row in self.by_harness],
            "by_model": [row.as_dict() for row in self.by_model],
            "by_activity": [row.as_dict() for row in self.by_activity],
            "unconfigured_models": [row.as_dict() for row in self.unconfigured_models],
        }


@dataclass(frozen=True)
class SubscriptionUsagePeriod:
    period: str
    since_ms: int
    until_ms: int
    limit_usd: Decimal
    used_usd: Decimal
    remaining_usd: Decimal
    over_limit_usd: Decimal
    percent_used: Decimal | None
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals

    def as_dict(self) -> dict[str, object]:
        return {
            "period": self.period,
            "since_ms": self.since_ms,
            "until_ms": self.until_ms,
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
        }


@dataclass(frozen=True)
class SubscriptionUsageRow:
    provider_id: str
    display_name: str
    timezone: str | None
    cycle_start: str
    cost_basis: str
    periods: tuple[SubscriptionUsagePeriod, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "timezone": self.timezone,
            "cycle_start": self.cycle_start,
            "cost_basis": self.cost_basis,
            "periods": [period.as_dict() for period in self.periods],
        }


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
class ScanUsageResult:
    harness: str
    source_path: Path
    source_session_id: str | None
    rows_seen: int
    rows_skipped: int
    events: tuple[UsageEvent, ...]
    files_seen: int | None = None

    def as_dict(self, *, include_events: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "harness": self.harness,
            "source_path": str(self.source_path),
            "source_session_id": self.source_session_id,
            "rows_seen": self.rows_seen,
            "rows_skipped": self.rows_skipped,
            "event_count": len(self.events),
            "files_seen": self.files_seen,
        }
        if include_events:
            payload["events"] = [event.as_dict() for event in self.events]
        return payload


@dataclass(frozen=True)
class HarnessEnvironment:
    harness: str
    source_path: Path | None
    env: dict[str, str]
    shell_exports: tuple[str, ...]
    instructions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "env", dict(self.env))

    def as_dict(self) -> dict[str, object]:
        return {
            "harness": self.harness,
            "source_path": _path_text(self.source_path),
            "env": dict(self.env),
            "shell_exports": list(self.shell_exports),
            "instructions": list(self.instructions),
        }


@dataclass(frozen=True)
class PreparedManualRun:
    run: Run
    harness: str
    source_path: Path | None
    before_snapshot: SourceSessionSnapshot
    environment: HarnessEnvironment

    def as_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.as_dict(),
            "harness": self.harness,
            "source_path": _path_text(self.source_path),
            "before_snapshot": self.before_snapshot.as_dict(),
            "environment": self.environment.as_dict(),
        }


@dataclass(frozen=True)
class FinalizedManualRun:
    run: Run
    source_session: SourceSessionSummary
    source_diff: SourceSessionDiff
    import_result: ImportUsageResult
    report: RunReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.as_dict(),
            "source_session": self.source_session.as_dict(),
            "source_diff": self.source_diff.as_dict(),
            "import_result": self.import_result.as_dict(),
            "report": self.report.as_dict(),
        }


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


__all__ = [
    "ActivitySummaryRow",
    "CostTotals",
    "FinalizedManualRun",
    "HarnessDefinition",
    "HarnessEnvironment",
    "HarnessSummaryRow",
    "ImportUsageResult",
    "ModelSummaryRow",
    "PreparedManualRun",
    "ProviderSummaryRow",
    "Run",
    "RunReport",
    "ScanUsageResult",
    "SessionTotals",
    "SourceSessionDiff",
    "SourceSessionSnapshot",
    "SourceSessionSummary",
    "SubscriptionUsagePeriod",
    "SubscriptionUsageReport",
    "SubscriptionUsageRow",
    "TokenBreakdown",
    "UnconfiguredModelRow",
    "UsageEvent",
    "UsageSeriesBucket",
    "UsageSeriesInstance",
    "UsageSeriesReport",
]
