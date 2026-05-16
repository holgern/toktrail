from __future__ import annotations

from typing import TYPE_CHECKING

from toktrail.config import normalize_identity
from toktrail.reporting import ModelSummaryRow, UnconfiguredModelRow

from .types import PriceDisplayFilter, ReportDisplayFilter

if TYPE_CHECKING:
    from toktrail.reporting import UsageSeriesBucket

_VALID_REPORT_PRICE_STATES = {"all", "priced", "unpriced"}
_VALID_REPORT_SORTS = {
    "actual",
    "virtual",
    "savings",
    "tokens",
    "messages",
    "provider",
    "model",
    "unpriced",
}
_VALID_PRICE_TABLES = {"virtual", "actual", "all"}
_VALID_PRICE_SORTS = {
    "provider",
    "model",
    "input",
    "cached",
    "cache-write",
    "output",
    "reasoning",
    "category",
    "release",
}


def _normalize_report_display_filter(
    *,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
    sort: str,
    limit: int | None,
) -> ReportDisplayFilter:
    normalized_price_state = price_state.strip().lower()
    if normalized_price_state not in _VALID_REPORT_PRICE_STATES:
        raise ValueError("Unsupported --price-state. Use all, priced, or unpriced.")
    normalized_sort = sort.strip().lower()
    if normalized_sort not in _VALID_REPORT_SORTS:
        raise ValueError(
            "Unsupported --sort. Use actual, virtual, savings, tokens, messages, "
            "provider, model, or unpriced."
        )
    if min_messages is not None and min_messages < 0:
        raise ValueError("--min-messages must be non-negative.")
    if min_tokens is not None and min_tokens < 0:
        raise ValueError("--min-tokens must be non-negative.")
    if limit is not None and limit < 0:
        raise ValueError("--limit must be non-negative.")
    return ReportDisplayFilter(
        price_state=normalized_price_state,
        min_messages=min_messages,
        min_tokens=min_tokens,
        sort=normalized_sort,
        limit=limit,
    )


def _normalize_price_display_filter(
    *,
    table: str,
    provider: str | None,
    model: str | None,
    query: str | None,
    category: str | None,
    release_status: str | None,
    sort: str,
    limit: int | None,
) -> PriceDisplayFilter:
    normalized_table = table.strip().lower()
    if normalized_table not in _VALID_PRICE_TABLES:
        raise ValueError("Unsupported --table. Use virtual, actual, or all.")
    normalized_sort = sort.strip().lower()
    if normalized_sort not in _VALID_PRICE_SORTS:
        raise ValueError(
            "Unsupported --sort. Use provider, model, input, cached, cache-write, "
            "output, reasoning, category, or release."
        )
    if limit is not None and limit < 0:
        raise ValueError("--limit must be non-negative.")
    return PriceDisplayFilter(
        table=normalized_table,
        provider=provider,
        model=model,
        query=query,
        category=category,
        release_status=release_status,
        sort=normalized_sort,
        limit=limit,
    )


def _filter_model_rows(
    rows: list[ModelSummaryRow],
    *,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
    sort: str,
    limit: int | None,
) -> list[ModelSummaryRow]:
    filtered = [
        row
        for row in rows
        if _model_row_matches_price_state(row, price_state)
        and (min_messages is None or row.message_count >= min_messages)
        and (min_tokens is None or row.total_tokens >= min_tokens)
    ]
    sorted_rows = _sort_model_rows(filtered, sort=sort)
    if limit is not None:
        return sorted_rows[:limit]
    return sorted_rows


def _model_row_matches_price_state(row: ModelSummaryRow, price_state: str) -> bool:
    if price_state == "all":
        return True
    if price_state == "priced":
        return row.unpriced_count == 0
    return row.unpriced_count > 0


def _sort_model_rows(
    rows: list[ModelSummaryRow],
    *,
    sort: str,
) -> list[ModelSummaryRow]:
    if sort == "actual":
        return sorted(
            rows,
            key=lambda row: (
                row.actual_cost_usd,
                row.total_tokens,
                row.message_count,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "virtual":
        return sorted(
            rows,
            key=lambda row: (
                row.virtual_cost_usd,
                row.total_tokens,
                row.message_count,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "savings":
        return sorted(
            rows,
            key=lambda row: (
                row.savings_usd,
                row.total_tokens,
                row.message_count,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "tokens":
        return sorted(
            rows,
            key=lambda row: (
                row.total_tokens,
                row.message_count,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "messages":
        return sorted(
            rows,
            key=lambda row: (
                row.message_count,
                row.total_tokens,
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
            ),
            reverse=True,
        )
    if sort == "provider":
        return sorted(
            rows,
            key=lambda row: (
                row.provider_id,
                row.model_id,
                row.thinking_level or "",
                -row.total_tokens,
                -row.message_count,
            ),
        )
    if sort == "model":
        return sorted(
            rows,
            key=lambda row: (
                row.model_id,
                row.provider_id,
                row.thinking_level or "",
                -row.total_tokens,
                -row.message_count,
            ),
        )
    return sorted(
        rows,
        key=lambda row: (
            row.unpriced_count == 0,
            -row.total_tokens,
            -row.message_count,
            row.provider_id,
            row.model_id,
            row.thinking_level or "",
        ),
    )


def _filter_unconfigured_models(
    rows: list[UnconfiguredModelRow],
    *,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
) -> list[UnconfiguredModelRow]:
    if price_state == "priced":
        return []
    return [
        row
        for row in rows
        if (min_messages is None or row.message_count >= min_messages)
        and (min_tokens is None or row.total_tokens >= min_tokens)
    ]


def _filter_series_buckets(
    buckets: tuple[UsageSeriesBucket, ...],
    *,
    price_state: str,
    min_messages: int | None,
    min_tokens: int | None,
) -> list[UsageSeriesBucket]:
    return [
        b
        for b in buckets
        if _series_bucket_matches_price_state(b, price_state)
        and (min_messages is None or b.message_count >= min_messages)
        and (min_tokens is None or b.tokens.total >= min_tokens)
    ]


def _series_bucket_matches_price_state(
    bucket: UsageSeriesBucket, price_state: str
) -> bool:
    if price_state == "all":
        return True
    if price_state == "priced":
        return bucket.costs.unpriced_count == 0
    return bucket.costs.unpriced_count > 0


def _sort_series_buckets(
    buckets: list[UsageSeriesBucket],
    *,
    sort: str,
) -> list[UsageSeriesBucket]:
    if sort == "actual":
        return sorted(
            buckets,
            key=lambda b: (
                b.costs.actual_cost_usd,
                b.tokens.total,
                b.message_count,
                b.key,
            ),
            reverse=True,
        )
    if sort == "virtual":
        return sorted(
            buckets,
            key=lambda b: (
                b.costs.virtual_cost_usd,
                b.tokens.total,
                b.message_count,
                b.key,
            ),
            reverse=True,
        )
    if sort == "savings":
        return sorted(
            buckets,
            key=lambda b: (
                b.costs.savings_usd,
                b.tokens.total,
                b.message_count,
                b.key,
            ),
            reverse=True,
        )
    if sort == "tokens":
        return sorted(
            buckets,
            key=lambda b: (
                b.tokens.total,
                b.message_count,
                b.key,
            ),
            reverse=True,
        )
    if sort == "messages":
        return sorted(
            buckets,
            key=lambda b: (
                b.message_count,
                b.tokens.total,
                b.key,
            ),
            reverse=True,
        )
    return sorted(buckets, key=lambda b: b.key)


def _aliases_from_row(row: dict[str, object]) -> list[str]:
    aliases = row.get("aliases")
    if not isinstance(aliases, list):
        return []
    return [str(alias) for alias in aliases]


def _as_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"Expected float-compatible value, got {value!r}"
        raise TypeError(msg)
    return float(value)


def _required_row_float(row: dict[str, object], key: str) -> float:
    value = _as_float_or_none(row.get(key))
    if value is None:
        msg = f"Missing numeric price field: {key}"
        raise TypeError(msg)
    return value


def _filter_price_rows(
    rows: list[dict[str, object]],
    filters: PriceDisplayFilter,
) -> list[dict[str, object]]:
    filtered = rows
    if filters.provider is not None:
        normalized_provider = normalize_identity(filters.provider)
        filtered = [
            row
            for row in filtered
            if normalize_identity(str(row["provider"])) == normalized_provider
        ]
    if filters.model is not None:
        normalized_model = normalize_identity(filters.model)
        filtered = [
            row
            for row in filtered
            if normalize_identity(str(row["model"])) == normalized_model
            or normalized_model
            in {normalize_identity(alias) for alias in _aliases_from_row(row)}
        ]
    if filters.category is not None:
        normalized_category = normalize_identity(filters.category)
        filtered = [
            row
            for row in filtered
            if normalize_identity(str(row.get("category") or "")) == normalized_category
        ]
    if filters.release_status is not None:
        normalized_release = filters.release_status.strip().lower()
        filtered = [
            row
            for row in filtered
            if str(row.get("release_status") or "").strip().lower()
            == normalized_release
        ]
    if filters.query is not None:
        needle = filters.query.strip().lower()
        filtered = [
            row
            for row in filtered
            if needle
            in " ".join(
                [
                    str(row["provider"]),
                    str(row["model"]),
                    *_aliases_from_row(row),
                    str(row.get("category") or ""),
                    str(row.get("release_status") or ""),
                ]
            ).lower()
        ]

    sorted_rows = _sort_price_rows(filtered, sort=filters.sort)
    if filters.limit is not None:
        return sorted_rows[: filters.limit]
    return sorted_rows


def _sort_price_rows(
    rows: list[dict[str, object]],
    *,
    sort: str,
) -> list[dict[str, object]]:
    if sort == "provider":
        return sorted(
            rows,
            key=lambda row: (
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
        )
    if sort == "model":
        return sorted(
            rows,
            key=lambda row: (
                str(row["model"]),
                str(row["provider"]),
                str(row["table"]),
            ),
        )
    if sort == "input":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "input_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "cached":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "effective_cached_input_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "cache-write":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "effective_cache_write_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "output":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "output_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "reasoning":
        return sorted(
            rows,
            key=lambda row: (
                _required_row_float(row, "effective_reasoning_usd_per_1m"),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
            reverse=True,
        )
    if sort == "category":
        return sorted(
            rows,
            key=lambda row: (
                str(row.get("category") or ""),
                str(row["provider"]),
                str(row["model"]),
                str(row["table"]),
            ),
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("release_status") or ""),
            str(row["provider"]),
            str(row["model"]),
            str(row["table"]),
        ),
    )
