from __future__ import annotations

import sqlite3
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _open_state_db
from toktrail.api._conversions import (
    _to_public_area,
    _to_public_area_session_assignment,
)
from toktrail.api.models import Area, AreaSessionAssignment


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


def set_active_area(
    path: str,
    *,
    db_path: Path | None = None,
    create: bool = True,
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
        db_module.set_active_area(conn, area.id)
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
        msg = (
            "No imported source session matched "
            f"{harness}/{source_session_id}."
        )
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


__all__ = [
    "assign_area_to_session",
    "clear_active_area",
    "create_area",
    "get_active_area",
    "list_areas",
    "set_active_area",
    "unassign_area_from_session",
]
