from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from toktrail.adapters.base import SourceSessionSummary
from toktrail.config import CostingConfig, default_costing_config
from toktrail.costing import CostBreakdown, UsageCostAtom
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import (
    AgentSummaryRow,
    CostTotals,
    HarnessSummaryRow,
    ModelSummaryRow,
    SessionTotals,
)


def add_tokens(left: TokenBreakdown, right: TokenBreakdown) -> TokenBreakdown:
    return TokenBreakdown(
        input=left.input + right.input,
        output=left.output + right.output,
        reasoning=left.reasoning + right.reasoning,
        cache_read=left.cache_read + right.cache_read,
        cache_write=left.cache_write + right.cache_write,
    )


def summarize_event_totals(
    events: Iterable[UsageEvent],
    *,
    costing_config: CostingConfig | None = None,
) -> SessionTotals:
    config = costing_config or default_costing_config()
    tokens = TokenBreakdown()
    costs = CostTotals()
    for atom in _usage_cost_atom_map(
        events,
        key_fn=lambda event: (
            event.harness,
            event.provider_id,
            event.model_id,
            event.thinking_level,
        ),
    ).values():
        tokens = add_tokens(tokens, atom.tokens)
        costs = _add_cost_breakdown(costs, atom.compute_costs(config))
    return SessionTotals(tokens=tokens, costs=costs)


def summarize_events_by_harness(
    events: Iterable[UsageEvent],
    *,
    costing_config: CostingConfig | None = None,
) -> list[HarnessSummaryRow]:
    config = costing_config or default_costing_config()
    grouped: dict[str, _AggregateBucket] = {}
    for atom in _usage_cost_atom_map(
        events,
        key_fn=lambda event: (
            event.harness,
            event.provider_id,
            event.model_id,
            event.thinking_level,
        ),
    ).values():
        bucket = grouped.setdefault(atom.harness, _AggregateBucket())
        bucket.add_atom(atom, config)
    return sorted(
        (
            HarnessSummaryRow(
                harness=harness,
                message_count=bucket.message_count,
                total_tokens=bucket.tokens.total,
                costs=bucket.costs,
            )
            for harness, bucket in grouped.items()
        ),
        key=lambda row: (-row.actual_cost_usd, -row.total_tokens, row.harness),
    )


def summarize_events_by_model(
    events: Iterable[UsageEvent],
    *,
    costing_config: CostingConfig | None = None,
    split_thinking: bool = True,
) -> list[ModelSummaryRow]:
    config = costing_config or default_costing_config()
    grouped: dict[tuple[str, str, str | None], _AggregateBucket] = {}
    for atom in _usage_cost_atom_map(
        events,
        key_fn=lambda event: (
            event.harness,
            event.provider_id,
            event.model_id,
            event.thinking_level if split_thinking else None,
        ),
    ).values():
        key = (
            atom.provider_id,
            atom.model_id,
            atom.thinking_level if split_thinking else None,
        )
        bucket = grouped.setdefault(key, _AggregateBucket())
        bucket.add_atom(atom, config)
    return sorted(
        (
            ModelSummaryRow(
                provider_id=provider_id,
                model_id=model_id,
                thinking_level=thinking_level,
                message_count=bucket.message_count,
                tokens=bucket.tokens,
                costs=bucket.costs,
            )
            for (provider_id, model_id, thinking_level), bucket in grouped.items()
        ),
        key=lambda row: (
            -row.actual_cost_usd,
            -row.message_count,
            row.provider_id,
            row.model_id,
            row.thinking_level or "",
        ),
    )


def summarize_events_by_agent(
    events: Iterable[UsageEvent],
    *,
    costing_config: CostingConfig | None = None,
) -> list[AgentSummaryRow]:
    config = costing_config or default_costing_config()
    grouped: dict[str, _AggregateBucket] = {}
    for atom in _usage_cost_atom_map(
        events,
        key_fn=lambda event: (
            event.harness,
            event.provider_id,
            event.model_id,
            event.thinking_level,
            event.agent or "unknown",
        ),
    ).values():
        bucket = grouped.setdefault(atom.agent, _AggregateBucket())
        bucket.add_atom(atom, config)
    return sorted(
        (
            AgentSummaryRow(
                agent=agent,
                message_count=bucket.message_count,
                total_tokens=bucket.tokens.total,
                costs=bucket.costs,
            )
            for agent, bucket in grouped.items()
        ),
        key=lambda row: (-row.actual_cost_usd, -row.total_tokens, row.agent),
    )


def summarize_events_by_source_session(
    harness: str,
    events: Iterable[UsageEvent],
    *,
    source_paths_by_session: Mapping[str, Iterable[str | Path]] | None = None,
    costing_config: CostingConfig | None = None,
) -> list[SourceSessionSummary]:
    config = costing_config or default_costing_config()
    grouped: dict[str, _SourceSessionBucket] = {}
    for event in events:
        bucket = grouped.setdefault(event.source_session_id, _SourceSessionBucket())
        bucket.add_event_metadata(event)

    for key, atom in _usage_cost_atom_map(
        events,
        key_fn=lambda event: (
            event.harness,
            event.source_session_id,
            event.provider_id,
            event.model_id,
            event.thinking_level,
        ),
    ).items():
        source_session_id = str(key[1])
        grouped[source_session_id].add_atom(atom, config)

    for source_session_id, paths in (source_paths_by_session or {}).items():
        grouped.setdefault(source_session_id, _SourceSessionBucket()).add_paths(paths)

    return sorted(
        (
            SourceSessionSummary(
                harness=harness,
                source_session_id=source_session_id,
                first_created_ms=bucket.first_created_ms,
                last_created_ms=bucket.last_created_ms,
                assistant_message_count=bucket.message_count,
                tokens=bucket.tokens,
                costs=bucket.costs,
                models=tuple(sorted(bucket.models)),
                providers=tuple(sorted(bucket.providers)),
                source_paths=tuple(sorted(bucket.source_paths)),
            )
            for source_session_id, bucket in grouped.items()
            if bucket.message_count > 0
        ),
        key=lambda summary: (summary.last_created_ms, summary.source_session_id),
        reverse=True,
    )


@dataclass
class _AggregateBucket:
    message_count: int = 0
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    costs: CostTotals = field(default_factory=CostTotals)

    def add_atom(self, atom: UsageCostAtom, config: CostingConfig) -> None:
        self.message_count += atom.message_count
        self.tokens = add_tokens(self.tokens, atom.tokens)
        self.costs = _add_cost_breakdown(self.costs, atom.compute_costs(config))


@dataclass
class _SourceSessionBucket:
    first_created_ms: int = 0
    last_created_ms: int = 0
    message_count: int = 0
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    costs: CostTotals = field(default_factory=CostTotals)
    models: set[str] = field(default_factory=set)
    providers: set[str] = field(default_factory=set)
    source_paths: set[str] = field(default_factory=set)

    def add_event_metadata(self, event: UsageEvent) -> None:
        if self.message_count == 0:
            self.first_created_ms = event.created_ms
            self.last_created_ms = event.created_ms
        else:
            self.first_created_ms = min(self.first_created_ms, event.created_ms)
            self.last_created_ms = max(self.last_created_ms, event.created_ms)
        self.models.add(event.model_id)
        self.providers.add(event.provider_id)

    def add_atom(self, atom: UsageCostAtom, config: CostingConfig) -> None:
        self.message_count += atom.message_count
        self.tokens = add_tokens(self.tokens, atom.tokens)
        self.costs = _add_cost_breakdown(self.costs, atom.compute_costs(config))

    def add_paths(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            self.source_paths.add(str(path))


def _add_cost_breakdown(costs: CostTotals, breakdown: CostBreakdown) -> CostTotals:
    return costs.add(
        source_cost_usd=breakdown.source_cost_usd,
        actual_cost_usd=breakdown.actual_cost_usd,
        virtual_cost_usd=breakdown.virtual_cost_usd,
        unpriced_count=breakdown.unpriced_count,
    )


def _usage_cost_atom_map(
    events: Iterable[UsageEvent],
    *,
    key_fn: Callable[[UsageEvent], tuple[object, ...]],
) -> dict[tuple[object, ...], UsageCostAtom]:
    grouped: dict[tuple[object, ...], _CostAtomBucket] = {}
    for event in events:
        key = key_fn(event)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = _CostAtomBucket(
                harness=event.harness,
                provider_id=event.provider_id,
                model_id=event.model_id,
                thinking_level=event.thinking_level,
                agent=event.agent or "unknown",
            )
            grouped[key] = bucket
        bucket.add(event)
    return {key: bucket.as_atom() for key, bucket in grouped.items()}


@dataclass
class _CostAtomBucket:
    harness: str
    provider_id: str
    model_id: str
    thinking_level: str | None
    agent: str
    message_count: int = 0
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    source_cost_usd: float = 0.0

    def add(self, event: UsageEvent) -> None:
        self.message_count += 1
        self.tokens = add_tokens(self.tokens, event.tokens)
        self.source_cost_usd += event.source_cost_usd

    def as_atom(self) -> UsageCostAtom:
        return UsageCostAtom(
            harness=self.harness,
            provider_id=self.provider_id,
            model_id=self.model_id,
            thinking_level=self.thinking_level,
            agent=self.agent,
            message_count=self.message_count,
            tokens=self.tokens,
            source_cost_usd=self.source_cost_usd,
        )
