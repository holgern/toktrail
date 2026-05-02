from __future__ import annotations

import sqlite3
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _open_state_db
from toktrail.api._conversions import _to_public_run
from toktrail.api.models import Run
from toktrail.errors import (
    ActiveRunExistsError,
    NoActiveRunError,
    RunAlreadyEndedError,
    RunNotFoundError,
    StateDatabaseError,
)


def init_state(db_path: Path | None = None) -> Path:
    conn, resolved = _open_state_db(db_path)
    conn.close()
    return resolved


def start_run(
    db_path: Path | None,
    *,
    name: str | None = None,
    started_at_ms: int | None = None,
) -> Run:
    conn, _ = _open_state_db(db_path)
    try:
        run_id = db_module.create_tracking_session(
            conn,
            name,
            started_at_ms=started_at_ms,
        )
        run = db_module.get_tracking_session(conn, run_id)
    except ValueError as exc:
        if "already active" in str(exc):
            raise ActiveRunExistsError(str(exc)) from exc
        raise StateDatabaseError(str(exc)) from exc
    except sqlite3.Error as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()
    if run is None:
        msg = f"Run not found after creation: {run_id}"
        raise StateDatabaseError(msg)
    public_run = _to_public_run(run)
    if public_run is None:
        msg = f"Run not found after creation: {run_id}"
        raise StateDatabaseError(msg)
    return public_run


def stop_run(
    db_path: Path | None,
    run_id: int | None = None,
    *,
    ended_at_ms: int | None = None,
) -> Run:
    conn, _ = _open_state_db(db_path)
    try:
        selected_run_id = run_id
        if selected_run_id is None:
            active = db_module.get_active_tracking_session(conn)
            if active is None:
                raise NoActiveRunError("An active run is required, but none exists.")
            selected_run_id = active
        run = db_module.get_tracking_session(conn, selected_run_id)
        if run is None:
            msg = f"Run not found: {selected_run_id}"
            raise RunNotFoundError(msg)
        if run.ended_at_ms is not None:
            msg = f"Run {selected_run_id} has already ended."
            raise RunAlreadyEndedError(msg)
        db_module.end_tracking_session(
            conn,
            selected_run_id,
            ended_at_ms=ended_at_ms,
        )
        updated = db_module.get_tracking_session(conn, selected_run_id)
    except sqlite3.Error as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()
    if updated is None:
        msg = f"Run not found after stop: {selected_run_id}"
        raise StateDatabaseError(msg)
    public_run = _to_public_run(updated)
    if public_run is None:
        msg = f"Run not found after stop: {selected_run_id}"
        raise StateDatabaseError(msg)
    return public_run


def get_active_run(db_path: Path | None) -> Run | None:
    conn, _ = _open_state_db(db_path)
    try:
        run_id = db_module.get_active_tracking_session(conn)
        if run_id is None:
            return None
        run = db_module.get_tracking_session(conn, run_id)
    finally:
        conn.close()
    if run is None:
        msg = f"Run not found: {run_id}"
        raise StateDatabaseError(msg)
    public_run = _to_public_run(run)
    if public_run is None:
        msg = f"Run not found: {run_id}"
        raise StateDatabaseError(msg)
    return public_run


def require_active_run(db_path: Path | None) -> Run:
    run = get_active_run(db_path)
    if run is None:
        msg = "An active run is required, but none exists."
        raise NoActiveRunError(msg)
    return run


def get_run(db_path: Path | None, run_id: int) -> Run:
    conn, _ = _open_state_db(db_path)
    try:
        run = db_module.get_tracking_session(conn, run_id)
    finally:
        conn.close()
    if run is None:
        msg = f"Run not found: {run_id}"
        raise RunNotFoundError(msg)
    public_run = _to_public_run(run)
    if public_run is None:
        msg = f"Run not found: {run_id}"
        raise StateDatabaseError(msg)
    return public_run


def list_runs(
    db_path: Path | None,
    *,
    limit: int | None = None,
    include_ended: bool = True,
) -> tuple[Run, ...]:
    conn, _ = _open_state_db(db_path)
    try:
        runs = db_module.list_tracking_sessions(conn)
    finally:
        conn.close()
    public_runs = tuple(
        public_run
        for run in runs
        for public_run in (_to_public_run(run),)
        if public_run is not None
    )
    if not include_ended:
        public_runs = tuple(run for run in public_runs if run.active)
    if limit is not None:
        public_runs = public_runs[:limit]
    return public_runs


__all__ = [
    "get_active_run",
    "get_run",
    "init_state",
    "list_runs",
    "require_active_run",
    "start_run",
    "stop_run",
]
