from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from toktrail.adapters.base import SourceSessionSummary
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import (
    AgentSummaryRow,
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


def summarize_event_totals(events: Iterable[UsageEvent]) -> SessionTotals:
    tokens = TokenBreakdown()
    cost_usd = 0.0
    for event in events:
        tokens = add_tokens(tokens, event.tokens)
        cost_usd += event.cost_usd
    return SessionTotals(tokens=tokens, cost_usd=cost_usd)


def summarize_events_by_harness(
    events: Iterable[UsageEvent],
) -> list[HarnessSummaryRow]:
    grouped: dict[str, _UsageBucket] = {}
    for event in events:
        bucket = grouped.setdefault(event.harness, _UsageBucket())
        bucket.add(event)
    return sorted(
        (
            HarnessSummaryRow(
                harness=harness,
                message_count=bucket.message_count,
                total_tokens=bucket.tokens.total,
                cost_usd=bucket.cost_usd,
            )
            for harness, bucket in grouped.items()
        ),
        key=lambda row: (-row.cost_usd, -row.total_tokens, row.harness),
    )


def summarize_events_by_model(events: Iterable[UsageEvent]) -> list[ModelSummaryRow]:
    grouped: dict[tuple[str, str], _UsageBucket] = {}
    for event in events:
        key = (event.provider_id, event.model_id)
        bucket = grouped.setdefault(key, _UsageBucket())
        bucket.add(event)
    return sorted(
        (
            ModelSummaryRow(
                provider_id=provider_id,
                model_id=model_id,
                message_count=bucket.message_count,
                tokens=bucket.tokens,
                cost_usd=bucket.cost_usd,
            )
            for (provider_id, model_id), bucket in grouped.items()
        ),
        key=lambda row: (
            -row.cost_usd,
            -row.message_count,
            row.provider_id,
            row.model_id,
        ),
    )


def summarize_events_by_agent(events: Iterable[UsageEvent]) -> list[AgentSummaryRow]:
    grouped: dict[str, _UsageBucket] = {}
    for event in events:
        agent = event.agent or "unknown"
        bucket = grouped.setdefault(agent, _UsageBucket())
        bucket.add(event)
    return sorted(
        (
            AgentSummaryRow(
                agent=agent,
                message_count=bucket.message_count,
                total_tokens=bucket.tokens.total,
                cost_usd=bucket.cost_usd,
            )
            for agent, bucket in grouped.items()
        ),
        key=lambda row: (-row.cost_usd, -row.total_tokens, row.agent),
    )


def summarize_events_by_source_session(
    harness: str,
    events: Iterable[UsageEvent],
    *,
    source_paths_by_session: Mapping[str, Iterable[str | Path]] | None = None,
) -> list[SourceSessionSummary]:
    grouped: dict[str, _SourceSessionBucket] = {}
    for event in events:
        bucket = grouped.setdefault(event.source_session_id, _SourceSessionBucket())
        bucket.add(event)

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
                cost_usd=bucket.cost_usd,
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
class _UsageBucket:
    message_count: int = 0
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    cost_usd: float = 0.0

    def add(self, event: UsageEvent) -> None:
        self.message_count += 1
        self.tokens = add_tokens(self.tokens, event.tokens)
        self.cost_usd += event.cost_usd


@dataclass
class _SourceSessionBucket:
    first_created_ms: int = 0
    last_created_ms: int = 0
    message_count: int = 0
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)
    providers: set[str] = field(default_factory=set)
    source_paths: set[str] = field(default_factory=set)

    def add(self, event: UsageEvent) -> None:
        if self.message_count == 0:
            self.first_created_ms = event.created_ms
            self.last_created_ms = event.created_ms
        else:
            self.first_created_ms = min(self.first_created_ms, event.created_ms)
            self.last_created_ms = max(self.last_created_ms, event.created_ms)
        self.message_count += 1
        self.tokens = add_tokens(self.tokens, event.tokens)
        self.cost_usd += event.cost_usd
        self.models.add(event.model_id)
        self.providers.add(event.provider_id)

    def add_paths(self, paths: Iterable[str | Path]) -> None:
        for path in paths:
            self.source_paths.add(str(path))
