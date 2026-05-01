from __future__ import annotations

import sqlite3
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _open_state_db
from toktrail.api._conversions import (
    _to_public_tracking_session,
)
from toktrail.api.models import Run
from toktrail.errors import (
    ActiveRunExistsError as ActiveSessionExistsError,
)
from toktrail.errors import (
    NoActiveRunError as NoActiveSessionError,
)
from toktrail.errors import (
    RunAlreadyEndedError as SessionAlreadyEndedError,
)
from toktrail.errors import (
    RunNotFoundError as SessionNotFoundError,
)
from toktrail.errors import (
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
    return start_session(
        db_path=db_path,
        name=name,
        started_at_ms=started_at_ms,
    )


def stop_run(
    db_path: Path | None,
    session_id: int | None = None,
    *,
    ended_at_ms: int | None = None,
) -> Run:
    return stop_session(
        db_path=db_path,
        session_id=session_id,
        ended_at_ms=ended_at_ms,
    )


def get_active_run(db_path: Path | None) -> Run | None:
    return get_active_session(db_path)


def require_active_run(db_path: Path | None) -> Run:
    return require_active_session(db_path)


def get_run(db_path: Path | None, session_id: int) -> Run:
    return get_session(db_path=db_path, session_id=session_id)


def list_runs(
    db_path: Path | None,
    *,
    limit: int | None = None,
    include_ended: bool = True,
) -> tuple[Run, ...]:
    return list_sessions(
        db_path=db_path,
        limit=limit,
        include_ended=include_ended,
    )


def start_session(
    db_path: Path | None,
    *,
    name: str | None = None,
    started_at_ms: int | None = None,
) -> Run:
    conn, _ = _open_state_db(db_path)
    try:
        session_id = db_module.create_tracking_session(
            conn,
            name,
            started_at_ms=started_at_ms,
        )
        session = db_module.get_tracking_session(conn, session_id)
    except ValueError as exc:
        if "already active" in str(exc):
            raise ActiveSessionExistsError(str(exc)) from exc
        raise StateDatabaseError(str(exc)) from exc
    except sqlite3.Error as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()
    if session is None:
        msg = f"Tracking session not found after creation: {session_id}"
        raise StateDatabaseError(msg)
    public_session = _to_public_tracking_session(session)
    if public_session is None:
        msg = f"Tracking session not found after creation: {session_id}"
        raise StateDatabaseError(msg)
    return public_session


def stop_session(
    db_path: Path | None,
    session_id: int | None = None,
    *,
    ended_at_ms: int | None = None,
) -> Run:
    conn, _ = _open_state_db(db_path)
    try:
        selected_session_id = session_id
        if selected_session_id is None:
            active = db_module.get_active_tracking_session(conn)
            if active is None:
                raise NoActiveSessionError(
                    "An active tracking session is required, but none exists."
                )
            selected_session_id = active
        session = db_module.get_tracking_session(conn, selected_session_id)
        if session is None:
            msg = f"Tracking session not found: {selected_session_id}"
            raise SessionNotFoundError(msg)
        if session.ended_at_ms is not None:
            msg = f"Tracking session {selected_session_id} has already ended."
            raise SessionAlreadyEndedError(msg)
        db_module.end_tracking_session(
            conn,
            selected_session_id,
            ended_at_ms=ended_at_ms,
        )
        updated = db_module.get_tracking_session(conn, selected_session_id)
    except sqlite3.Error as exc:
        raise StateDatabaseError(str(exc)) from exc
    finally:
        conn.close()
    if updated is None:
        msg = f"Tracking session not found after stop: {selected_session_id}"
        raise StateDatabaseError(msg)
    public_session = _to_public_tracking_session(updated)
    if public_session is None:
        msg = f"Tracking session not found after stop: {selected_session_id}"
        raise StateDatabaseError(msg)
    return public_session


def get_active_session(db_path: Path | None) -> Run | None:
    conn, _ = _open_state_db(db_path)
    try:
        session_id = db_module.get_active_tracking_session(conn)
        if session_id is None:
            return None
        session = db_module.get_tracking_session(conn, session_id)
    finally:
        conn.close()
    if session is None:
        msg = f"Tracking session not found: {session_id}"
        raise StateDatabaseError(msg)
    public_session = _to_public_tracking_session(session)
    if public_session is None:
        msg = f"Tracking session not found: {session_id}"
        raise StateDatabaseError(msg)
    return public_session


def require_active_session(db_path: Path | None) -> Run:
    session = get_active_session(db_path)
    if session is None:
        msg = "An active tracking session is required, but none exists."
        raise NoActiveSessionError(msg)
    return session


def get_session(db_path: Path | None, session_id: int) -> Run:
    conn, _ = _open_state_db(db_path)
    try:
        session = db_module.get_tracking_session(conn, session_id)
    finally:
        conn.close()
    if session is None:
        msg = f"Tracking session not found: {session_id}"
        raise SessionNotFoundError(msg)
    public_session = _to_public_tracking_session(session)
    if public_session is None:
        msg = f"Tracking session not found: {session_id}"
        raise StateDatabaseError(msg)
    return public_session


def list_sessions(
    db_path: Path | None,
    *,
    limit: int | None = None,
    include_ended: bool = True,
) -> tuple[Run, ...]:
    conn, _ = _open_state_db(db_path)
    try:
        sessions = db_module.list_tracking_sessions(conn)
    finally:
        conn.close()
    public_sessions = tuple(
        public_session
        for session in sessions
        for public_session in (_to_public_tracking_session(session),)
        if public_session is not None
    )
    if not include_ended:
        public_sessions = tuple(
            session for session in public_sessions if session.active
        )
    if limit is not None:
        public_sessions = public_sessions[:limit]
    return public_sessions


__all__ = [
    # New run terminology
    "start_run",
    "stop_run",
    "get_active_run",
    "require_active_run",
    "get_run",
    "list_runs",
    "init_state",
    # Legacy session terminology (backward compatibility)
    "start_session",
    "stop_session",
    "get_active_session",
    "require_active_session",
    "get_session",
    "list_sessions",
]
