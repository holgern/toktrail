from __future__ import annotations

from toktrail.adapters.base import SourceSessionSummary as InternalSourceSessionSummary
from toktrail.api.models import (
    AgentSummaryRow,
    CostTotals,
    HarnessSummaryRow,
    ModelSummaryRow,
    SessionTotals,
    SourceSessionSummary,
    TokenBreakdown,
    TrackingSession,
    TrackingSessionReport,
    UsageEvent,
)
from toktrail.models import (
    TokenBreakdown as InternalTokenBreakdown,
)
from toktrail.models import (
    TrackingSession as InternalTrackingSession,
)
from toktrail.models import (
    UsageEvent as InternalUsageEvent,
)
from toktrail.reporting import (
    AgentSummaryRow as InternalAgentSummaryRow,
)
from toktrail.reporting import (
    CostTotals as InternalCostTotals,
)
from toktrail.reporting import (
    HarnessSummaryRow as InternalHarnessSummaryRow,
)
from toktrail.reporting import (
    ModelSummaryRow as InternalModelSummaryRow,
)
from toktrail.reporting import (
    SessionTotals as InternalSessionTotals,
)
from toktrail.reporting import (
    TrackingSessionReport as InternalTrackingSessionReport,
)


def _to_public_token_breakdown(value: InternalTokenBreakdown) -> TokenBreakdown:
    return TokenBreakdown(
        input=value.input,
        output=value.output,
        reasoning=value.reasoning,
        cache_read=value.cache_read,
        cache_write=value.cache_write,
    )


def _to_public_cost_totals(value: InternalCostTotals) -> CostTotals:
    return CostTotals(
        source_cost_usd=value.source_cost_usd,
        actual_cost_usd=value.actual_cost_usd,
        virtual_cost_usd=value.virtual_cost_usd,
        unpriced_count=value.unpriced_count,
    )


def _to_public_tracking_session(
    value: InternalTrackingSession | None,
) -> TrackingSession | None:
    if value is None:
        return None
    return TrackingSession(
        id=value.id,
        name=value.name,
        started_at_ms=value.started_at_ms,
        ended_at_ms=value.ended_at_ms,
    )


def _to_public_usage_event(
    value: InternalUsageEvent,
    *,
    include_raw_json: bool = False,
) -> UsageEvent:
    return UsageEvent(
        harness=value.harness,
        source_session_id=value.source_session_id,
        source_row_id=value.source_row_id,
        source_message_id=value.source_message_id,
        source_dedup_key=value.source_dedup_key,
        global_dedup_key=value.global_dedup_key,
        fingerprint_hash=value.fingerprint_hash,
        provider_id=value.provider_id,
        model_id=value.model_id,
        thinking_level=value.thinking_level,
        agent=value.agent,
        created_ms=value.created_ms,
        completed_ms=value.completed_ms,
        tokens=_to_public_token_breakdown(value.tokens),
        source_cost_usd=value.source_cost_usd,
        raw_json=value.raw_json if include_raw_json else None,
    )


def _to_public_source_summary(
    value: InternalSourceSessionSummary,
) -> SourceSessionSummary:
    return SourceSessionSummary(
        harness=value.harness,
        source_session_id=value.source_session_id,
        first_created_ms=value.first_created_ms,
        last_created_ms=value.last_created_ms,
        assistant_message_count=value.assistant_message_count,
        tokens=_to_public_token_breakdown(value.tokens),
        costs=_to_public_cost_totals(value.costs),
        models=tuple(value.models),
        providers=tuple(value.providers),
        source_paths=tuple(value.source_paths),
    )


def _to_public_session_totals(
    value: InternalSessionTotals,
    *,
    message_count: int,
) -> SessionTotals:
    return SessionTotals(
        tokens=_to_public_token_breakdown(value.tokens),
        costs=_to_public_cost_totals(value.costs),
        message_count=message_count,
    )


def _to_public_harness_row(value: InternalHarnessSummaryRow) -> HarnessSummaryRow:
    return HarnessSummaryRow(
        harness=value.harness,
        message_count=value.message_count,
        total_tokens=value.total_tokens,
        costs=_to_public_cost_totals(value.costs),
    )


def _to_public_model_row(value: InternalModelSummaryRow) -> ModelSummaryRow:
    return ModelSummaryRow(
        provider_id=value.provider_id,
        model_id=value.model_id,
        thinking_level=value.thinking_level,
        message_count=value.message_count,
        tokens=_to_public_token_breakdown(value.tokens),
        costs=_to_public_cost_totals(value.costs),
    )


def _to_public_agent_row(value: InternalAgentSummaryRow) -> AgentSummaryRow:
    return AgentSummaryRow(
        agent=value.agent,
        message_count=value.message_count,
        total_tokens=value.total_tokens,
        costs=_to_public_cost_totals(value.costs),
    )


def _to_public_report(value: InternalTrackingSessionReport) -> TrackingSessionReport:
    by_harness = tuple(_to_public_harness_row(row) for row in value.by_harness)
    by_model = tuple(_to_public_model_row(row) for row in value.by_model)
    by_agent = tuple(_to_public_agent_row(row) for row in value.by_agent)
    message_count = sum(row.message_count for row in by_harness)
    filters: dict[str, object] = {
        key: filter_value for key, filter_value in value.filters.as_dict().items()
    }
    return TrackingSessionReport(
        session=_to_public_tracking_session(value.session),
        totals=_to_public_session_totals(value.totals, message_count=message_count),
        by_harness=by_harness,
        by_model=by_model,
        by_agent=by_agent,
        filters=filters,
    )


__all__ = [
    "_to_public_agent_row",
    "_to_public_cost_totals",
    "_to_public_harness_row",
    "_to_public_model_row",
    "_to_public_report",
    "_to_public_session_totals",
    "_to_public_source_summary",
    "_to_public_token_breakdown",
    "_to_public_tracking_session",
    "_to_public_usage_event",
]
