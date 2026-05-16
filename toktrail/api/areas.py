from __future__ import annotations

import sqlite3
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _open_state_db
from toktrail.api._conversions import (
    _to_public_active_area_status,
    _to_public_area,
    _to_public_area_session_assignment,
)
from toktrail.api.models import (
    ActiveArea,
    Area,
    AreaSessionAssignment,
    UsageAreasReport,
    UsageSessionsReport,
)
from toktrail.api.reports import usage_areas_report, usage_sessions_report


def create_area(
    path: str,
    *,
    db_path: Path | None = None,
    name: str | None = None,
) -> Area:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        area = db_module.ensure_area(conn, path, name=name)
        conn.commit()
    finally:
        conn.close()
    return _to_public_area(area)


def list_areas(
    db_path: Path | None = None,
    *,
    include_archived: bool = False,
) -> tuple[Area, ...]:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        areas = db_module.list_areas(conn, include_archived=include_archived)
    finally:
        conn.close()
    return tuple(_to_public_area(area) for area in areas)


def get_active_area(db_path: Path | None = None) -> Area | None:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        area = db_module.get_active_area(conn)
    finally:
        conn.close()
    if area is None:
        return None
    return _to_public_area(area)


def get_active_area_status(db_path: Path | None = None) -> ActiveArea:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        status = db_module.get_active_area_status(conn)
    finally:
        conn.close()
    return _to_public_active_area_status(status)


def set_active_area(
    path: str,
    *,
    db_path: Path | None = None,
    create: bool = True,
    expires_at_ms: int | None = None,
) -> Area:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        area = db_module.get_area_by_path(conn, path)
        if area is None:
            if not create:
                msg = f"Area not found: {path}"
                raise ValueError(msg)
            area = db_module.ensure_area(conn, path)
        db_module.set_active_area(conn, area.id, expires_at_ms=expires_at_ms)
        conn.commit()
    finally:
        conn.close()
    return _to_public_area(area)


def clear_active_area(*, db_path: Path | None = None) -> None:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        db_module.set_active_area(conn, None)
        conn.commit()
    finally:
        conn.close()


def _resolve_assignment_machine_id(
    conn: sqlite3.Connection,
    *,
    harness: str,
    source_session_id: str,
    machine: str | None,
) -> str:
    if machine is not None:
        return db_module.resolve_machine_selector(conn, machine).machine_id
    rows = conn.execute(
        """
        SELECT DISTINCT origin_machine_id
        FROM usage_events
        WHERE harness = ?
          AND source_session_id = ?
          AND origin_machine_id IS NOT NULL
        ORDER BY origin_machine_id
        """,
        (harness, source_session_id),
    ).fetchall()
    if not rows:
        msg = f"No imported source session matched {harness}/{source_session_id}."
        raise ValueError(msg)
    if len(rows) > 1:
        labels = db_module.machine_label_map(conn)
        candidates = ", ".join(
            labels.get(str(row["origin_machine_id"]), str(row["origin_machine_id"]))
            for row in rows
        )
        msg = (
            f"Source session {harness}/{source_session_id} matched multiple machines: "
            f"{candidates}. Specify machine."
        )
        raise ValueError(msg)
    return str(rows[0]["origin_machine_id"])


def _resolve_session_key(
    conn: sqlite3.Connection,
    session_key: str,
) -> tuple[str, str, str]:
    parts = session_key.split("/", 2)
    if len(parts) != 3:
        msg = (
            "Session key must be machine/harness/source_session_id, "
            f"got {session_key!r}."
        )
        raise ValueError(msg)
    machine_selector, harness, source_session_id = parts
    machine_id = db_module.resolve_machine_selector(conn, machine_selector).machine_id
    return machine_id, harness.strip(), source_session_id.strip()


def assign_area_to_session(
    path: str,
    *,
    harness: str,
    source_session_id: str,
    machine: str | None = None,
    db_path: Path | None = None,
) -> AreaSessionAssignment:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        area = db_module.ensure_area(conn, path)
        machine_id = _resolve_assignment_machine_id(
            conn,
            harness=harness,
            source_session_id=source_session_id,
            machine=machine,
        )
        assignment = db_module.assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness=harness,
            source_session_id=source_session_id,
        )
        conn.commit()
    finally:
        conn.close()
    return _to_public_area_session_assignment(assignment)


def unassign_area_from_session(
    *,
    harness: str,
    source_session_id: str,
    machine: str | None = None,
    db_path: Path | None = None,
) -> None:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        machine_id = _resolve_assignment_machine_id(
            conn,
            harness=harness,
            source_session_id=source_session_id,
            machine=machine,
        )
        db_module.unassign_area_from_source_session(
            conn,
            origin_machine_id=machine_id,
            harness=harness,
            source_session_id=source_session_id,
        )
        conn.commit()
    finally:
        conn.close()


def resolve_area_selector(
    path_or_stable_id: str,
    *,
    db_path: Path | None = None,
) -> Area:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        area = db_module.resolve_area_selector(conn, path_or_stable_id)
    finally:
        conn.close()
    return _to_public_area(area)


def assign_area_to_session_key(
    path: str,
    session_key: str,
    *,
    db_path: Path | None = None,
) -> AreaSessionAssignment:
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        area = db_module.ensure_area(conn, path)
        machine_id, harness, source_session_id = _resolve_session_key(conn, session_key)
        assignment = db_module.assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness=harness,
            source_session_id=source_session_id,
        )
        conn.commit()
    finally:
        conn.close()
    return _to_public_area_session_assignment(assignment)


def list_area_sessions(
    *,
    db_path: Path | None = None,
    area: str | None = None,
    area_exact: bool = False,
    unassigned: bool = False,
    harness: str | None = None,
    machine_id: str | None = None,
    period: str | None = None,
    limit: int | None = 20,
    order: str = "desc",
) -> UsageSessionsReport:
    return usage_sessions_report(
        db_path,
        area=area,
        area_exact=area_exact,
        unassigned_area=unassigned,
        harness=harness,
        machine_id=machine_id,
        period=period,
        limit=limit,
        order=order,
    )


def bulk_assign_area(
    path: str,
    *,
    db_path: Path | None = None,
    area_filter: str | None = None,
    area_exact: bool = False,
    unassigned: bool = True,
    harness: str | None = None,
    machine_id: str | None = None,
    period: str | None = None,
    apply_changes: bool = False,
    overwrite: bool = False,
) -> dict[str, int]:
    report = usage_sessions_report(
        db_path,
        area=area_filter,
        area_exact=area_exact,
        unassigned_area=unassigned,
        harness=harness,
        machine_id=machine_id,
        period=period,
        limit=None,
        order="desc",
    )
    candidates = report.sessions
    assigned = 0
    skipped = 0
    if not apply_changes:
        return {"matched": len(candidates), "assigned": 0, "skipped": len(candidates)}
    conn, _ = _open_state_db(db_path)
    try:
        db_module.migrate(conn)
        area = db_module.ensure_area(conn, path)
        for session in candidates:
            if session.origin_machine_id is None:
                skipped += 1
                continue
            if session.area_id is not None and not overwrite:
                skipped += 1
                continue
            db_module.assign_area_to_source_session(
                conn,
                area_id=area.id,
                origin_machine_id=session.origin_machine_id,
                harness=session.harness,
                source_session_id=session.source_session_id,
            )
            assigned += 1
        conn.commit()
    finally:
        conn.close()
    return {"matched": len(candidates), "assigned": assigned, "skipped": skipped}


def usage_area_tree_report(
    *,
    db_path: Path | None = None,
    area: str | None = None,
    area_exact: bool = False,
    unassigned_area: bool = False,
    period: str | None = None,
    harness: str | None = None,
    machine_id: str | None = None,
) -> UsageAreasReport:
    return usage_areas_report(
        db_path,
        area=area,
        area_exact=area_exact,
        unassigned_area=unassigned_area,
        period=period,
        harness=harness,
        machine_id=machine_id,
    )


__all__ = [
    "assign_area_to_session_key",
    "assign_area_to_session",
    "bulk_assign_area",
    "clear_active_area",
    "create_area",
    "get_active_area",
    "get_active_area_status",
    "list_areas",
    "list_area_sessions",
    "resolve_area_selector",
    "set_active_area",
    "unassign_area_from_session",
    "usage_area_tree_report",
]
