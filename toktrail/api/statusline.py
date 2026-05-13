from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from toktrail.api.models import StatuslineReport
from toktrail.statusline import StatuslineRequest, build_statusline_report


def statusline_report(
    db_path: Path | None = None,
    *,
    harness: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    source_session_id: str | None = None,
    session_mode: str = "auto",
    basis: str = "virtual",
    max_width: int = 120,
    stale_after_seconds: int = 60,
    active_session_window_minutes: int = 30,
    elements: tuple[str, ...] | None = None,
    stdin_payload: Mapping[str, object] | None = None,
    config_path: Path | None = None,
    now_ms: int | None = None,
) -> StatuslineReport:
    return build_statusline_report(
        db_path,
        config_path=config_path,
        request=StatuslineRequest(
            harness=harness,
            provider_id=provider_id,
            model_id=model_id,
            source_session_id=source_session_id,
            session_mode=session_mode,
            basis=basis,
            max_width=max_width,
            stale_after_seconds=stale_after_seconds,
            active_session_window_minutes=active_session_window_minutes,
            elements=elements if elements is not None else StatuslineRequest().elements,
            stdin_payload=stdin_payload,
        ),
        now_ms=now_ms,
    )


__all__ = ["statusline_report"]
