from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from toktrail.config import normalize_identity


def normalize_thinking_level(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        normalized = normalize_identity(stripped)
    except ValueError:
        return None
    if normalized in {"unknown", "default"}:
        return None
    return normalized


@dataclass(frozen=True)
class TokenBreakdown:
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_output: int = 0

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
    source_cost_usd: Decimal
    raw_json: str | None


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


def _normalize_unique_identities(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized_values: list[str] = []
    for value in values:
        normalized = normalize_identity(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(normalized)
    return tuple(normalized_values)


def _normalize_unique_session_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized_values: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(normalized)
    return tuple(normalized_values)


def _normalize_unique_thinking_levels(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized_values: list[str] = []
    for value in values:
        normalized = normalize_thinking_level(value)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(normalized)
    return tuple(normalized_values)


def normalize_run_scope(scope: RunScope | None) -> RunScope:
    if scope is None:
        return RunScope()
    return RunScope(
        harnesses=_normalize_unique_identities(scope.harnesses),
        provider_ids=_normalize_unique_identities(scope.provider_ids),
        model_ids=_normalize_unique_identities(scope.model_ids),
        source_session_ids=_normalize_unique_session_ids(scope.source_session_ids),
        thinking_levels=_normalize_unique_thinking_levels(scope.thinking_levels),
        agents=_normalize_unique_identities(scope.agents),
    )


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
class OpenCodeSessionSummary:
    source_session_id: str
    first_created_ms: int
    last_created_ms: int
    assistant_message_count: int
    tokens: TokenBreakdown
    source_cost_usd: Decimal
