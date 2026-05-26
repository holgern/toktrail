"""Internal data models for the insights reporting layer.

These dataclasses represent the insights report structure before
rendering.  They are serializable to JSON and independent of any
renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class InsightSessionMeta:
    """Per-source-session metadata and metrics for insights."""

    harness: str
    source_session_id: str
    origin_machine_id: str | None = None
    source_path: str | None = None
    cwd: str | None = None
    area_path: str | None = None
    area_name: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    duration_ms: int | None = None
    user_messages: int = 0
    assistant_messages: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_output_tokens: int = 0
    actual_cost: Decimal = Decimal(0)
    virtual_cost: Decimal = Decimal(0)
    source_cost: Decimal = Decimal(0)
    unpriced_count: int = 0
    tool_call_count: int = 0
    tool_failure_count: int = 0
    tool_failure_categories: tuple[tuple[str, int], ...] = ()
    models: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    first_prompt_preview: str | None = None

    @property
    def cache_read_ratio(self) -> float:
        prompt_total = (
            self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        )
        if prompt_total == 0:
            return 0.0
        return self.cache_read_tokens / prompt_total

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "origin_machine_id": self.origin_machine_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_ms": self.duration_ms,
            "area_path": self.area_path,
            "area_name": self.area_name,
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_output_tokens": self.cache_output_tokens,
            "actual_cost": str(self.actual_cost),
            "virtual_cost": str(self.virtual_cost),
            "source_cost": str(self.source_cost),
            "unpriced_count": self.unpriced_count,
            "tool_call_count": self.tool_call_count,
            "tool_failure_count": self.tool_failure_count,
            "models": list(self.models),
            "providers": list(self.providers),
        }
        if self.first_prompt_preview is not None:
            d["first_prompt_preview"] = self.first_prompt_preview
        return d


@dataclass(frozen=True)
class InsightGroupRow:
    """Aggregated metrics for a single group (area/machine/harness/model)."""

    key: str
    label: str
    session_count: int = 0
    message_count: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_output_tokens: int = 0
    actual_cost: Decimal = Decimal(0)
    virtual_cost: Decimal = Decimal(0)
    source_cost: Decimal = Decimal(0)
    unpriced_count: int = 0
    tool_call_count: int = 0
    tool_failure_count: int = 0

    @property
    def cache_read_ratio(self) -> float:
        prompt_total = (
            self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        )
        if prompt_total == 0:
            return 0.0
        return self.cache_read_tokens / prompt_total

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "session_count": self.session_count,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_output_tokens": self.cache_output_tokens,
            "actual_cost": str(self.actual_cost),
            "virtual_cost": str(self.virtual_cost),
            "source_cost": str(self.source_cost),
            "unpriced_count": self.unpriced_count,
            "tool_call_count": self.tool_call_count,
            "tool_failure_count": self.tool_failure_count,
            "cache_read_ratio": round(self.cache_read_ratio, 4),
        }


@dataclass(frozen=True)
class InsightAggregate:
    """Aggregate metrics for the selected period."""

    session_count: int = 0
    message_count: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_output_tokens: int = 0
    actual_cost: Decimal = Decimal(0)
    virtual_cost: Decimal = Decimal(0)
    source_cost: Decimal = Decimal(0)
    unpriced_count: int = 0
    tool_call_count: int = 0
    tool_failure_count: int = 0
    by_area: tuple[InsightGroupRow, ...] = ()
    by_machine: tuple[InsightGroupRow, ...] = ()
    by_harness: tuple[InsightGroupRow, ...] = ()
    by_model: tuple[InsightGroupRow, ...] = ()
    by_provider: tuple[InsightGroupRow, ...] = ()
    top_sessions: tuple[InsightSessionMeta, ...] = ()

    @property
    def cache_read_ratio(self) -> float:
        prompt_total = (
            self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        )
        if prompt_total == 0:
            return 0.0
        return self.cache_read_tokens / prompt_total

    def as_dict(self) -> dict[str, object]:
        return {
            "session_count": self.session_count,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_output_tokens": self.cache_output_tokens,
            "actual_cost": str(self.actual_cost),
            "virtual_cost": str(self.virtual_cost),
            "source_cost": str(self.source_cost),
            "unpriced_count": self.unpriced_count,
            "tool_call_count": self.tool_call_count,
            "tool_failure_count": self.tool_failure_count,
            "cache_read_ratio": round(self.cache_read_ratio, 4),
            "by_area": [row.as_dict() for row in self.by_area],
            "by_machine": [row.as_dict() for row in self.by_machine],
            "by_harness": [row.as_dict() for row in self.by_harness],
            "by_model": [row.as_dict() for row in self.by_model],
            "by_provider": [row.as_dict() for row in self.by_provider],
        }


@dataclass(frozen=True)
class InsightDelta:
    """Delta between current and previous period for one metric."""

    metric: str
    current: Decimal | int | float
    previous: Decimal | int | float | None
    change: str  # e.g. "+23%", "-8%", "new", "removed", "0%"
    direction: Literal["up", "down", "flat", "new", "removed"]

    def as_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "current": (
                str(self.current) if isinstance(self.current, Decimal) else self.current
            ),
            "previous": (
                str(self.previous)
                if isinstance(self.previous, Decimal)
                else self.previous
            ),
            "change": self.change,
            "direction": self.direction,
        }


@dataclass(frozen=True)
class InsightAnomaly:
    """Detected anomaly for a session or aggregate."""

    kind: Literal["cost", "tokens", "errors", "unpriced", "cache"]
    severity: Literal["low", "medium", "high"]
    session_key: str  # harness/source_session_id or group key
    message: str
    value: Decimal | int | float
    baseline: Decimal | int | float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "session_key": self.session_key,
            "message": self.message,
            "value": (
                str(self.value) if isinstance(self.value, Decimal) else self.value
            ),
            "baseline": (
                str(self.baseline)
                if isinstance(self.baseline, Decimal)
                else self.baseline
            ),
        }


@dataclass(frozen=True)
class DeterministicSuggestion:
    """Actionable deterministic suggestion."""

    kind: str
    severity: Literal["info", "warning", "critical"]
    title: str
    detail: str
    command: str | None = None

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
        }
        if self.command is not None:
            d["command"] = self.command
        return d


@dataclass(frozen=True)
class InsightsReport:
    """Top-level insights report model."""

    filters: dict[str, object]
    period_label: str
    current: InsightAggregate
    previous: InsightAggregate | None = None
    deltas: tuple[InsightDelta, ...] = ()
    anomalies: tuple[InsightAnomaly, ...] = ()
    suggestions: tuple[DeterministicSuggestion, ...] = ()
    sessions_to_inspect: tuple[InsightSessionMeta, ...] = ()

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "type": "insights_report",
            "filters": dict(self.filters),
            "period_label": self.period_label,
            "current": self.current.as_dict(),
        }
        if self.previous is not None:
            result["previous"] = self.previous.as_dict()
        result["deltas"] = [d.as_dict() for d in self.deltas]
        result["anomalies"] = [a.as_dict() for a in self.anomalies]
        result["suggestions"] = [s.as_dict() for s in self.suggestions]
        result["sessions_to_inspect"] = [s.as_dict() for s in self.sessions_to_inspect]
        return result
