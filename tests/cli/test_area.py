from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.cli.helpers import (
    _future_ms,
    make_cli_usage_event,
)
from toktrail.cli import app
from toktrail.db import (
    assign_area_to_source_session,
    connect,
    ensure_area,
    get_local_machine_id,
    insert_usage_events,
    migrate,
    upsert_machine,
)
from toktrail.models import TokenBreakdown


def test_cli_area_create_list_use_status(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    runner.invoke(app, ["--db", str(state_db), "init"])

    create_result = runner.invoke(
        app,
        ["--db", str(state_db), "area", "create", "privat/toktrail"],
    )
    assert create_result.exit_code == 0, create_result.output

    create_json = runner.invoke(
        app,
        ["--db", str(state_db), "area", "create", "work/odoo", "--json"],
    )
    assert create_json.exit_code == 0, create_json.output
    create_payload = json.loads(create_json.output)
    assert create_payload["area_id"] == create_payload["local_id"]
    assert create_payload["sync_id"] == create_payload["stable_id"]

    list_result = runner.invoke(app, ["--db", str(state_db), "area", "list"])
    assert list_result.exit_code == 0, list_result.output
    assert "privat/toktrail" in list_result.output
    assert "local id" not in list_result.output

    verbose_result = runner.invoke(
        app,
        ["--db", str(state_db), "area", "list", "--verbose"],
    )
    assert verbose_result.exit_code == 0, verbose_result.output
    assert "local id" in verbose_result.output
    assert "stable id" in verbose_result.output

    list_json = runner.invoke(app, ["--db", str(state_db), "area", "list", "--json"])
    assert list_json.exit_code == 0, list_json.output
    list_payload = json.loads(list_json.output)
    assert list_payload[0]["area_id"] == list_payload[0]["local_id"]
    assert list_payload[0]["sync_id"] == list_payload[0]["stable_id"]

    use_result = runner.invoke(
        app,
        ["--db", str(state_db), "area", "use", "privat/toktrail"],
    )
    assert use_result.exit_code == 0, use_result.output
    assert "Active area: privat/toktrail" in use_result.output
    assert (
        "New source sessions imported on this machine will be assigned"
        in use_result.output
    )

    use_json = runner.invoke(
        app,
        ["--db", str(state_db), "area", "use", "privat/toktrail", "--json"],
    )
    assert use_json.exit_code == 0, use_json.output
    use_payload = json.loads(use_json.output)
    active_payload = use_payload["active_area"]
    assert active_payload["area_id"] == active_payload["local_id"]
    assert active_payload["sync_id"] == active_payload["stable_id"]

    status_result = runner.invoke(app, ["--db", str(state_db), "area", "status"])
    assert status_result.exit_code == 0, status_result.output
    assert "privat/toktrail" in status_result.output

    status_json = runner.invoke(
        app,
        ["--db", str(state_db), "area", "status", "--json"],
    )
    assert status_json.exit_code == 0, status_json.output
    status_payload = json.loads(status_json.output)
    status_active = status_payload["active_area"]
    assert status_active["area_id"] == status_active["local_id"]
    assert status_active["sync_id"] == status_active["stable_id"]


def test_cli_area_use_ttl_sets_expiry_in_status_json(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    runner.invoke(app, ["--db", str(state_db), "init"])

    use_result = runner.invoke(
        app,
        ["--db", str(state_db), "area", "use", "work/odoo", "--ttl", "1h"],
    )
    assert use_result.exit_code == 0, use_result.output

    status_json = runner.invoke(
        app,
        ["--db", str(state_db), "area", "status", "--json"],
    )
    assert status_json.exit_code == 0, status_json.output
    payload = json.loads(status_json.output)
    assert payload["active_area"]["path"] == "work/odoo"
    assert isinstance(payload["expires_at_ms"], int)


def test_cli_area_assign_and_unassign_old_session(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "assign",
                    source_session_id="ses-assign",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=10, output=2),
                )
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assign_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "area",
            "assign",
            "privat/toktrail",
            "--harness",
            "opencode",
            "--source-session-id",
            "ses-assign",
        ],
    )
    assert assign_result.exit_code == 0, assign_result.output

    sessions_assigned = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--json", "--no-refresh"],
    )
    assert sessions_assigned.exit_code == 0, sessions_assigned.output
    assigned_payload = json.loads(sessions_assigned.output)
    assert assigned_payload["sessions"][0]["area_path"] == "privat/toktrail"
    assert "area_sync_id" in assigned_payload["sessions"][0]

    unassign_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "area",
            "unassign",
            "--harness",
            "opencode",
            "--source-session-id",
            "ses-assign",
        ],
    )
    assert unassign_result.exit_code == 0, unassign_result.output

    sessions_unassigned = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--json", "--no-refresh"],
    )
    assert sessions_unassigned.exit_code == 0, sessions_unassigned.output
    unassigned_payload = json.loads(sessions_unassigned.output)
    assert unassigned_payload["sessions"][0]["area_path"] is None


def test_cli_area_assign_and_unassign_by_session_key(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "assign-key",
                    source_session_id="ses-assign-key",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=12, output=3),
                )
            ],
        )
        conn.commit()
    finally:
        conn.close()

    sessions = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--json", "--no-refresh"],
    )
    assert sessions.exit_code == 0, sessions.output
    key = json.loads(sessions.output)["sessions"][0]["key"]

    assign_result = runner.invoke(
        app,
        ["--db", str(state_db), "area", "assign", "privat/toktrail", "--session", key],
    )
    assert assign_result.exit_code == 0, assign_result.output

    sessions_after_assign = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--json", "--no-refresh"],
    )
    assert sessions_after_assign.exit_code == 0, sessions_after_assign.output
    assert (
        json.loads(sessions_after_assign.output)["sessions"][0]["area_path"]
        == "privat/toktrail"
    )

    unassign_result = runner.invoke(
        app,
        ["--db", str(state_db), "area", "unassign", "--session", key],
    )
    assert unassign_result.exit_code == 0, unassign_result.output

    sessions_after_unassign = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--json", "--no-refresh"],
    )
    assert sessions_after_unassign.exit_code == 0, sessions_after_unassign.output
    assert (
        json.loads(sessions_after_unassign.output)["sessions"][0]["area_path"] is None
    )


def test_cli_area_assign_last_defaults_local_machine_and_all_machines_override(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        local_machine_id = get_local_machine_id(conn)
        remote_machine_id = "remote-machine-1234abcd"
        upsert_machine(
            conn,
            machine_id=remote_machine_id,
            name="pc2",
            seen_ms=_future_ms(),
            is_local=False,
        )
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "last-local",
                    source_session_id="ses-local",
                    created_ms=_future_ms() - 5_000,
                    tokens=TokenBreakdown(input=10, output=1),
                )
            ],
            origin_machine_id=local_machine_id,
        )
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "last-remote",
                    source_session_id="ses-remote",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=20, output=2),
                )
            ],
            origin_machine_id=remote_machine_id,
        )
        conn.commit()
    finally:
        conn.close()

    local_only = runner.invoke(
        app,
        ["--db", str(state_db), "area", "assign", "work/odoo", "--last"],
    )
    assert local_only.exit_code == 0, local_only.output
    assert "ses-local" in local_only.output

    all_machines = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "area",
            "assign",
            "privat/toktrail",
            "--last",
            "--all-machines",
        ],
    )
    assert all_machines.exit_code == 0, all_machines.output
    assert "ses-remote" in all_machines.output


def test_cli_area_sessions_unassigned_json(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "area-sessions-assigned",
                    source_session_id="ses-area-assigned",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
                make_cli_usage_event(
                    "area-sessions-unassigned",
                    source_session_id="ses-area-unassigned",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=8, output=1),
                ),
            ],
        )
        machine_id = get_local_machine_id(conn)
        area = ensure_area(conn, "work/odoo")
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-area-assigned",
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "area",
            "sessions",
            "--unassigned",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sessions"]
    assert all(session["area_path"] is None for session in payload["sessions"])


def test_cli_area_sessions_positional_selector_accepts_unique_suffix(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "area-suffix",
                    source_session_id="ses-area-suffix",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=10, output=2),
                )
            ],
        )
        machine_id = get_local_machine_id(conn)
        area = ensure_area(conn, "privat/toktrail")
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-area-suffix",
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "area",
            "sessions",
            "toktrail",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["sessions"]) == 1
    assert payload["filters"]["area"] == "toktrail"
    assert payload["filters"]["area_match"] == "unique_suffix"
    assert payload["filters"]["area_matches"] == ["privat/toktrail"]


def test_cli_area_sessions_accepts_area_leaf(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "area-leaf-a",
                    source_session_id="ses-area-leaf-a",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
                make_cli_usage_event(
                    "area-leaf-b",
                    source_session_id="ses-area-leaf-b",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=8, output=1),
                ),
            ],
        )
        machine_id = get_local_machine_id(conn)
        toktrail_tests = ensure_area(conn, "privat/toktrail/tests")
        taskledger_tests = ensure_area(conn, "privat/taskledger/tests")
        assign_area_to_source_session(
            conn,
            area_id=toktrail_tests.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-area-leaf-a",
        )
        assign_area_to_source_session(
            conn,
            area_id=taskledger_tests.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-area-leaf-b",
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "area",
            "sessions",
            "--area-leaf",
            "tests",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["sessions"]) == 2
    assert payload["filters"]["area"] == "tests"
    assert payload["filters"]["area_match"] == "leaf"
    assert payload["filters"]["area_matches"] == [
        "privat/taskledger/tests",
        "privat/toktrail/tests",
    ]
