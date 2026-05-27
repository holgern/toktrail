"""Temporal comparison and anomaly detection for insights.

Resolves the previous comparison period, computes deltas between
current and previous aggregates, and flags outlier sessions.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from toktrail.insights.models import (
    InsightAggregate,
    InsightAnomaly,
    InsightDelta,
    InsightSessionMeta,
)

# --- Delta computation ---


def _percent_change(
    current: Decimal | int | float,
    previous: Decimal | int | float | None,
) -> tuple[str, Literal["up", "down", "flat", "new", "removed"]]:
    """Compute a human-readable percentage change and direction."""
    if previous is None or previous == 0:
        if current == 0:
            return "0%", "flat"
        return "new", "new"
    if current == 0:
        return "removed", "removed"
    ratio = (Decimal(str(current)) - Decimal(str(previous))) / Decimal(str(previous))
    pct = float(ratio * 100)
    if abs(pct) < 0.5:
        return f"{pct:+.0f}%", "flat"
    if pct > 0:
        return f"+{pct:.0f}%", "up"
    return f"{pct:.0f}%", "down"


def compute_deltas(
    current: InsightAggregate,
    previous: InsightAggregate | None,
) -> tuple[InsightDelta, ...]:
    """Compute key metric deltas between current and previous periods."""
    if previous is None:
        return ()

    deltas: list[InsightDelta] = []

    metrics: list[tuple[str, Decimal, Decimal]] = [
        ("virtual_cost", current.virtual_cost, previous.virtual_cost),
        ("actual_cost", current.actual_cost, previous.actual_cost),
        (
            "total_tokens",
            Decimal(current.total_tokens),
            Decimal(previous.total_tokens),
        ),
        (
            "input_tokens",
            Decimal(current.input_tokens),
            Decimal(previous.input_tokens),
        ),
        (
            "output_tokens",
            Decimal(current.output_tokens),
            Decimal(previous.output_tokens),
        ),
        (
            "tool_failures",
            Decimal(current.tool_failure_count),
            Decimal(previous.tool_failure_count),
        ),
        (
            "unpriced_models",
            Decimal(current.unpriced_count),
            Decimal(previous.unpriced_count),
        ),
    ]

    # Cache ratio as percentage points difference
    curr_cache_ratio = current.cache_read_ratio
    prev_cache_ratio = previous.cache_read_ratio
    if prev_cache_ratio != 0 or curr_cache_ratio != 0:
        pp_diff = (curr_cache_ratio - prev_cache_ratio) * 100
        if abs(pp_diff) < 0.5:
            change_str = f"{pp_diff:+.0f}pp"
            direction: Literal["up", "down", "flat", "new", "removed"] = "flat"
        elif pp_diff > 0:
            change_str = f"+{pp_diff:.0f}pp"
            direction = "up"
        else:
            change_str = f"{pp_diff:.0f}pp"
            direction = "down"
        deltas.append(
            InsightDelta(
                metric="cache_read_ratio",
                current=round(curr_cache_ratio * 100, 1),
                previous=round(prev_cache_ratio * 100, 1),
                change=change_str,
                direction=direction,
            )
        )

    for name, curr_val, prev_val in metrics:
        change_str, direction = _percent_change(curr_val, prev_val)
        deltas.append(
            InsightDelta(
                metric=name,
                current=curr_val,
                previous=prev_val,
                change=change_str,
                direction=direction,
            )
        )

    # Session count
    change_str, direction = _percent_change(
        Decimal(current.session_count),
        Decimal(previous.session_count),
    )
    deltas.append(
        InsightDelta(
            metric="sessions",
            current=current.session_count,
            previous=previous.session_count,
            change=change_str,
            direction=direction,
        )
    )

    return tuple(deltas)


# --- Anomaly detection ---

# Thresholds — hardcoded constants acceptable per handoff spec.
_COST_OUTLIER_MULTIPLIER = 3.0
_FAILURE_OUTLIER_MULTIPLIER = 3.0
_FAILURE_MINIMUM = 3
_CACHE_INEFFICIENCY_THRESHOLD = 0.10  # below this cache_read_ratio
_HIGH_INPUT_TOKENS_THRESHOLD = 50_000
_AREA_DOMINANCE_THRESHOLD = 0.70
_MACHINE_MISSING_FLAG = True


def detect_anomalies(
    current: InsightAggregate,
    sessions: tuple[InsightSessionMeta, ...] = (),
) -> tuple[InsightAnomaly, ...]:
    """Detect outlier sessions and aggregate anomalies."""
    anomalies: list[InsightAnomaly] = []

    if not sessions:
        return ()

    # Cost outliers: session cost > 3x median
    costs = sorted(s.virtual_cost for s in sessions if s.virtual_cost > 0)
    if len(costs) >= 3:
        median_cost = costs[len(costs) // 2]
        cost_threshold = median_cost * Decimal(str(_COST_OUTLIER_MULTIPLIER))
        for s in sessions:
            if s.virtual_cost > cost_threshold:
                anomalies.append(
                    InsightAnomaly(
                        kind="cost",
                        severity="high",
                        session_key=f"{s.harness}/{s.source_session_id}",
                        message=(
                            f"Session cost (${s.virtual_cost:.2f}) exceeds "
                            f"3x median (${cost_threshold:.2f})"
                        ),
                        value=s.virtual_cost,
                        baseline=median_cost,
                    )
                )

    # Failure outliers: tool failures > 3x median and at least 3 failures
    failures = sorted(s.tool_failure_count for s in sessions)
    if failures and failures[-1] >= _FAILURE_MINIMUM:
        median_failures = failures[len(failures) // 2]
        failure_threshold = max(
            int(median_failures * _FAILURE_OUTLIER_MULTIPLIER),
            _FAILURE_MINIMUM,
        )
        for s in sessions:
            if s.tool_failure_count >= failure_threshold:
                anomalies.append(
                    InsightAnomaly(
                        kind="errors",
                        severity="medium",
                        session_key=f"{s.harness}/{s.source_session_id}",
                        message=(
                            f"Session has {s.tool_failure_count} tool failures "
                            f"(threshold: {failure_threshold})"
                        ),
                        value=s.tool_failure_count,
                        baseline=median_failures,
                    )
                )

    # Unpriced in top-cost sessions
    top_by_cost = sorted(sessions, key=lambda s: s.virtual_cost, reverse=True)
    for s in top_by_cost[:5]:
        if s.unpriced_count > 0:
            anomalies.append(
                InsightAnomaly(
                    kind="unpriced",
                    severity="medium",
                    session_key=f"{s.harness}/{s.source_session_id}",
                    message=(
                        f"Top-cost session has {s.unpriced_count} unpriced model calls"
                    ),
                    value=s.unpriced_count,
                    baseline=0,
                )
            )
            break  # flag at most one

    # Cache inefficiency: high input, low cache hit ratio
    for s in sessions:
        if (
            s.input_tokens > _HIGH_INPUT_TOKENS_THRESHOLD
            and s.cache_read_ratio < _CACHE_INEFFICIENCY_THRESHOLD
        ):
            anomalies.append(
                InsightAnomaly(
                    kind="cache",
                    severity="low",
                    session_key=f"{s.harness}/{s.source_session_id}",
                    message=(
                        f"Session has {s.input_tokens:,} input tokens but only "
                        f"{s.cache_read_ratio:.0%} cache hit ratio"
                    ),
                    value=round(s.cache_read_ratio, 4),
                    baseline=_CACHE_INEFFICIENCY_THRESHOLD,
                )
            )
            break  # flag at most one cache anomaly

    # Area dominance: one area accounts for >70% of cost
    if current.by_area and current.virtual_cost > 0:
        for area_row in current.by_area:
            area_share = (
                float(area_row.virtual_cost) / float(current.virtual_cost)
                if current.virtual_cost
                else 0
            )
            if area_share > _AREA_DOMINANCE_THRESHOLD:
                anomalies.append(
                    InsightAnomaly(
                        kind="cost",
                        severity="medium",
                        session_key=area_row.key,
                        message=(
                            f"Area '{area_row.label}' accounts for "
                            f"{area_share:.0%} of total cost"
                        ),
                        value=area_row.virtual_cost,
                        baseline=current.virtual_cost,
                    )
                )
                break  # flag at most one

    # Machine name missing
    for s in sessions:
        if s.origin_machine_id is None or s.origin_machine_id in ("", "(unknown)"):
            anomalies.append(
                InsightAnomaly(
                    kind="cost",
                    severity="low",
                    session_key=f"{s.harness}/{s.source_session_id}",
                    message="Session has no machine name assigned",
                    value=0,
                )
            )
            break  # flag once

    return tuple(anomalies)


def resolve_previous_period(
    since_ms: int | None,
    until_ms: int | None,
) -> tuple[int | None, int | None]:
    """Compute the previous same-length period boundaries.

    For explicit since/until, the previous period starts at
    `since - (until - since)` and ends at `since`.
    Returns (None, None) if boundaries cannot be determined.
    """
    if since_ms is None or until_ms is None:
        return None, None
    if since_ms >= until_ms:
        return None, None

    duration_ms = until_ms - since_ms
    prev_since = since_ms - duration_ms
    prev_until = since_ms
    return prev_since, prev_until
