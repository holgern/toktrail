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
    cache_output: int = 0

    def __post_init__(self) -> None:
        _require_non_negative_int("input", self.input)
        _require_non_negative_int("output", self.output)
        _require_non_negative_int("reasoning", self.reasoning)
        _require_non_negative_int("cache_read", self.cache_read)
        _require_non_negative_int("cache_write", self.cache_write)
        _require_non_negative_int("cache_output", self.cache_output)

    @property
    def total(self) -> int:
        # User-facing total, aligned with harness output.
        return self.input + self.output

    @property
    def prompt_total(self) -> int:
        return self.input + self.cache_read + self.cache_write

    @property
    def output_total(self) -> int:
        return self.output + self.cache_output

    @property
    def accounting_total(self) -> int:
        return (
            self.input
            + self.output
            + self.reasoning
            + self.cache_read
            + self.cache_write
            + self.cache_output
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "output": self.output,
            "reasoning": self.reasoning,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "cache_output": self.cache_output,
            "total": self.total,
            "prompt_total": self.prompt_total,
            "output_total": self.output_total,
            "accounting_total": self.accounting_total,
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
class RunScope:
    harnesses: tuple[str, ...] = ()
    provider_ids: tuple[str, ...] = ()
    model_ids: tuple[str, ...] = ()
    source_session_ids: tuple[str, ...] = ()
    thinking_levels: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.harnesses,
                self.provider_ids,
                self.model_ids,
                self.source_session_ids,
                self.thinking_levels,
                self.agents,
            )
        )

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "harnesses": list(self.harnesses),
            "provider_ids": list(self.provider_ids),
            "model_ids": list(self.model_ids),
            "source_session_ids": list(self.source_session_ids),
            "thinking_levels": list(self.thinking_levels),
            "agents": list(self.agents),
        }


@dataclass(frozen=True)
class Run:
    id: int
    sync_id: str
    name: str | None
    started_at_ms: int
    ended_at_ms: int | None
    scope: RunScope = field(default_factory=RunScope)
    archived_at_ms: int | None = None

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
            "sync_id": self.sync_id,
            "name": self.name,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "active": self.active,
            "archived_at_ms": self.archived_at_ms,
            "scope": self.scope.as_dict(),
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
    config_key: str | None = None
    id_prefix: str = ""
    watch_subdirs: tuple[str, ...] = ()
    shallow_watch: bool = False
    file_based: bool = True
    platform_notes: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "supports_watch": self.supports_watch,
            "supports_environment": self.supports_environment,
            "default_source_path": _path_text(self.default_source_path),
            "source_path_env_vars": list(self.source_path_env_vars),
            "source_path_kind": self.source_path_kind,
            "config_key": self.config_key,
            "id_prefix": self.id_prefix,
            "watch_subdirs": list(self.watch_subdirs),
            "shallow_watch": self.shallow_watch,
            "file_based": self.file_based,
            "platform_notes": self.platform_notes,
        }


@dataclass(frozen=True)
class ImportUsageResult:
    run_id: int | None
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
    rows_scope_excluded: int = 0
    status: str = "ok"
    error_message: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "harness": self.harness,
            "source_path": _path_text(self.source_path),
            "source_session_id": self.source_session_id,
            "rows_seen": self.rows_seen,
            "rows_imported": self.rows_imported,
            "rows_linked": self.rows_linked,
            "rows_scope_excluded": self.rows_scope_excluded,
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
class StateExportResult:
    archive_path: Path
    exported_at_ms: int
    schema_version: int
    machine_id: str
    run_count: int
    source_session_count: int
    usage_event_count: int
    run_event_count: int
    raw_json_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "archive_path": str(self.archive_path),
            "exported_at_ms": self.exported_at_ms,
            "schema_version": self.schema_version,
            "machine_id": self.machine_id,
            "run_count": self.run_count,
            "source_session_count": self.source_session_count,
            "usage_event_count": self.usage_event_count,
            "run_event_count": self.run_event_count,
            "raw_json_count": self.raw_json_count,
        }


@dataclass(frozen=True)
class StateImportConflict:
    kind: str
    harness: str | None = None
    global_dedup_key: str | None = None
    local_fingerprint: str | None = None
    imported_fingerprint: str | None = None
    message: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "harness": self.harness,
            "global_dedup_key": self.global_dedup_key,
            "local_fingerprint": self.local_fingerprint,
            "imported_fingerprint": self.imported_fingerprint,
            "message": self.message,
        }


@dataclass(frozen=True)
class StateImportResult:
    archive_path: Path
    dry_run: bool
    runs_inserted: int
    runs_updated: int
    source_sessions_inserted: int
    source_sessions_updated: int
    usage_events_inserted: int
    usage_events_skipped: int
    run_events_inserted: int
    conflicts: tuple[StateImportConflict, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "archive_path": str(self.archive_path),
            "dry_run": self.dry_run,
            "runs_inserted": self.runs_inserted,
            "runs_updated": self.runs_updated,
            "source_sessions_inserted": self.source_sessions_inserted,
            "source_sessions_updated": self.source_sessions_updated,
            "usage_events_inserted": self.usage_events_inserted,
            "usage_events_skipped": self.usage_events_skipped,
            "run_events_inserted": self.run_events_inserted,
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
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
    tokens: TokenBreakdown
    costs: CostTotals

    @property
    def total_tokens(self) -> int:
        return self.tokens.total

    def as_dict(self) -> dict[str, object]:
        return {
            "agent": self.agent,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            **self.tokens.as_dict(),
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
class StatsReport:
    schema_version: int
    range: dict[str, object]
    totals: dict[str, object]
    sessions: dict[str, object]
    cache: dict[str, object]
    models: tuple[dict[str, object], ...]
    providers: tuple[dict[str, object], ...]
    harnesses: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "range": dict(self.range),
            "totals": dict(self.totals),
            "sessions": dict(self.sessions),
            "cache": dict(self.cache),
            "models": list(self.models),
            "providers": list(self.providers),
            "harnesses": list(self.harnesses),
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
class StatuslineQuota:
    subscription_id: str
    display_name: str | None
    period: str
    status: str
    reset_at: str
    percent_used: Decimal | None
    remaining_usd: Decimal
    over_limit_usd: Decimal
    reset_in_seconds: int | None
    since_ms: int | None = None
    until_ms: int | None = None
    used_usd: Decimal = field(default_factory=lambda: Decimal(0))
    limit_usd: Decimal = field(default_factory=lambda: Decimal(0))

    def as_dict(self) -> dict[str, object]:
        return {
            "subscription_id": self.subscription_id,
            "display_name": self.display_name,
            "period": self.period,
            "status": self.status,
            "reset_at": self.reset_at,
            "percent_used": None
            if self.percent_used is None
            else str(self.percent_used),
            "remaining_usd": str(self.remaining_usd),
            "over_limit_usd": str(self.over_limit_usd),
            "reset_in_seconds": self.reset_in_seconds,
            "since_ms": self.since_ms,
            "until_ms": self.until_ms,
            "used_usd": str(self.used_usd),
            "limit_usd": str(self.limit_usd),
        }


@dataclass(frozen=True)
class StatuslineBurn:
    ratio: float
    label: str

    def as_dict(self) -> dict[str, object]:
        return {
            "ratio": self.ratio,
            "label": self.label,
        }


@dataclass(frozen=True)
class StatuslineContext:
    used_tokens: int | None
    limit_tokens: int | None
    percentage: float
    approximate: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "used_tokens": self.used_tokens,
            "limit_tokens": self.limit_tokens,
            "percentage": self.percentage,
            "approximate": self.approximate,
        }


@dataclass(frozen=True)
class StatuslineCache:
    cached_tokens: int
    cache_reuse_ratio: float | None
    output_cache: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "cached_tokens": self.cached_tokens,
            "cache_reuse_ratio": self.cache_reuse_ratio,
            "output_cache": self.output_cache,
        }


@dataclass(frozen=True)
class StatuslineReport:
    line: str
    generated_at_ms: int
    harness: str | None
    source_session_id: str | None
    source_path: Path | None
    provider_id: str | None
    model_id: str | None
    agent: str | None
    basis: str
    message_count: int
    tokens: TokenBreakdown
    costs: CostTotals
    quota: StatuslineQuota | None = None
    burn: StatuslineBurn | None = None
    context: StatuslineContext | None = None
    cache: StatuslineCache | None = None
    stale_seconds: int | None = None

    @property
    def unpriced_count(self) -> int:
        return self.costs.unpriced_count

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "statusline",
            "line": self.line,
            "generated_at_ms": self.generated_at_ms,
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "source_path": _path_text(self.source_path),
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "agent": self.agent,
            "basis": self.basis,
            "message_count": self.message_count,
            "tokens": self.tokens.as_dict(),
            "costs": self.costs.as_dict(),
            "stale_seconds": self.stale_seconds,
        }
        if self.quota is not None:
            payload["quota"] = self.quota.as_dict()
        if self.burn is not None:
            payload["burn"] = self.burn.as_dict()
        if self.context is not None:
            payload["context"] = self.context.as_dict()
        if self.cache is not None:
            payload["cache"] = self.cache.as_dict()
        return payload


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


@dataclass(frozen=True)
class CacheCallRow:
    ordinal: int
    harness: str
    source_session_id: str
    source_row_id: str | None
    source_message_id: str | None
    provider_id: str
    model_id: str
    thinking_level: str | None
    agent: str | None
    created_ms: int
    completed_ms: int | None
    tokens: TokenBreakdown
    source_cost_usd: Decimal
    actual_cost_usd: Decimal
    virtual_cost_usd: Decimal
    virtual_uncached_cost_usd: Decimal
    virtual_cache_savings_usd: Decimal
    missing_price_kinds: tuple[str, ...]
    context_tokens: int
    actual_price_context_label: str | None
    virtual_price_context_label: str | None
    prompt_like_tokens: int
    cache_reuse_ratio: float
    cache_presence_ratio: float
    source_cost_per_1m_prompt_like: Decimal | None
    source_cost_per_1m_total_tokens: Decimal | None
    virtual_cost_per_1m_prompt_like: Decimal | None
    cache_status: str
    flags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "source_row_id": self.source_row_id,
            "source_message_id": self.source_message_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "thinking_level": self.thinking_level,
            "agent": self.agent,
            "created_ms": self.created_ms,
            "completed_ms": self.completed_ms,
            "tokens": self.tokens.as_dict(),
            "source_cost_usd": str(self.source_cost_usd),
            "actual_cost_usd": str(self.actual_cost_usd),
            "virtual_cost_usd": str(self.virtual_cost_usd),
            "virtual_uncached_cost_usd": str(self.virtual_uncached_cost_usd),
            "virtual_cache_savings_usd": str(self.virtual_cache_savings_usd),
            "missing_price_kinds": list(self.missing_price_kinds),
            "context_tokens": self.context_tokens,
            "actual_price_context_label": self.actual_price_context_label,
            "virtual_price_context_label": self.virtual_price_context_label,
            "prompt_like_tokens": self.prompt_like_tokens,
            "cache_reuse_ratio": self.cache_reuse_ratio,
            "cache_presence_ratio": self.cache_presence_ratio,
            "source_cost_per_1m_prompt_like": (
                str(self.source_cost_per_1m_prompt_like)
                if self.source_cost_per_1m_prompt_like is not None
                else None
            ),
            "source_cost_per_1m_total_tokens": (
                str(self.source_cost_per_1m_total_tokens)
                if self.source_cost_per_1m_total_tokens is not None
                else None
            ),
            "virtual_cost_per_1m_prompt_like": (
                str(self.virtual_cost_per_1m_prompt_like)
                if self.virtual_cost_per_1m_prompt_like is not None
                else None
            ),
            "cache_status": self.cache_status,
            "flags": list(self.flags),
        }


@dataclass(frozen=True)
class CacheClusterRow:
    provider_id: str
    model_id: str
    thinking_level: str | None
    prompt_like_min: int
    prompt_like_max: int
    call_count: int
    hit_count: int
    miss_count: int
    median_hit_source_cost_usd: Decimal | None
    median_miss_source_cost_usd: Decimal | None
    estimated_source_loss_usd: Decimal
    call_ordinals: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "thinking_level": self.thinking_level,
            "prompt_like_min": self.prompt_like_min,
            "prompt_like_max": self.prompt_like_max,
            "call_count": self.call_count,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "median_hit_source_cost_usd": (
                str(self.median_hit_source_cost_usd)
                if self.median_hit_source_cost_usd is not None
                else None
            ),
            "median_miss_source_cost_usd": (
                str(self.median_miss_source_cost_usd)
                if self.median_miss_source_cost_usd is not None
                else None
            ),
            "estimated_source_loss_usd": str(self.estimated_source_loss_usd),
            "call_ordinals": list(self.call_ordinals),
        }


@dataclass(frozen=True)
class SessionCacheAnalysisReport:
    harness: str
    source_session_id: str
    first_created_ms: int | None
    last_created_ms: int | None
    call_count: int
    totals: SessionTotals
    cache_read_tokens: int
    cache_write_tokens: int
    prompt_like_tokens: int
    cache_reuse_ratio: float
    cache_presence_ratio: float
    source_cost_usd: Decimal
    actual_cost_usd: Decimal
    virtual_cost_usd: Decimal
    virtual_uncached_cost_usd: Decimal
    virtual_cache_savings_usd: Decimal
    estimated_source_cache_loss_usd: Decimal
    calls: tuple[CacheCallRow, ...]
    clusters: tuple[CacheClusterRow, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self, *, include_calls: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "session_cache_analysis",
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "first_created_ms": self.first_created_ms,
            "last_created_ms": self.last_created_ms,
            "call_count": self.call_count,
            "totals": self.totals.as_dict(),
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "prompt_like_tokens": self.prompt_like_tokens,
            "cache_reuse_ratio": self.cache_reuse_ratio,
            "cache_presence_ratio": self.cache_presence_ratio,
            "source_cost_usd": str(self.source_cost_usd),
            "actual_cost_usd": str(self.actual_cost_usd),
            "virtual_cost_usd": str(self.virtual_cost_usd),
            "virtual_uncached_cost_usd": str(self.virtual_uncached_cost_usd),
            "virtual_cache_savings_usd": str(self.virtual_cache_savings_usd),
            "estimated_source_cache_loss_usd": str(
                self.estimated_source_cache_loss_usd
            ),
            "clusters": [cluster.as_dict() for cluster in self.clusters],
            "warnings": list(self.warnings),
        }
        if include_calls:
            payload["calls"] = [call.as_dict() for call in self.calls]
        return payload


@dataclass(frozen=True)
class UsageSessionRow:
    key: str
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
    totals: SessionTotals = field(
        default_factory=lambda: SessionTotals(
            tokens=TokenBreakdown(),
            costs=CostTotals(),
        )
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "type": "usage_sessions",
            "order": self.filters.get("order", "desc"),
            "filters": dict(self.filters),
            "sessions": [row.as_dict() for row in self.sessions],
            "totals": self.totals.as_dict(),
        }


@dataclass(frozen=True)
class UsageRunsReport:
    filters: dict[str, object]
    runs: tuple[dict[str, object], ...]
    totals: SessionTotals = field(
        default_factory=lambda: SessionTotals(
            tokens=TokenBreakdown(),
            costs=CostTotals(),
        )
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "type": "usage_runs",
            "order": self.filters.get("order", "desc"),
            "filters": dict(self.filters),
            "runs": list(self.runs),
            "totals": self.totals.as_dict(),
        }


__all__ = [
    "ActivitySummaryRow",
    "CacheCallRow",
    "CacheClusterRow",
    "CostTotals",
    "FinalizedManualRun",
    "HarnessDefinition",
    "HarnessEnvironment",
    "HarnessSummaryRow",
    "ImportUsageResult",
    "ModelSummaryRow",
    "PreparedManualRun",
    "ProviderSummaryRow",
    "RunScope",
    "Run",
    "RunReport",
    "ScanUsageResult",
    "SessionTotals",
    "SourceSessionDiff",
    "SourceSessionSnapshot",
    "SourceSessionSummary",
    "StatuslineBurn",
    "StatuslineCache",
    "StatuslineContext",
    "StatuslineQuota",
    "StatuslineReport",
    "StateExportResult",
    "StateImportConflict",
    "StateImportResult",
    "SessionCacheAnalysisReport",
    "SubscriptionBillingPeriod",
    "SubscriptionUsagePeriod",
    "SubscriptionUsageReport",
    "SubscriptionUsageRow",
    "TokenBreakdown",
    "UnconfiguredModelRow",
    "UsageEvent",
    "UsageSeriesBucket",
    "UsageSeriesInstance",
    "UsageSeriesReport",
    "UsageSessionRow",
    "UsageSessionsReport",
    "UsageRunsReport",
]
