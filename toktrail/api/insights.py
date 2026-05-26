"""Public API wrapper for insights report generation.

Provides the stable public surface that CLI and external consumers
call.  Delegates to toktrail.insights.service for implementation.
"""

from __future__ import annotations

from pathlib import Path

from toktrail.insights.models import InsightsReport
from toktrail.insights.service import insights_report as _insights_report


def insights_report(
    *,
    db_path: Path | None = None,
    config_path: Path | None = None,
    period: str | None = None,
    timezone_name: str | None = None,
    utc: bool = False,
    since_ms: int | None = None,
    until_ms: int | None = None,
    area: str | None = None,
    area_leaf: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    machine_id: str | None = None,
    harnesses: tuple[str, ...] = (),
    provider_id: str | None = None,
    model_id: str | None = None,
    agent: str | None = None,
    split_thinking: bool = False,
    refresh: bool = True,
) -> InsightsReport:
    """Generate an insights report from existing toktrail state.

    This is the public API entry point.  Parameters and return types
    are stable contracts; internal implementation may change.
    """
    return _insights_report(
        db_path=db_path,
        config_path=config_path,
        period=period,
        timezone_name=timezone_name,
        utc=utc,
        since_ms=since_ms,
        until_ms=until_ms,
        area=area,
        area_leaf=area_leaf,
        area_exact=area_exact,
        unassigned_area=unassigned_area,
        machine_id=machine_id,
        harnesses=harnesses,
        provider_id=provider_id,
        model_id=model_id,
        agent=agent,
        split_thinking=split_thinking,
        refresh=refresh,
    )


__all__ = ["insights_report"]
