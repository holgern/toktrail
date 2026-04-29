from __future__ import annotations

from pathlib import Path

from toktrail.api.environment import prepare_environment
from toktrail.api.imports import import_usage
from toktrail.api.models import (
    FinalizedManualRun,
    HarnessEnvironment,
    PreparedManualRun,
    SourceSessionDiff,
    SourceSessionSnapshot,
    SourceSessionSummary,
)
from toktrail.api.paths import resolve_source_path
from toktrail.api.reports import session_report
from toktrail.api.sessions import (
    get_session,
    init_state,
    start_session,
)
from toktrail.api.sessions import (
    stop_session as stop_tracking_session,
)
from toktrail.api.sources import (
    capture_source_snapshot,
    diff_source_snapshots,
)
from toktrail.errors import SourcePathError
from toktrail.paths import new_copilot_otel_file_path


def prepare_manual_run(
    db_path: Path | None,
    harness: str,
    *,
    name: str | None = None,
    source_path: Path | None = None,
    config_path: Path | None = None,
    include_environment: bool = True,
    shell: str = "bash",
) -> PreparedManualRun:
    init_state(db_path)
    tracking_session = start_session(db_path, name=name)
    selected_source_path = _manual_run_source_path(harness, source_path)
    before_snapshot = capture_source_snapshot(
        harness,
        source_path=selected_source_path,
        config_path=config_path,
    )
    environment = (
        prepare_environment(
            harness,
            source_path=selected_source_path,
            shell=shell,
        )
        if include_environment
        else HarnessEnvironment(
            harness=harness.strip().lower(),
            source_path=selected_source_path,
            env={},
            shell_exports=(),
        )
    )
    return PreparedManualRun(
        tracking_session=tracking_session,
        harness=harness,
        source_path=selected_source_path,
        before_snapshot=before_snapshot,
        environment=environment,
    )


def finalize_manual_run(
    db_path: Path | None,
    prepared: PreparedManualRun,
    *,
    source_session_id: str | None = None,
    config_path: Path | None = None,
    include_raw_json: bool = False,
    stop_session: bool = True,
) -> FinalizedManualRun:
    after_snapshot = capture_source_snapshot(
        prepared.harness,
        source_path=prepared.source_path,
        config_path=config_path,
    )
    source_diff = diff_source_snapshots(prepared.before_snapshot, after_snapshot)
    selected_source_session = _select_source_session(
        after_snapshot=after_snapshot,
        source_diff=source_diff,
        source_session_id=source_session_id,
    )
    import_result = import_usage(
        db_path,
        prepared.harness,
        session_id=prepared.tracking_session.id,
        source_path=prepared.source_path,
        source_session_id=selected_source_session.source_session_id,
        include_raw_json=include_raw_json,
    )
    report = session_report(
        db_path,
        prepared.tracking_session.id,
        config_path=config_path,
    )
    tracking_session = (
        stop_tracking_session(db_path, prepared.tracking_session.id)
        if stop_session
        else get_session(db_path, prepared.tracking_session.id)
    )
    return FinalizedManualRun(
        tracking_session=tracking_session,
        source_session=selected_source_session,
        source_diff=source_diff,
        import_result=import_result,
        report=report,
    )


def _manual_run_source_path(
    harness: str,
    source_path: Path | None,
) -> Path | None:
    normalized = harness.strip().lower()
    if normalized == "copilot" and source_path is None:
        return new_copilot_otel_file_path().expanduser()
    return resolve_source_path(normalized, source_path)


def _select_source_session(
    *,
    after_snapshot: SourceSessionSnapshot,
    source_diff: SourceSessionDiff,
    source_session_id: str | None,
) -> SourceSessionSummary:
    if source_session_id is not None:
        matches = [
            summary
            for summary in after_snapshot.sessions
            if summary.source_session_id == source_session_id
        ]
        if not matches:
            msg = f"Source session not found after manual run: {source_session_id}"
            raise SourcePathError(msg)
        return matches[0]
    return source_diff.require_single_candidate()


__all__ = ["finalize_manual_run", "prepare_manual_run"]
