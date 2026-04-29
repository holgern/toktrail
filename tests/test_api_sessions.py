from __future__ import annotations

import pytest

from toktrail.api.sessions import (
    get_active_session,
    get_session,
    init_state,
    list_sessions,
    require_active_session,
    start_session,
    stop_session,
)
from toktrail.errors import (
    ActiveSessionExistsError,
    NoActiveSessionError,
    SessionAlreadyEndedError,
    SessionNotFoundError,
)


def test_api_init_state_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"

    assert init_state(db_path) == db_path
    assert init_state(db_path) == db_path
    assert db_path.exists()


def test_api_start_and_stop_session_round_trip(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)

    started = start_session(db_path, name="api-session", started_at_ms=123)
    active = get_active_session(db_path)
    stopped = stop_session(db_path, started.id, ended_at_ms=456)

    assert started.active is True
    assert started.started_at_ms == 123
    assert active is not None
    assert active.id == started.id
    assert stopped.active is False
    assert stopped.ended_at_ms == 456
    assert get_session(db_path, started.id).ended_at_ms == 456


def test_api_require_active_session_raises_without_session(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)

    with pytest.raises(NoActiveSessionError, match="active tracking session"):
        require_active_session(db_path)


def test_api_starting_second_active_session_raises(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)
    start_session(db_path, name="first")

    with pytest.raises(ActiveSessionExistsError, match="already active"):
        start_session(db_path, name="second")


def test_api_stop_missing_and_already_ended_sessions_raise(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)
    started = start_session(db_path, name="first")
    stop_session(db_path, started.id)

    with pytest.raises(SessionNotFoundError, match="Tracking session not found"):
        stop_session(db_path, 999)
    with pytest.raises(SessionAlreadyEndedError, match="already ended"):
        stop_session(db_path, started.id)


def test_api_explicit_db_path_overrides_environment(monkeypatch, tmp_path) -> None:
    env_db = tmp_path / "env" / "toktrail.db"
    explicit_db = tmp_path / "explicit" / "toktrail.db"
    monkeypatch.setenv("TOKTRAIL_DB", str(env_db))

    init_state(explicit_db)
    session = start_session(explicit_db, name="explicit")
    sessions = list_sessions(explicit_db)

    assert explicit_db.exists()
    assert env_db.exists() is False
    assert session.id == sessions[0].id
