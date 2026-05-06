from __future__ import annotations

import pytest

from toktrail.api.models import RunScope
from toktrail.api.sessions import (
    archive_run,
    get_active_run,
    get_run,
    init_state,
    list_runs,
    require_active_run,
    start_run,
    stop_run,
    unarchive_run,
)
from toktrail.errors import (
    ActiveRunExistsError,
    NoActiveRunError,
    RunAlreadyEndedError,
    RunNotFoundError,
)


def test_api_init_state_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"

    assert init_state(db_path) == db_path
    assert init_state(db_path) == db_path
    assert db_path.exists()


def test_api_start_and_stop_session_round_trip(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)

    started = start_run(db_path, name="api-session", started_at_ms=123)
    active = get_active_run(db_path)
    stopped = stop_run(db_path, started.id, ended_at_ms=456)

    assert started.active is True
    assert started.sync_id
    assert started.started_at_ms == 123
    assert active is not None
    assert active.id == started.id
    assert stopped.active is False
    assert stopped.ended_at_ms == 456
    assert get_run(db_path, started.id).ended_at_ms == 456


def test_api_require_active_run_raises_without_session(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)

    with pytest.raises(NoActiveRunError, match="active run"):
        require_active_run(db_path)


def test_api_starting_second_active_session_raises(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)
    start_run(db_path, name="first")

    with pytest.raises(ActiveRunExistsError, match="already active"):
        start_run(db_path, name="second")


def test_api_stop_missing_and_already_ended_sessions_raise(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)
    started = start_run(db_path, name="first")
    stop_run(db_path, started.id)

    with pytest.raises(RunNotFoundError, match="Run not found"):
        stop_run(db_path, 999)
    with pytest.raises(RunAlreadyEndedError, match="already ended"):
        stop_run(db_path, started.id)


def test_api_explicit_db_path_overrides_environment(monkeypatch, tmp_path) -> None:
    env_db = tmp_path / "env" / "toktrail.db"
    explicit_db = tmp_path / "explicit" / "toktrail.db"
    monkeypatch.setenv("TOKTRAIL_DB", str(env_db))

    init_state(explicit_db)
    session = start_run(explicit_db, name="explicit")
    sessions = list_runs(explicit_db)

    assert explicit_db.exists()
    assert env_db.exists() is False
    assert session.id == sessions[0].id


def test_api_start_run_accepts_scope(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)

    run = start_run(
        db_path,
        name="scoped",
        scope=RunScope(harnesses=("codex",), provider_ids=("openai",)),
    )

    assert run.scope.harnesses == ("codex",)
    assert run.scope.provider_ids == ("openai",)


def test_api_archive_and_unarchive_run(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)
    run = start_run(db_path, name="archive")
    stop_run(db_path, run.id)

    archived = archive_run(db_path, run.id)
    unarchived = unarchive_run(db_path, run.id)

    assert archived.archived_at_ms is not None
    assert unarchived.archived_at_ms is None


def test_api_list_runs_excludes_archived_by_default(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)
    run = start_run(db_path, name="archive")
    stop_run(db_path, run.id)
    archive_run(db_path, run.id)

    default_runs = list_runs(db_path)
    archived_runs = list_runs(db_path, include_archived=True)
    archived_only = list_runs(db_path, archived_only=True)

    assert all(item.id != run.id for item in default_runs)
    assert any(item.id == run.id for item in archived_runs)
    assert [item.id for item in archived_only] == [run.id]


def test_public_run_json_includes_scope_and_archived_at_ms(tmp_path) -> None:
    db_path = tmp_path / "toktrail.db"
    init_state(db_path)
    run = start_run(db_path, name="scoped", scope=RunScope(harnesses=("codex",)))
    stop_run(db_path, run.id)
    archived = archive_run(db_path, run.id)

    payload = archived.as_dict()

    assert payload["scope"]["harnesses"] == ["codex"]
    assert payload["archived_at_ms"] is not None
