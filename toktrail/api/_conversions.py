from __future__ import annotations

from toktrail.adapters.base import SourceSessionSummary as InternalSourceSessionSummary
from toktrail.api.models import (
    ActivitySummaryRow,
    CostTotals,
    HarnessSummaryRow,
    ModelSummaryRow,
    ProviderSummaryRow,
    Run,
    RunReport,
    SessionTotals,
    SourceSessionSummary,
    SubscriptionUsagePeriod,
    SubscriptionUsageReport,
    SubscriptionUsageRow,
    TokenBreakdown,
    UnconfiguredModelRow,
    UsageEvent,
    UsageSeriesBucket,
    UsageSeriesInstance,
    UsageSeriesReport,
)
from toktrail.models import (
    Run as InternalTrackingSession,
)
from toktrail.models import (
    TokenBreakdown as InternalTokenBreakdown,
)
from toktrail.models import (
    UsageEvent as InternalUsageEvent,
)
from toktrail.reporting import (
    ActivitySummaryRow as InternalActivitySummaryRow,
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
    ProviderSummaryRow as InternalProviderSummaryRow,
)
from toktrail.reporting import (
    RunReport as InternalRunReport,
)
from toktrail.reporting import (
    SessionTotals as InternalSessionTotals,
)
from toktrail.reporting import (
    SubscriptionUsagePeriod as InternalSubscriptionUsagePeriod,
)
from toktrail.reporting import (
    SubscriptionUsageReport as InternalSubscriptionUsageReport,
)
from toktrail.reporting import (
    SubscriptionUsageRow as InternalSubscriptionUsageRow,
)
from toktrail.reporting import (
    UnconfiguredModelRow as InternalUnconfiguredModelRow,
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
) -> Run | None:
    if value is None:
        return None
    return Run(
        id=value.id,
        name=value.name,
        started_at_ms=value.started_at_ms,
        ended_at_ms=value.ended_at_ms,
    )


def _to_public_run(
    value: InternalTrackingSession | None,
) -> Run | None:
    """Alias for _to_public_tracking_session for the new Run terminology."""
    return _to_public_tracking_session(value)


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


def _to_public_provider_row(value: InternalProviderSummaryRow) -> ProviderSummaryRow:
    return ProviderSummaryRow(
        provider_id=value.provider_id,
        message_count=value.message_count,
        tokens=_to_public_token_breakdown(value.tokens),
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


def _to_public_activity_row(value: InternalActivitySummaryRow) -> ActivitySummaryRow:
    return ActivitySummaryRow(
        agent=value.agent,
        message_count=value.message_count,
        total_tokens=value.total_tokens,
        costs=_to_public_cost_totals(value.costs),
    )


def _to_public_unconfigured_model_row(
    value: InternalUnconfiguredModelRow,
) -> UnconfiguredModelRow:
    return UnconfiguredModelRow(
        required=value.required,
        harness=value.harness,
        provider_id=value.provider_id,
        model_id=value.model_id,
        thinking_level=value.thinking_level,
        message_count=value.message_count,
        tokens=_to_public_token_breakdown(value.tokens),
    )


def _to_public_report(value: InternalRunReport) -> RunReport:
    by_provider = tuple(_to_public_provider_row(row) for row in value.by_provider)
    by_harness = tuple(_to_public_harness_row(row) for row in value.by_harness)
    by_model = tuple(_to_public_model_row(row) for row in value.by_model)
    by_activity = tuple(_to_public_activity_row(row) for row in value.by_activity)
    unconfigured_models = tuple(
        _to_public_unconfigured_model_row(row) for row in value.unconfigured_models
    )
    message_count = sum(row.message_count for row in by_harness)
    filters: dict[str, object] = {
        key: filter_value for key, filter_value in value.filters.as_dict().items()
    }
    return RunReport(
        session=_to_public_tracking_session(value.session),
        totals=_to_public_session_totals(value.totals, message_count=message_count),
        by_provider=by_provider,
        by_harness=by_harness,
        by_model=by_model,
        by_activity=by_activity,
        unconfigured_models=unconfigured_models,
        filters=filters,
    )


def _to_public_subscription_period(
    value: InternalSubscriptionUsagePeriod,
) -> SubscriptionUsagePeriod:
    return SubscriptionUsagePeriod(
        period=value.period,
        since_ms=value.since_ms,
        until_ms=value.until_ms,
        limit_usd=value.limit_usd,
        used_usd=value.used_usd,
        remaining_usd=value.remaining_usd,
        over_limit_usd=value.over_limit_usd,
        percent_used=value.percent_used,
        message_count=value.message_count,
        tokens=_to_public_token_breakdown(value.tokens),
        costs=_to_public_cost_totals(value.costs),
    )


def _to_public_subscription_row(
    value: InternalSubscriptionUsageRow,
) -> SubscriptionUsageRow:
    return SubscriptionUsageRow(
        provider_id=value.provider_id,
        display_name=value.display_name,
        timezone=value.timezone,
        cycle_start=value.cycle_start,
        cost_basis=value.cost_basis,
        periods=tuple(
            _to_public_subscription_period(period) for period in value.periods
        ),
    )


def _to_public_subscription_report(
    value: InternalSubscriptionUsageReport,
) -> SubscriptionUsageReport:
    return SubscriptionUsageReport(
        generated_at_ms=value.generated_at_ms,
        subscriptions=tuple(
            _to_public_subscription_row(subscription)
            for subscription in value.subscriptions
        ),
    )


def _to_public_series_report(
    value: object,
) -> UsageSeriesReport:
    from toktrail.reporting import UsageSeriesReport as InternalUsageSeriesReport

    assert isinstance(value, InternalUsageSeriesReport)
    return UsageSeriesReport(
        granularity=value.granularity,
        timezone=value.timezone,
        locale=value.locale,
        start_of_week=value.start_of_week,
        filters=value.filters,
        buckets=tuple(_to_public_series_bucket(b) for b in value.buckets),
        instances=tuple(_to_public_series_instance(i) for i in value.instances),
        totals=_to_public_session_totals(
            value.totals,
            message_count=sum(bucket.message_count for bucket in value.buckets),
        ),
    )


def _to_public_series_bucket(
    value: object,
) -> UsageSeriesBucket:
    from toktrail.reporting import UsageSeriesBucket as InternalUsageSeriesBucket

    assert isinstance(value, InternalUsageSeriesBucket)
    return UsageSeriesBucket(
        key=value.key,
        label=value.label,
        since_ms=value.since_ms,
        until_ms=value.until_ms,
        message_count=value.message_count,
        tokens=_to_public_token_breakdown(value.tokens),
        costs=_to_public_cost_totals(value.costs),
        models=value.models,
        by_model=tuple(_to_public_model_row(m) for m in value.by_model),
    )


def _to_public_series_instance(
    value: object,
) -> UsageSeriesInstance:
    from toktrail.reporting import UsageSeriesInstance as InternalUsageSeriesInstance

    assert isinstance(value, InternalUsageSeriesInstance)
    return UsageSeriesInstance(
        instance_key=value.instance_key,
        instance_label=value.instance_label,
        harness=value.harness,
        source_session_id=value.source_session_id,
        buckets=tuple(_to_public_series_bucket(b) for b in value.buckets),
        totals=_to_public_session_totals(
            value.totals,
            message_count=sum(bucket.message_count for bucket in value.buckets),
        ),
    )


__all__ = [
    "_to_public_activity_row",
    "_to_public_cost_totals",
    "_to_public_harness_row",
    "_to_public_model_row",
    "_to_public_provider_row",
    "_to_public_report",
    "_to_public_session_totals",
    "_to_public_source_summary",
    "_to_public_subscription_report",
    "_to_public_token_breakdown",
    "_to_public_tracking_session",
    "_to_public_unconfigured_model_row",
    "_to_public_usage_event",
    "_to_public_series_report",
]
