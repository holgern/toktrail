from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from time import time

from toktrail import db as db_module
from toktrail.api._common import _open_state_db
from toktrail.api.models import (
    CostTotals,
    ModelSummaryRow,
    StatuslineBurn,
    StatuslineCache,
    StatuslineContext,
    StatuslineQuota,
    StatuslineReport,
    SubscriptionUsageRow,
    TokenBreakdown,
    UsageSessionRow,
)
from toktrail.api.reports import (
    subscription_usage_report,
    usage_report,
    usage_sessions_report,
)
from toktrail.api.sources import list_source_sessions
from toktrail.config import load_toktrail_config, normalize_identity
from toktrail.models import RunScope

DEFAULT_STATUSLINE_MAX_WIDTH = 120
DEFAULT_STATUSLINE_STALE_AFTER_SECONDS = 60
DEFAULT_STATUSLINE_ELEMENTS = (
    "harness",
    "model",
    "tokens",
    "cached",
    "cost",
    "quota",
    "burn",
    "unpriced",
)
_VALID_COST_BASES = {"source", "actual", "virtual"}


@dataclass(frozen=True)
class StatuslineRequest:
    harness: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    source_session_id: str | None = None
    session_mode: str = "auto"
    basis: str = "virtual"
    max_width: int = DEFAULT_STATUSLINE_MAX_WIDTH
    stale_after_seconds: int = DEFAULT_STATUSLINE_STALE_AFTER_SECONDS
    active_session_window_minutes: int = 30
    elements: tuple[str, ...] = DEFAULT_STATUSLINE_ELEMENTS
    stdin_payload: Mapping[str, object] | None = None


def statusline_cache_dir() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir).expanduser() / "toktrail" / "statusline"
    return Path(tempfile.gettempdir()) / f"toktrail-{os.getuid()}" / "statusline"


def statusline_cache_key(
    db_path: Path,
    *,
    request: StatuslineRequest,
    json_output: bool,
) -> str:
    payload_hash = "-"
    if request.stdin_payload is not None:
        payload_hash = hashlib.sha256(
            json.dumps(request.stdin_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
    digest = hashlib.sha256(
        "|".join(
            [
                str(db_path),
                request.harness or "-",
                request.provider_id or "-",
                request.model_id or "-",
                request.source_session_id or "-",
                request.session_mode,
                request.basis,
                str(request.max_width),
                str(request.stale_after_seconds),
                str(request.active_session_window_minutes),
                ",".join(request.elements),
                "json" if json_output else "human",
                payload_hash,
            ]
        ).encode("utf-8")
    ).hexdigest()
    return digest


def load_statusline_output_cache(
    *,
    cache_dir: Path,
    cache_key: str,
    state_db_path: Path,
    config_path: Path | None,
    source_path: Path | None,
    max_age_seconds: int,
) -> StatuslineReport | None:
    cache_file = cache_dir / f"{cache_key}.json"
    if not cache_file.exists():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    created_ms = payload.get("created_ms")
    if not isinstance(created_ms, int):
        return None
    if (
        max_age_seconds >= 0
        and int(time() * 1000) - created_ms > max_age_seconds * 1000
    ):
        return None
    if payload.get("state_db_mtime_ns") != _path_mtime_ns(state_db_path):
        return None
    if payload.get("config_mtime_ns") != _path_mtime_ns(config_path):
        return None
    if payload.get("source_mtime_ns") != _path_mtime_ns(source_path):
        return None
    report_payload = payload.get("report")
    if not isinstance(report_payload, dict):
        return None
    report = statusline_report_from_dict(report_payload)
    return _with_output_cache(report, "hit")


def write_statusline_output_cache(
    *,
    cache_dir: Path,
    cache_key: str,
    report: StatuslineReport,
    state_db_path: Path,
    config_path: Path | None,
    source_path: Path | None,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cache_key}.json"
    temp_file = cache_file.with_suffix(".tmp")
    payload = {
        "created_ms": int(time() * 1000),
        "state_db_mtime_ns": _path_mtime_ns(state_db_path),
        "config_mtime_ns": _path_mtime_ns(config_path),
        "source_mtime_ns": _path_mtime_ns(source_path),
        "report": _with_output_cache(report, "miss").as_dict(),
    }
    temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_file.replace(cache_file)


def load_statusline_cache_metadata(
    *,
    cache_dir: Path,
    cache_key: str,
) -> dict[str, object] | None:
    cache_file = cache_dir / f"{cache_key}.json"
    if not cache_file.exists():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def statusline_report_from_dict(payload: Mapping[str, object]) -> StatuslineReport:
    report_cache = payload.get("cache")
    report_quota = payload.get("quota")
    report_burn = payload.get("burn")
    report_context = payload.get("context")
    return StatuslineReport(
        line=_mapping_string(payload, "line") or "toktrail",
        generated_at_ms=_mapping_int(payload, "generated_at_ms") or 0,
        harness=_mapping_string(payload, "harness"),
        source_session_id=_mapping_string(payload, "source_session_id"),
        source_path=_mapping_path(payload, "source_path"),
        provider_id=_mapping_string(payload, "provider_id"),
        model_id=_mapping_string(payload, "model_id"),
        agent=_mapping_string(payload, "agent"),
        basis=_mapping_string(payload, "basis") or "virtual",
        message_count=_mapping_int(payload, "message_count") or 0,
        tokens=_token_breakdown_from_dict(payload.get("tokens")),
        costs=_cost_totals_from_dict(payload.get("costs")),
        quota=(
            _statusline_quota_from_dict(report_quota)
            if isinstance(report_quota, Mapping)
            else None
        ),
        burn=(
            _statusline_burn_from_dict(report_burn)
            if isinstance(report_burn, Mapping)
            else None
        ),
        context=(
            _statusline_context_from_dict(report_context)
            if isinstance(report_context, Mapping)
            else None
        ),
        cache=(
            _statusline_cache_from_dict(report_cache)
            if isinstance(report_cache, Mapping)
            else None
        ),
        stale_seconds=_mapping_int(payload, "stale_seconds"),
        area_path=_mapping_string(payload, "area_path"),
    )


def build_statusline_report(
    db_path: Path | None,
    *,
    config_path: Path | None,
    request: StatuslineRequest,
    now_ms: int | None = None,
) -> StatuslineReport:
    if request.basis not in _VALID_COST_BASES:
        msg = "basis must be one of: source, actual, virtual."
        raise ValueError(msg)
    if request.session_mode not in {"auto", "latest", "none"}:
        msg = "session_mode must be one of: auto, latest, none."
        raise ValueError(msg)

    generated_at_ms = int(time() * 1000) if now_ms is None else now_ms
    payload = request.stdin_payload
    active_scope = _active_run_scope(db_path)
    scope_harness = (
        _scope_singleton(active_scope.harnesses)
        if active_scope is not None and request.session_mode == "auto"
        else None
    )
    scope_source_session_id = (
        _scope_singleton(active_scope.source_session_ids)
        if active_scope is not None and request.session_mode == "auto"
        else None
    )

    harness = _coalesce(
        request.harness,
        _payload_string(payload, "harness"),
        scope_harness,
    )
    source_session_id = _coalesce(
        request.source_session_id,
        _payload_string(payload, "source_session_id"),
        scope_source_session_id,
    )
    provider_id = _coalesce(
        request.provider_id,
        _payload_nested_string(payload, "model", "provider_id"),
    )
    model_id = _coalesce(
        request.model_id,
        _payload_nested_string(payload, "model", "model_id"),
    )

    selected_session = _select_session_row(
        db_path,
        config_path=config_path,
        harness=harness,
        source_session_id=source_session_id,
        provider_id=provider_id,
        model_id=model_id,
        session_mode=request.session_mode,
    )
    if (
        selected_session is not None
        and source_session_id == scope_source_session_id
        and request.source_session_id is None
        and _payload_string(payload, "source_session_id") is None
        and generated_at_ms - selected_session.last_ms
        > request.active_session_window_minutes * 60 * 1000
    ):
        source_session_id = None
        selected_session = _select_session_row(
            db_path,
            config_path=config_path,
            harness=harness,
            source_session_id=None,
            provider_id=provider_id,
            model_id=model_id,
            session_mode="latest",
        )
    fallback_report = None
    if selected_session is None:
        fallback_report = usage_report(
            db_path,
            period="today",
            harness=harness,
            provider_id=provider_id,
            model_id=model_id,
            config_path=config_path,
        )

    selected_model = _select_model_row(selected_session)
    provider_id = _coalesce(
        provider_id,
        selected_model.provider_id if selected_model is not None else None,
        (
            _first_or_none(selected_session.providers)
            if selected_session is not None
            else None
        ),
    )
    model_id = _coalesce(
        model_id,
        selected_model.model_id if selected_model is not None else None,
        (
            _first_or_none(selected_session.models)
            if selected_session is not None
            else None
        ),
    )
    harness = _coalesce(
        harness,
        selected_session.harness if selected_session is not None else None,
    )
    source_session_id = _coalesce(
        source_session_id,
        selected_session.source_session_id if selected_session is not None else None,
    )

    if selected_session is not None:
        tokens = selected_session.tokens
        costs = selected_session.costs
        message_count = selected_session.message_count
        stale_seconds = max(0, (generated_at_ms - selected_session.last_ms) // 1000)
    elif fallback_report is not None:
        tokens = fallback_report.totals.tokens
        costs = fallback_report.totals.costs
        message_count = fallback_report.totals.message_count
        stale_seconds = None
    else:
        tokens = TokenBreakdown()
        costs = CostTotals()
        message_count = 0
        stale_seconds = None

    quota = _select_primary_quota(
        db_path,
        config_path=config_path,
        providers=tuple(
            provider for provider in ((provider_id,) if provider_id is not None else ())
        )
        or (selected_session.providers if selected_session is not None else ()),
        now_ms=generated_at_ms,
    )
    burn = _build_burn(quota=quota, now_ms=generated_at_ms)
    context = _build_context(
        payload=payload,
        tokens=tokens,
        provider_id=provider_id,
        model_id=model_id,
        config_path=config_path,
    )
    cache = _build_cache(tokens=tokens)
    source_path = _resolve_source_path(
        harness=harness,
        source_session_id=source_session_id,
        config_path=config_path,
    )
    active_area_path = _active_area_path(db_path)

    report = StatuslineReport(
        line="",
        generated_at_ms=generated_at_ms,
        harness=harness,
        source_session_id=source_session_id,
        source_path=source_path,
        provider_id=provider_id,
        model_id=model_id,
        agent=None,
        basis=request.basis,
        message_count=message_count,
        tokens=tokens,
        costs=costs,
        quota=quota,
        burn=burn,
        context=context,
        cache=cache,
        stale_seconds=stale_seconds,
        area_path=active_area_path
        or (selected_session.area_path if selected_session is not None else None),
    )
    return StatuslineReport(
        line=render_statusline(
            report,
            max_width=request.max_width,
            stale_after_seconds=request.stale_after_seconds,
            elements=request.elements,
        ),
        generated_at_ms=report.generated_at_ms,
        harness=report.harness,
        source_session_id=report.source_session_id,
        source_path=report.source_path,
        provider_id=report.provider_id,
        model_id=report.model_id,
        agent=report.agent,
        basis=report.basis,
        message_count=report.message_count,
        tokens=report.tokens,
        costs=report.costs,
        quota=report.quota,
        burn=report.burn,
        context=report.context,
        cache=report.cache,
        stale_seconds=report.stale_seconds,
        area_path=report.area_path,
    )


def render_statusline(
    report: StatuslineReport,
    *,
    max_width: int = DEFAULT_STATUSLINE_MAX_WIDTH,
    stale_after_seconds: int = DEFAULT_STATUSLINE_STALE_AFTER_SECONDS,
    elements: tuple[str, ...] = DEFAULT_STATUSLINE_ELEMENTS,
) -> str:
    if report.message_count == 0 and report.tokens.accounting_total == 0:
        return "toktrail: no usage sources"

    parts: list[str] = []
    for element in elements:
        part = _render_element(
            report,
            element=element,
            stale_after_seconds=stale_after_seconds,
        )
        if part:
            parts.append(part)

    line = " · ".join(parts) if parts else "toktrail"
    if len(line) <= max_width:
        return line
    if max_width <= 1:
        return line[:max_width]
    return f"{line[: max_width - 1]}…"


def _active_run_scope(db_path: Path | None) -> RunScope | None:
    conn, _ = _open_state_db(db_path)
    try:
        active_id = db_module.get_active_tracking_session(conn)
        if active_id is None:
            return None
        run = db_module.get_tracking_session(conn, active_id)
        return None if run is None else run.scope
    finally:
        conn.close()


def _active_area_path(db_path: Path | None) -> str | None:
    conn, _ = _open_state_db(db_path)
    try:
        area = db_module.get_active_area(conn)
    finally:
        conn.close()
    return area.path if area is not None else None


def _select_session_row(
    db_path: Path | None,
    *,
    config_path: Path | None,
    harness: str | None,
    source_session_id: str | None,
    provider_id: str | None,
    model_id: str | None,
    session_mode: str,
) -> UsageSessionRow | None:
    if session_mode == "none":
        return None
    report = usage_sessions_report(
        db_path,
        harness=harness,
        source_session_id=source_session_id,
        provider_id=provider_id,
        model_id=model_id,
        limit=1,
        order="desc",
        breakdown=True,
        config_path=config_path,
    )
    return report.sessions[0] if report.sessions else None


def _select_model_row(row: UsageSessionRow | None) -> ModelSummaryRow | None:
    if row is None or not row.by_model:
        return None
    return max(
        row.by_model,
        key=lambda item: (
            item.tokens.total,
            item.message_count,
            item.provider_id,
            item.model_id,
        ),
    )


def _select_primary_quota(
    db_path: Path | None,
    *,
    config_path: Path | None,
    providers: tuple[str, ...],
    now_ms: int,
) -> StatuslineQuota | None:
    report = subscription_usage_report(
        db_path,
        provider_id=providers[0] if len(providers) == 1 else None,
        now_ms=now_ms,
        config_path=config_path,
    )

    selected_row: SubscriptionUsageRow | None = None
    selected_period = None
    selected_key: tuple[int, float, float, float]
    selected_key = (-1, -1.0, float("-inf"), float("-inf"))
    provider_filter = set(providers)
    for row in report.subscriptions:
        if provider_filter and not provider_filter.intersection(row.usage_provider_ids):
            continue
        for period in row.periods:
            if period.status not in {"active", "expired_waiting_for_next_use"}:
                continue
            if period.percent_used is None:
                continue
            until_score = (
                -float(max(0, period.until_ms - now_ms))
                if period.until_ms is not None
                else float("-inf")
            )
            key = (
                1 if period.over_limit_usd > 0 else 0,
                float(period.percent_used),
                -float(period.remaining_usd),
                until_score,
            )
            if key <= selected_key:
                continue
            selected_key = key
            selected_row = row
            selected_period = period
    if selected_row is None or selected_period is None:
        return None
    reset_in_seconds = (
        max(0, (selected_period.until_ms - now_ms) // 1000)
        if selected_period.until_ms is not None
        else None
    )
    return StatuslineQuota(
        subscription_id=selected_row.subscription_id,
        display_name=selected_row.display_name,
        period=selected_period.period,
        status=selected_period.status,
        reset_at=selected_period.reset_at,
        percent_used=selected_period.percent_used,
        remaining_usd=selected_period.remaining_usd,
        over_limit_usd=selected_period.over_limit_usd,
        reset_in_seconds=reset_in_seconds,
        since_ms=selected_period.since_ms,
        until_ms=selected_period.until_ms,
        used_usd=selected_period.used_usd,
        limit_usd=selected_period.limit_usd,
    )


def _build_burn(
    *,
    quota: StatuslineQuota | None,
    now_ms: int,
) -> StatuslineBurn | None:
    if quota is None or quota.reset_in_seconds is None:
        return None
    if quota.over_limit_usd > 0:
        return StatuslineBurn(ratio=float("inf"), label="limit")
    if (
        quota.since_ms is None
        or quota.until_ms is None
        or quota.used_usd <= 0
        or quota.remaining_usd <= 0
    ):
        return None
    elapsed_ms = max(0, now_ms - quota.since_ms)
    remaining_ms = max(0, quota.until_ms - now_ms)
    if elapsed_ms <= 0 or remaining_ms <= 0:
        return None
    current_rate = float(quota.used_usd) / (elapsed_ms / 3_600_000)
    safe_rate = float(quota.remaining_usd) / (remaining_ms / 3_600_000)
    if safe_rate <= 0:
        return None
    ratio = current_rate / safe_rate
    label = _format_ratio(ratio)
    if ratio >= 1:
        label = f"{label} {_format_duration(quota.reset_in_seconds)}"
    return StatuslineBurn(ratio=ratio, label=label)


def _build_context(
    *,
    payload: Mapping[str, object] | None,
    tokens: TokenBreakdown,
    provider_id: str | None,
    model_id: str | None,
    config_path: Path | None,
) -> StatuslineContext | None:
    context_payload = _payload_mapping(payload, "context")
    if context_payload is not None:
        percentage = _payload_number(context_payload, "percentage")
        if percentage is not None:
            return StatuslineContext(
                used_tokens=_payload_int(context_payload, "used_tokens"),
                limit_tokens=_payload_int(context_payload, "limit_tokens"),
                percentage=percentage,
            )
        used_tokens = _payload_int(context_payload, "used_tokens")
        limit_tokens = _payload_int(context_payload, "limit_tokens")
        if used_tokens is not None and limit_tokens is not None and limit_tokens != 0:
            return StatuslineContext(
                used_tokens=used_tokens,
                limit_tokens=limit_tokens,
                percentage=(used_tokens / limit_tokens) * 100,
            )
    if tokens.prompt_total > 0:
        limit_tokens = _context_window_limit(
            config_path=config_path,
            provider_id=provider_id,
            model_id=model_id,
        )
        if limit_tokens is not None:
            return StatuslineContext(
                used_tokens=tokens.prompt_total,
                limit_tokens=limit_tokens,
                percentage=(tokens.prompt_total / limit_tokens) * 100,
                approximate=True,
            )
    return None


def _context_window_limit(
    *,
    config_path: Path | None,
    provider_id: str | None,
    model_id: str | None,
) -> int | None:
    if config_path is None or provider_id is None or model_id is None:
        return None
    try:
        config = load_toktrail_config(config_path)
    except ValueError:
        return None
    normalized_provider = normalize_identity(provider_id)
    normalized_model = normalize_identity(model_id)
    for window in config.context_windows:
        if (
            normalize_identity(window.provider) == normalized_provider
            and normalize_identity(window.model) == normalized_model
        ):
            return window.tokens
    return None


def _build_cache(*, tokens: TokenBreakdown) -> StatuslineCache:
    cached_tokens = tokens.cache_read + tokens.cache_write + tokens.cache_output
    cache_reuse_ratio = (
        tokens.cache_read / tokens.prompt_total if tokens.prompt_total > 0 else None
    )
    return StatuslineCache(
        cached_tokens=cached_tokens,
        cache_reuse_ratio=cache_reuse_ratio,
    )


def _resolve_source_path(
    *,
    harness: str | None,
    source_session_id: str | None,
    config_path: Path | None,
) -> Path | None:
    if harness is None or source_session_id is None:
        return None
    sessions = list_source_sessions(
        harness,
        source_session_id=source_session_id,
        limit=1,
        config_path=config_path,
    )
    if not sessions or not sessions[0].source_paths:
        return None
    return Path(sessions[0].source_paths[0])


def _render_element(
    report: StatuslineReport,
    *,
    element: str,
    stale_after_seconds: int,
) -> str | None:
    if element == "harness":
        return report.harness or "toktrail"
    if element == "provider" and report.provider_id is not None:
        return report.provider_id
    if element == "model":
        return report.model_id
    if element == "session" and report.source_session_id is not None:
        return report.source_session_id
    if element == "area":
        return f"area {report.area_path}" if report.area_path else "area -"
    if element == "tokens":
        return f"{_format_compact_int(report.tokens.total)} tok"
    if element == "cached":
        if report.cache is None or report.cache.cached_tokens <= 0:
            return None
        return f"+{_format_compact_int(report.cache.cached_tokens)} cached"
    if element == "reasoning" and report.tokens.reasoning > 0:
        return f"r:{_format_compact_int(report.tokens.reasoning)}"
    if element == "cost":
        return _render_cost(report)
    if element == "quota":
        return _render_quota(report.quota)
    if element == "burn":
        return None if report.burn is None else f"burn {report.burn.label}"
    if element == "context":
        return _render_context(report.context)
    if element == "cache_ratio":
        if report.cache is None or report.cache.cache_reuse_ratio is None:
            return None
        return f"cache {_format_ratio(report.cache.cache_reuse_ratio)}"
    if element == "unpriced" and report.unpriced_count > 0:
        return f"?{report.unpriced_count}"
    if element == "stale":
        if report.stale_seconds is None or report.stale_seconds < stale_after_seconds:
            return None
        return f"stale {_format_duration(report.stale_seconds)}"
    return None


def _render_cost(report: StatuslineReport) -> str:
    value = {
        "source": report.costs.source_cost_usd,
        "actual": report.costs.actual_cost_usd,
        "virtual": report.costs.virtual_cost_usd,
    }[report.basis]
    prefix = {"source": "s", "actual": "a", "virtual": "v"}[report.basis]
    return f"{prefix}${float(value):.2f}"


def _render_quota(quota: StatuslineQuota | None) -> str | None:
    if quota is None:
        return None
    if quota.over_limit_usd > 0:
        return f"{quota.period} over ${float(quota.over_limit_usd):.2f}"
    if quota.percent_used is None:
        return quota.period
    parts = [
        quota.period,
        f"{float(quota.percent_used):.0f}%",
        f"${float(quota.remaining_usd):.2f} left",
    ]
    if quota.reset_in_seconds is not None:
        parts.append(_format_duration(quota.reset_in_seconds))
    return " ".join(parts)


def _render_context(context: StatuslineContext | None) -> str | None:
    if context is None:
        return None
    prefix = "ctx ~" if context.approximate else "ctx "
    return f"{prefix}{context.percentage:.0f}%"


def _format_compact_int(value: int) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _format_ratio(value: float) -> str:
    return f"{round(value * 100):.0f}%"


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" if rem_seconds == 0 else f"{minutes}m{rem_seconds}s"
    hours, rem_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if rem_minutes == 0 else f"{hours}h{rem_minutes}m"
    days, rem_hours = divmod(hours, 24)
    return f"{days}d" if rem_hours == 0 else f"{days}d{rem_hours}h"


def _scope_singleton(values: tuple[str, ...]) -> str | None:
    return values[0] if len(values) == 1 else None


def _coalesce(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _first_or_none(values: tuple[str, ...]) -> str | None:
    return values[0] if values else None


def _payload_string(
    payload: Mapping[str, object] | None,
    key: str,
) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _payload_nested_string(
    payload: Mapping[str, object] | None,
    parent_key: str,
    child_key: str,
) -> str | None:
    parent = _payload_mapping(payload, parent_key)
    if parent is None:
        return None
    return _payload_string(parent, child_key)


def _payload_mapping(
    payload: Mapping[str, object] | None,
    key: str,
) -> Mapping[str, object] | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, Mapping) else None


def _payload_int(
    payload: Mapping[str, object] | None,
    key: str,
) -> int | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _payload_number(
    payload: Mapping[str, object] | None,
    key: str,
) -> float | None:
    if payload is None:
        return None
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _with_output_cache(
    report: StatuslineReport,
    status: str,
) -> StatuslineReport:
    cache = report.cache or StatuslineCache(
        cached_tokens=0,
        cache_reuse_ratio=None,
    )
    return replace(report, cache=replace(cache, output_cache=status))


def _path_mtime_ns(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _mapping_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _mapping_int(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _mapping_path(payload: Mapping[str, object], key: str) -> Path | None:
    value = _mapping_string(payload, key)
    return None if value is None else Path(value)


def _mapping_decimal(payload: Mapping[str, object], key: str) -> Decimal | None:
    value = payload.get(key)
    if isinstance(value, str):
        return Decimal(value)
    return None


def _token_breakdown_from_dict(value: object) -> TokenBreakdown:
    if not isinstance(value, Mapping):
        return TokenBreakdown()
    return TokenBreakdown(
        input=_mapping_int(value, "input") or 0,
        output=_mapping_int(value, "output") or 0,
        reasoning=_mapping_int(value, "reasoning") or 0,
        cache_read=_mapping_int(value, "cache_read") or 0,
        cache_write=_mapping_int(value, "cache_write") or 0,
        cache_output=_mapping_int(value, "cache_output") or 0,
    )


def _cost_totals_from_dict(value: object) -> CostTotals:
    if not isinstance(value, Mapping):
        return CostTotals()
    return CostTotals(
        source_cost_usd=_mapping_decimal(value, "source_cost_usd") or Decimal(0),
        actual_cost_usd=_mapping_decimal(value, "actual_cost_usd") or Decimal(0),
        virtual_cost_usd=_mapping_decimal(value, "virtual_cost_usd") or Decimal(0),
        unpriced_count=_mapping_int(value, "unpriced_count") or 0,
    )


def _statusline_quota_from_dict(value: Mapping[str, object]) -> StatuslineQuota:
    return StatuslineQuota(
        subscription_id=_mapping_string(value, "subscription_id") or "",
        display_name=_mapping_string(value, "display_name"),
        period=_mapping_string(value, "period") or "",
        status=_mapping_string(value, "status") or "",
        reset_at=_mapping_string(value, "reset_at") or "",
        percent_used=_mapping_decimal(value, "percent_used"),
        remaining_usd=_mapping_decimal(value, "remaining_usd") or Decimal(0),
        over_limit_usd=_mapping_decimal(value, "over_limit_usd") or Decimal(0),
        reset_in_seconds=_mapping_int(value, "reset_in_seconds"),
        since_ms=_mapping_int(value, "since_ms"),
        until_ms=_mapping_int(value, "until_ms"),
        used_usd=_mapping_decimal(value, "used_usd") or Decimal(0),
        limit_usd=_mapping_decimal(value, "limit_usd") or Decimal(0),
    )


def _statusline_burn_from_dict(value: Mapping[str, object]) -> StatuslineBurn:
    ratio = value.get("ratio")
    return StatuslineBurn(
        ratio=float(ratio) if isinstance(ratio, (int, float)) else 0.0,
        label=_mapping_string(value, "label") or "-",
    )


def _statusline_context_from_dict(value: Mapping[str, object]) -> StatuslineContext:
    percentage = value.get("percentage")
    return StatuslineContext(
        used_tokens=_mapping_int(value, "used_tokens"),
        limit_tokens=_mapping_int(value, "limit_tokens"),
        percentage=float(percentage) if isinstance(percentage, (int, float)) else 0.0,
        approximate=bool(value.get("approximate", False)),
    )


def _statusline_cache_from_dict(value: Mapping[str, object]) -> StatuslineCache:
    ratio = value.get("cache_reuse_ratio")
    return StatuslineCache(
        cached_tokens=_mapping_int(value, "cached_tokens") or 0,
        cache_reuse_ratio=(float(ratio) if isinstance(ratio, (int, float)) else None),
        output_cache=_mapping_string(value, "output_cache"),
    )
