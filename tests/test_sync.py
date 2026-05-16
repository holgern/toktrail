from __future__ import annotations

import json
import tarfile
import tempfile
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from toktrail.db import (
    archive_tracking_session,
    assign_area_to_source_session,
    connect,
    create_tracking_session,
    end_tracking_session,
    ensure_area,
    get_active_area,
    get_active_area_status,
    get_local_machine_id,
    get_tracking_session,
    insert_usage_events,
    list_areas,
    migrate,
    set_active_area,
    summarize_tracking_session,
    summarize_usage,
)
from toktrail.models import RunScope, TokenBreakdown, UsageEvent
from toktrail.reporting import UsageReportFilter
from toktrail.sync import export_state_archive, import_state_archive


def make_usage_event(
    *,
    dedup_suffix: str,
    fingerprint: str | None = None,
    source_session_id: str = "ses-1",
    source_cost_usd: float = 1.0,
    raw_json: str | None = "{}",
    created_ms: int = 1777801200000,
) -> UsageEvent:
    return UsageEvent(
        harness="opencode",
        source_session_id=source_session_id,
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"msg-{dedup_suffix}",
        global_dedup_key=f"opencode:msg-{dedup_suffix}",
        fingerprint_hash=fingerprint or f"fp-{dedup_suffix}",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
        thinking_level=None,
        agent="build",
        created_ms=created_ms,
        completed_ms=created_ms + 100,
        tokens=TokenBreakdown(input=100, output=20),
        source_cost_usd=Decimal(str(source_cost_usd)),
        raw_json=raw_json,
    )


def seed_db_with_event(
    db_path: Path,
    *,
    run_name: str,
    event: UsageEvent,
    end_run: bool = True,
) -> int:
    conn = connect(db_path)
    try:
        migrate(conn)
        run_id = create_tracking_session(
            conn,
            run_name,
            started_at_ms=event.created_ms,
        )
        insert_usage_events(conn, run_id, [event])
        if end_run:
            end_tracking_session(conn, run_id, ended_at_ms=event.completed_ms)
        return run_id
    finally:
        conn.close()


def usage_total_tokens(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        migrate(conn)
        report = summarize_usage(conn, UsageReportFilter())
        return report.totals.tokens.total
    finally:
        conn.close()


def test_sync_export_creates_valid_archive(tmp_path: Path) -> None:
    db_path = tmp_path / "state-a.db"
    archive_path = tmp_path / "state-a.tar.gz"
    event = make_usage_event(dedup_suffix="1")
    seed_db_with_event(db_path, run_name="run-a", event=event)

    result = export_state_archive(db_path, archive_path)

    assert archive_path.exists()
    assert result.usage_event_count == 1
    with tarfile.open(archive_path, "r:gz") as tar:
        names = set(tar.getnames())
        assert "manifest.json" in names
        assert "toktrail-state.sqlite" in names
        manifest_member = tar.extractfile("manifest.json")
        state_member = tar.extractfile("toktrail-state.sqlite")
        assert manifest_member is not None
        assert state_member is not None
        manifest = json.loads(manifest_member.read().decode("utf-8"))
        exported_db = state_member.read()
    with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
        handle.write(exported_db)
        handle.flush()
        checksum = _sha256_bytes(exported_db)
        assert manifest["sha256"]["toktrail-state.sqlite"] == checksum


def test_sync_import_into_empty_db_preserves_totals(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"
    seed_db_with_event(db_a, run_name="run-a", event=make_usage_event(dedup_suffix="1"))
    export_state_archive(db_a, archive)

    import_state_archive(db_b, archive)

    assert usage_total_tokens(db_b) == usage_total_tokens(db_a)


def test_sync_round_trip_two_machine_merge_is_idempotent(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive_a = tmp_path / "a.tar.gz"
    archive_b = tmp_path / "b.tar.gz"

    seed_db_with_event(
        db_a,
        run_name="run-a",
        event=make_usage_event(dedup_suffix="a1"),
    )
    export_state_archive(db_a, archive_a)
    import_state_archive(db_b, archive_a)

    seed_db_with_event(
        db_b,
        run_name="run-b",
        event=make_usage_event(dedup_suffix="b1"),
    )
    export_state_archive(db_b, archive_b)
    import_state_archive(db_a, archive_b)

    first_total = usage_total_tokens(db_a)
    import_state_archive(db_a, archive_b)
    second_total = usage_total_tokens(db_a)

    assert first_total == second_total
    assert first_total > 0


def test_sync_duplicate_event_with_same_fingerprint_is_skipped(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    event = make_usage_event(dedup_suffix="dup", fingerprint="same")
    seed_db_with_event(db_a, run_name="run-a", event=event)
    seed_db_with_event(db_b, run_name="run-b", event=event)
    export_state_archive(db_a, archive)

    result = import_state_archive(db_b, archive)

    assert result.usage_events_inserted == 0
    assert result.usage_events_skipped >= 1


def test_sync_preserves_usage_event_origin_machine(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"
    remote_machine_id = "remote-origin-1234"
    conn = connect(db_a)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [make_usage_event(dedup_suffix="origin-1")],
            origin_machine_id=remote_machine_id,
        )
    finally:
        conn.close()
    export_state_archive(db_a, archive)
    import_state_archive(db_b, archive)

    imported = connect(db_b)
    try:
        migrate(imported)
        row = imported.execute(
            "SELECT origin_machine_id FROM usage_events LIMIT 1"
        ).fetchone()
    finally:
        imported.close()

    assert row is not None
    assert row["origin_machine_id"] == remote_machine_id


def test_sync_duplicate_event_keeps_existing_origin_machine(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"
    local_origin = "local-origin-1111"
    remote_origin = "remote-origin-2222"
    event = make_usage_event(dedup_suffix="dup-origin", fingerprint="fp-same")

    conn_a = connect(db_a)
    try:
        migrate(conn_a)
        insert_usage_events(
            conn_a,
            None,
            [event],
            origin_machine_id=remote_origin,
        )
    finally:
        conn_a.close()
    conn_b = connect(db_b)
    try:
        migrate(conn_b)
        insert_usage_events(
            conn_b,
            None,
            [event],
            origin_machine_id=local_origin,
        )
    finally:
        conn_b.close()

    export_state_archive(db_a, archive)
    result = import_state_archive(db_b, archive)

    imported = connect(db_b)
    try:
        migrate(imported)
        row = imported.execute(
            """
            SELECT origin_machine_id
            FROM usage_events
            WHERE global_dedup_key = ?
            """,
            (event.global_dedup_key,),
        ).fetchone()
    finally:
        imported.close()

    assert result.usage_events_inserted == 0
    assert result.usage_events_skipped >= 1
    assert row is not None
    assert row["origin_machine_id"] == local_origin


def test_sync_duplicate_event_with_different_fingerprint_conflicts(
    tmp_path: Path,
) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    seed_db_with_event(
        db_a,
        run_name="run-a",
        event=make_usage_event(dedup_suffix="dup", fingerprint="fp-a"),
    )
    seed_db_with_event(
        db_b,
        run_name="run-b",
        event=make_usage_event(dedup_suffix="dup", fingerprint="fp-b"),
    )
    export_state_archive(db_a, archive)
    before_total = usage_total_tokens(db_b)

    with pytest.raises(ValueError, match="Fingerprint mismatch"):
        import_state_archive(db_b, archive)

    after_total = usage_total_tokens(db_b)
    assert before_total == after_total


def test_sync_run_id_collision_inserts_distinct_run(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    seed_db_with_event(db_a, run_name="run-a", event=make_usage_event(dedup_suffix="1"))
    seed_db_with_event(db_b, run_name="run-b", event=make_usage_event(dedup_suffix="2"))
    export_state_archive(db_a, archive)
    import_state_archive(db_b, archive)

    conn = connect(db_b)
    try:
        migrate(conn)
        rows = conn.execute("SELECT id, sync_id, name FROM runs ORDER BY id").fetchall()
    finally:
        conn.close()
    assert len(rows) >= 2
    assert len({str(row["sync_id"]) for row in rows}) == len(rows)
    assert any(row["name"] == "run-a" for row in rows)


def test_sync_run_event_remapping_keeps_imported_run_reporting(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    run_a_id = seed_db_with_event(
        db_a,
        run_name="run-a",
        event=make_usage_event(dedup_suffix="a1"),
    )
    seed_db_with_event(
        db_b,
        run_name="run-b",
        event=make_usage_event(dedup_suffix="b1"),
    )
    assert run_a_id == 1
    export_state_archive(db_a, archive)
    import_state_archive(db_b, archive)

    conn = connect(db_b)
    try:
        migrate(conn)
        imported_row = conn.execute(
            "SELECT id FROM runs WHERE name = 'run-a' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert imported_row is not None
        report = summarize_tracking_session(conn, int(imported_row["id"]))
    finally:
        conn.close()

    assert report.totals.tokens.total > 0


def test_sync_preserves_run_scope_and_archived_metadata(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    conn = connect(db_a)
    try:
        migrate(conn)
        run_id = create_tracking_session(
            conn,
            "scoped",
            scope=RunScope(
                harnesses=("codex",),
                provider_ids=("openai",),
                model_ids=("gpt-5.5",),
            ),
        )
        end_tracking_session(conn, run_id)
        archive_tracking_session(conn, run_id, archived_at_ms=1234)
    finally:
        conn.close()

    export_state_archive(db_a, archive)
    import_state_archive(db_b, archive)

    conn = connect(db_b)
    try:
        migrate(conn)
        row = conn.execute("SELECT id FROM runs WHERE name = 'scoped'").fetchone()
        assert row is not None
        run = get_tracking_session(conn, int(row["id"]))
    finally:
        conn.close()

    assert run is not None
    assert run.scope.harnesses == ("codex",)
    assert run.scope.provider_ids == ("openai",)
    assert run.scope.model_ids == ("gpt-5.5",)
    assert run.archived_at_ms == 1234


def test_sync_export_redacts_raw_json(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    seed_db_with_event(
        db_a,
        run_name="run-a",
        event=make_usage_event(dedup_suffix="1", raw_json='{"secret":true}'),
    )
    export_state_archive(db_a, archive, redact_raw_json=True)
    import_state_archive(db_b, archive)

    conn = connect(db_b)
    try:
        migrate(conn)
        row = conn.execute("SELECT raw_json FROM usage_events LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["raw_json"] is None


def test_sync_import_rejects_unsafe_archive_paths(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    archive_path = tmp_path / "unsafe.tar.gz"
    payload_path = tmp_path / "payload.txt"
    payload_path.write_text("x", encoding="utf-8")

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(payload_path, arcname="../evil")

    with pytest.raises(ValueError, match="Unsafe archive member path"):
        import_state_archive(db_path, archive_path)


def test_sync_preserves_area_tree_and_session_assignments(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    conn_a = connect(db_a)
    try:
        migrate(conn_a)
        insert_usage_events(
            conn_a,
            None,
            [
                make_usage_event(
                    dedup_suffix="area-roundtrip",
                    source_session_id="ses-area-roundtrip",
                )
            ],
        )
        area = ensure_area(conn_a, "work/odoo")
        machine_id = get_local_machine_id(conn_a)
        assign_area_to_source_session(
            conn_a,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-area-roundtrip",
        )
        set_active_area(
            conn_a,
            area.id,
            machine_id=machine_id,
            expires_at_ms=9_999_999_999_999,
        )
        conn_a.commit()
    finally:
        conn_a.close()

    export_state_archive(db_a, archive)
    import_state_archive(db_b, archive)

    conn_b = connect(db_b)
    try:
        migrate(conn_b)
        paths = {area.path for area in list_areas(conn_b)}
        assert "work" in paths
        assert "work/odoo" in paths
        assignment_row = conn_b.execute(
            """
            SELECT a.path
            FROM area_session_assignments asa
            JOIN areas a ON a.id = asa.area_id
            WHERE asa.harness = ?
              AND asa.source_session_id = ?
            """,
            ("opencode", "ses-area-roundtrip"),
        ).fetchone()
        usage_row = conn_b.execute(
            """
            SELECT a.path
            FROM usage_events ue
            JOIN areas a ON a.id = ue.area_id
            WHERE ue.global_dedup_key = ?
            """,
            ("opencode:msg-area-roundtrip",),
        ).fetchone()
        imported_machine = conn_b.execute(
            """
            SELECT machine_id
            FROM machines
            WHERE is_local = 0
            ORDER BY machine_id
            LIMIT 1
            """
        ).fetchone()
        assert imported_machine is not None
        active = get_active_area(conn_b, machine_id=str(imported_machine["machine_id"]))
        active_status = get_active_area_status(
            conn_b,
            machine_id=str(imported_machine["machine_id"]),
        )
    finally:
        conn_b.close()

    assert assignment_row is not None
    assert assignment_row["path"] == "work/odoo"
    assert usage_row is not None
    assert usage_row["path"] == "work/odoo"
    assert active is not None
    assert active.path == "work/odoo"
    assert active_status.expires_at_ms == 9_999_999_999_999


def test_sync_usage_events_keep_area_after_import(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    conn_a = connect(db_a)
    try:
        migrate(conn_a)
        insert_usage_events(
            conn_a,
            None,
            [
                make_usage_event(
                    dedup_suffix="area-map",
                    source_session_id="ses-area-map",
                )
            ],
        )
        area_a = ensure_area(conn_a, "work/odoo")
        area_a_sync_id = area_a.sync_id
        assign_area_to_source_session(
            conn_a,
            area_id=area_a.id,
            origin_machine_id=get_local_machine_id(conn_a),
            harness="opencode",
            source_session_id="ses-area-map",
        )
        conn_a.commit()
    finally:
        conn_a.close()

    conn_b = connect(db_b)
    try:
        migrate(conn_b)
        local_area = ensure_area(conn_b, "work/odoo")
        local_area_id = local_area.id
        local_area_sync_id = local_area.sync_id
        conn_b.commit()
    finally:
        conn_b.close()

    export_state_archive(db_a, archive)
    import_state_archive(db_b, archive)

    conn_b = connect(db_b)
    try:
        migrate(conn_b)
        row = conn_b.execute(
            """
            SELECT ue.area_id, a.sync_id AS area_sync_id
            FROM usage_events AS ue
            LEFT JOIN areas AS a ON a.id = ue.area_id
            WHERE global_dedup_key = ?
            """,
            ("opencode:msg-area-map",),
        ).fetchone()
    finally:
        conn_b.close()

    assert row is not None
    assert row["area_id"] == local_area_id
    assert row["area_sync_id"] == area_a_sync_id
    assert local_area_sync_id == area_a_sync_id


def test_sync_area_assignment_conflict_uses_updated_at(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"
    shared_machine_id = "shared-machine-1234"

    conn_a = connect(db_a)
    try:
        migrate(conn_a)
        insert_usage_events(
            conn_a,
            None,
            [make_usage_event(dedup_suffix="assign-a", source_session_id="ses-shared")],
            origin_machine_id=shared_machine_id,
        )
        area_a = ensure_area(conn_a, "work/odoo")
        assign_a = assign_area_to_source_session(
            conn_a,
            area_id=area_a.id,
            origin_machine_id=shared_machine_id,
            harness="opencode",
            source_session_id="ses-shared",
        )
        conn_a.execute(
            "UPDATE area_session_assignments SET updated_at_ms = ? WHERE id = ?",
            (1_000, assign_a.id),
        )
        conn_a.commit()
    finally:
        conn_a.close()

    conn_b = connect(db_b)
    try:
        migrate(conn_b)
        insert_usage_events(
            conn_b,
            None,
            [make_usage_event(dedup_suffix="assign-b", source_session_id="ses-shared")],
            origin_machine_id=shared_machine_id,
        )
        area_b = ensure_area(conn_b, "privat/toktrail")
        assign_b = assign_area_to_source_session(
            conn_b,
            area_id=area_b.id,
            origin_machine_id=shared_machine_id,
            harness="opencode",
            source_session_id="ses-shared",
        )
        conn_b.execute(
            "UPDATE area_session_assignments SET updated_at_ms = ? WHERE id = ?",
            (2_000, assign_b.id),
        )
        conn_b.commit()
    finally:
        conn_b.close()

    export_state_archive(db_a, archive)
    import_state_archive(db_b, archive)

    conn_b = connect(db_b)
    try:
        migrate(conn_b)
        assignment = conn_b.execute(
            """
            SELECT a.path
            FROM area_session_assignments asa
            JOIN areas a ON a.id = asa.area_id
            WHERE asa.harness = ?
              AND asa.source_session_id = ?
            """,
            ("opencode", "ses-shared"),
        ).fetchone()
        event_paths = conn_b.execute(
            """
            SELECT DISTINCT a.path
            FROM usage_events ue
            LEFT JOIN areas a ON a.id = ue.area_id
            WHERE ue.source_session_id = ?
            """,
            ("ses-shared",),
        ).fetchall()
    finally:
        conn_b.close()

    assert assignment is not None
    assert assignment["path"] == "privat/toktrail"
    assert {row["path"] for row in event_paths} == {"privat/toktrail"}


def test_sync_import_detects_area_path_sync_id_conflict(tmp_path: Path) -> None:
    db_a = tmp_path / "state-a.db"
    db_b = tmp_path / "state-b.db"
    archive = tmp_path / "a.tar.gz"

    conn_a = connect(db_a)
    try:
        migrate(conn_a)
        area = ensure_area(conn_a, "work/odoo")
        conn_a.execute(
            "UPDATE areas SET sync_id = ? WHERE id = ?",
            ("imported-sync-conflict", area.id),
        )
        conn_a.commit()
    finally:
        conn_a.close()

    conn_b = connect(db_b)
    try:
        migrate(conn_b)
        by_path = ensure_area(conn_b, "work/odoo")
        by_sync = ensure_area(conn_b, "privat/toktrail")
        conn_b.execute(
            "UPDATE areas SET sync_id = ? WHERE id = ?",
            ("local-path-sync", by_path.id),
        )
        conn_b.execute(
            "UPDATE areas SET sync_id = ? WHERE id = ?",
            ("imported-sync-conflict", by_sync.id),
        )
        conn_b.commit()
    finally:
        conn_b.close()

    export_state_archive(db_a, archive)

    with pytest.raises(ValueError, match="Area sync conflict"):
        import_state_archive(db_b, archive)


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()
