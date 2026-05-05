from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from statistics import median
from typing import Final

from toktrail.config import CostingConfig, default_costing_config
from toktrail.costing import (
    UsageCostAtom,
    cost_from_price,
    resolve_price_resolution,
    uncached_tokens,
)
from toktrail.models import TokenBreakdown, UsageEvent

_HIT_STATUSES: Final[set[str]] = {"hit", "partial"}
_MISS_STATUSES: Final[set[str]] = {"miss", "cold", "warming", "write"}


@dataclass(frozen=True)
class CacheCallAnalysis:
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


@dataclass(frozen=True)
class CacheClusterAnalysis:
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
    call_ordinals: tuple[int, ...]


@dataclass(frozen=True)
class SessionCacheAnalysis:
    harness: str
    source_session_id: str
    first_created_ms: int | None
    last_created_ms: int | None
    call_count: int
    tokens: TokenBreakdown
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
    unpriced_count: int
    calls: tuple[CacheCallAnalysis, ...]
    clusters: tuple[CacheClusterAnalysis, ...]
    warnings: tuple[str, ...] = ()


def prompt_like_tokens(tokens: TokenBreakdown) -> int:
    return tokens.input + tokens.cache_read + tokens.cache_write


def cache_reuse_ratio(tokens: TokenBreakdown) -> float:
    return float(tokens.cache_read) / max(tokens.input + tokens.cache_read, 1)


def cache_presence_ratio(tokens: TokenBreakdown) -> float:
    return float(tokens.cache_read) / max(prompt_like_tokens(tokens), 1)


def classify_cache_status(tokens: TokenBreakdown, *, ordinal: int) -> str:
    prompt_tokens = prompt_like_tokens(tokens)
    if prompt_tokens <= 0:
        return "unknown"
    if tokens.cache_read == 0 and tokens.cache_write > 0:
        return "warming" if ordinal == 1 else "write"
    ratio = cache_reuse_ratio(tokens)
    if ordinal == 1 and tokens.cache_read == 0:
        return "cold"
    if ratio >= 0.80:
        return "hit"
    if ratio >= 0.20:
        return "partial"
    return "miss"


def analyze_usage_events(
    events: list[UsageEvent],
    *,
    costing_config: CostingConfig | None = None,
    cluster_tolerance: float = 0.05,
) -> SessionCacheAnalysis:
    if cluster_tolerance < 0:
        msg = "cluster_tolerance must be non-negative."
        raise ValueError(msg)
    if not events:
        msg = "No usage events available for cache analysis."
        raise ValueError(msg)

    ordered_events = sorted(
        events,
        key=lambda event: (event.created_ms, event.global_dedup_key),
    )
    config = costing_config or default_costing_config()

    first = ordered_events[0]
    warnings: list[str] = []
    if any(event.harness != first.harness for event in ordered_events):
        warnings.append("mixed_harnesses")
    if any(
        event.source_session_id != first.source_session_id for event in ordered_events
    ):
        warnings.append("mixed_source_sessions")

    calls: list[CacheCallAnalysis] = []
    total_tokens = TokenBreakdown()
    total_source = Decimal(0)
    total_actual = Decimal(0)
    total_virtual = Decimal(0)
    total_virtual_uncached = Decimal(0)
    total_virtual_savings = Decimal(0)
    total_unpriced = 0
    prompt_like_total = 0

    for ordinal, event in enumerate(ordered_events, start=1):
        atom = UsageCostAtom(
            harness=event.harness,
            provider_id=event.provider_id,
            model_id=event.model_id,
            thinking_level=event.thinking_level,
            agent=event.agent,
            message_count=1,
            tokens=event.tokens,
            source_cost_usd=event.source_cost_usd,
        )
        costs = atom.compute_costs(config)
        resolution = resolve_price_resolution(
            harness=event.harness,
            provider_id=event.provider_id,
            model_id=event.model_id,
            config=config,
            tokens=event.tokens,
        )
        uncached = uncached_tokens(event.tokens)
        virtual_uncached = (
            cost_from_price(uncached, resolution.virtual_price)
            if resolution.virtual_price is not None
            else Decimal(0)
        )
        virtual_savings = virtual_uncached - costs.virtual_cost_usd
        prompt_like = prompt_like_tokens(event.tokens)
        source_per_1m = (
            (event.source_cost_usd * Decimal(1_000_000)) / Decimal(prompt_like)
            if prompt_like > 0
            else None
        )
        source_per_1m_total = (
            (event.source_cost_usd * Decimal(1_000_000)) / Decimal(event.tokens.total)
            if event.tokens.total > 0
            else None
        )
        virtual_per_1m_prompt = (
            (costs.virtual_cost_usd * Decimal(1_000_000)) / Decimal(prompt_like)
            if prompt_like > 0
            else None
        )
        status = classify_cache_status(event.tokens, ordinal=ordinal)
        call_flags: list[str] = []
        if prompt_like == 0 and event.tokens.cache_output > 0:
            call_flags.append("cache_only")
        if (
            prompt_like > 0
            and event.tokens.cache_read == 0
            and event.tokens.cache_write == 0
            and event.tokens.cache_output == 0
        ):
            call_flags.append("missing_cache_fields")

        calls.append(
            CacheCallAnalysis(
                ordinal=ordinal,
                harness=event.harness,
                source_session_id=event.source_session_id,
                source_row_id=event.source_row_id,
                source_message_id=event.source_message_id,
                provider_id=event.provider_id,
                model_id=event.model_id,
                thinking_level=event.thinking_level,
                agent=event.agent,
                created_ms=event.created_ms,
                completed_ms=event.completed_ms,
                tokens=event.tokens,
                source_cost_usd=event.source_cost_usd,
                actual_cost_usd=costs.actual_cost_usd,
                virtual_cost_usd=costs.virtual_cost_usd,
                virtual_uncached_cost_usd=virtual_uncached,
                virtual_cache_savings_usd=virtual_savings,
                missing_price_kinds=resolution.missing_kinds,
                context_tokens=(
                    resolution.selected_context_tokens
                    if resolution.selected_context_tokens is not None
                    else prompt_like
                ),
                actual_price_context_label=resolution.actual_context_label,
                virtual_price_context_label=resolution.virtual_context_label,
                prompt_like_tokens=prompt_like,
                cache_reuse_ratio=cache_reuse_ratio(event.tokens),
                cache_presence_ratio=cache_presence_ratio(event.tokens),
                source_cost_per_1m_prompt_like=source_per_1m,
                source_cost_per_1m_total_tokens=source_per_1m_total,
                virtual_cost_per_1m_prompt_like=virtual_per_1m_prompt,
                cache_status=status,
                flags=tuple(call_flags),
            )
        )

        total_tokens = TokenBreakdown(
            input=total_tokens.input + event.tokens.input,
            output=total_tokens.output + event.tokens.output,
            reasoning=total_tokens.reasoning + event.tokens.reasoning,
            cache_read=total_tokens.cache_read + event.tokens.cache_read,
            cache_write=total_tokens.cache_write + event.tokens.cache_write,
            cache_output=total_tokens.cache_output + event.tokens.cache_output,
        )
        total_source += event.source_cost_usd
        total_actual += costs.actual_cost_usd
        total_virtual += costs.virtual_cost_usd
        total_virtual_uncached += virtual_uncached
        total_virtual_savings += virtual_savings
        total_unpriced += costs.unpriced_count
        prompt_like_total += prompt_like

    flags_by_index: list[set[str]] = [set(call.flags) for call in calls]
    clusters, estimated_source_cache_loss = _build_clusters(
        calls,
        flags_by_index=flags_by_index,
        tolerance=cluster_tolerance,
    )
    calls = [
        replace(call, flags=tuple(sorted(flags_by_index[index])))
        for index, call in enumerate(calls)
    ]
    _annotate_sequence_flags(
        calls,
        flags_by_index=flags_by_index,
        tolerance=cluster_tolerance,
    )
    calls = [
        replace(call, flags=tuple(sorted(flags_by_index[index])))
        for index, call in enumerate(calls)
    ]

    return SessionCacheAnalysis(
        harness=first.harness,
        source_session_id=first.source_session_id,
        first_created_ms=min(event.created_ms for event in ordered_events),
        last_created_ms=max(event.created_ms for event in ordered_events),
        call_count=len(calls),
        tokens=total_tokens,
        cache_read_tokens=total_tokens.cache_read,
        cache_write_tokens=total_tokens.cache_write,
        prompt_like_tokens=prompt_like_total,
        cache_reuse_ratio=float(total_tokens.cache_read)
        / max(total_tokens.input + total_tokens.cache_read, 1),
        cache_presence_ratio=float(total_tokens.cache_read) / max(prompt_like_total, 1),
        source_cost_usd=total_source,
        actual_cost_usd=total_actual,
        virtual_cost_usd=total_virtual,
        virtual_uncached_cost_usd=total_virtual_uncached,
        virtual_cache_savings_usd=total_virtual_savings,
        estimated_source_cache_loss_usd=estimated_source_cache_loss,
        unpriced_count=total_unpriced,
        calls=tuple(calls),
        clusters=tuple(clusters),
        warnings=tuple(warnings),
    )


def _build_clusters(
    calls: list[CacheCallAnalysis],
    *,
    flags_by_index: list[set[str]],
    tolerance: float,
) -> tuple[list[CacheClusterAnalysis], Decimal]:
    grouped: dict[tuple[str, str, str | None], list[int]] = {}
    for index, call in enumerate(calls):
        grouped.setdefault(
            (call.provider_id, call.model_id, call.thinking_level),
            [],
        ).append(index)

    clusters: list[CacheClusterAnalysis] = []
    estimated_loss_total = Decimal(0)

    for key in sorted(grouped):
        indices = sorted(grouped[key], key=lambda idx: calls[idx].prompt_like_tokens)
        index_buckets: list[list[int]] = []
        current_bucket: list[int] = []
        anchor = 0

        for idx in indices:
            prompt = calls[idx].prompt_like_tokens
            if not current_bucket:
                current_bucket = [idx]
                anchor = prompt
                continue
            if _within_tolerance(anchor, prompt, tolerance=tolerance):
                current_bucket.append(idx)
                continue
            index_buckets.append(current_bucket)
            current_bucket = [idx]
            anchor = prompt

        if current_bucket:
            index_buckets.append(current_bucket)

        for bucket in index_buckets:
            bucket_calls = [calls[idx] for idx in bucket]
            hit_calls = [
                call for call in bucket_calls if call.cache_status in _HIT_STATUSES
            ]
            miss_calls = [
                call for call in bucket_calls if call.cache_status in _MISS_STATUSES
            ]
            hit_median = (
                _decimal_median([call.source_cost_usd for call in hit_calls])
                if hit_calls
                else None
            )
            miss_median = (
                _decimal_median([call.source_cost_usd for call in miss_calls])
                if miss_calls
                else None
            )
            estimated_loss = Decimal(0)
            if hit_median is not None:
                for call in miss_calls:
                    estimated_loss += max(Decimal(0), call.source_cost_usd - hit_median)
            estimated_loss_total += estimated_loss

            if hit_median is not None:
                for idx in bucket:
                    call = calls[idx]
                    if call.cache_status == "miss":
                        flags_by_index[idx].add("suspicious_miss")
                    if call.cache_status in _MISS_STATUSES and call.source_cost_usd > (
                        hit_median * Decimal(2)
                    ):
                        flags_by_index[idx].add("cost_outlier")

            clusters.append(
                CacheClusterAnalysis(
                    provider_id=key[0],
                    model_id=key[1],
                    thinking_level=key[2],
                    prompt_like_min=min(
                        call.prompt_like_tokens for call in bucket_calls
                    ),
                    prompt_like_max=max(
                        call.prompt_like_tokens for call in bucket_calls
                    ),
                    call_count=len(bucket_calls),
                    hit_count=len(hit_calls),
                    miss_count=len(miss_calls),
                    median_hit_source_cost_usd=hit_median,
                    median_miss_source_cost_usd=miss_median,
                    estimated_source_loss_usd=estimated_loss,
                    call_ordinals=tuple(call.ordinal for call in bucket_calls),
                )
            )
    return clusters, estimated_loss_total


def _annotate_sequence_flags(
    calls: list[CacheCallAnalysis],
    *,
    flags_by_index: list[set[str]],
    tolerance: float,
) -> None:
    for index in range(1, len(calls)):
        previous = calls[index - 1]
        current = calls[index]
        if (
            previous.provider_id != current.provider_id
            or previous.model_id != current.model_id
            or previous.thinking_level != current.thinking_level
        ):
            continue
        if not _within_tolerance(
            previous.prompt_like_tokens,
            current.prompt_like_tokens,
            tolerance=tolerance,
        ):
            continue
        if (
            previous.cache_status in _HIT_STATUSES
            and current.cache_status in _MISS_STATUSES
        ):
            flags_by_index[index].add("cache_cliff")
        if (
            previous.cache_status in _MISS_STATUSES
            and current.cache_status in _HIT_STATUSES
        ):
            flags_by_index[index].add("cache_recovered")


def _within_tolerance(
    left: int,
    right: int,
    *,
    tolerance: float,
) -> bool:
    if left == right:
        return True
    if left <= 0 or right <= 0:
        return False
    baseline = max(left, right)
    return abs(left - right) <= int(baseline * tolerance)


def _decimal_median(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    return median(sorted(values))


__all__ = [
    "CacheCallAnalysis",
    "CacheClusterAnalysis",
    "SessionCacheAnalysis",
    "analyze_usage_events",
    "cache_presence_ratio",
    "cache_reuse_ratio",
    "classify_cache_status",
    "prompt_like_tokens",
]
