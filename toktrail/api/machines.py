from __future__ import annotations

import json
from pathlib import Path

from toktrail import db as db_module
from toktrail.api._common import _open_state_db
from toktrail.api._conversions import _to_public_machine
from toktrail.api.models import Machine
from toktrail.config import load_machine_config
from toktrail.paths import resolve_toktrail_machine_path


def machine_status(
    db_path: Path | None = None,
    *,
    config_path: Path | None = None,
) -> Machine:
    conn, _ = _open_state_db(db_path)
    try:
        local_machine_id = db_module.get_local_machine_id(conn)
        machine = db_module.get_machine(conn, local_machine_id)
    finally:
        conn.close()
    if machine is None:
        msg = "Local machine record not found."
        raise ValueError(msg)
    return _to_public_machine(machine)


def list_machines(db_path: Path | None = None) -> tuple[Machine, ...]:
    conn, _ = _open_state_db(db_path)
    try:
        machines = db_module.list_machines(conn)
    finally:
        conn.close()
    return tuple(_to_public_machine(machine) for machine in machines)


def set_machine_name(
    name: str,
    *,
    db_path: Path | None = None,
    machine_config_path: Path | None = None,
) -> Machine:
    cleaned_name = name.strip()
    if not cleaned_name:
        msg = "Machine name must not be empty."
        raise ValueError(msg)
    config_path = resolve_toktrail_machine_path(machine_config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "[machine]\nname = " + json.dumps(cleaned_name) + "\n",
        encoding="utf-8",
    )
    loaded = load_machine_config(config_path)
    conn, _ = _open_state_db(db_path)
    try:
        machine = db_module.apply_local_machine_config(conn, loaded.config)
        conn.commit()
    finally:
        conn.close()
    return _to_public_machine(machine)


def clear_machine_name(
    *,
    db_path: Path | None = None,
    machine_config_path: Path | None = None,
) -> Machine:
    config_path = resolve_toktrail_machine_path(machine_config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[machine]\n", encoding="utf-8")
    loaded = load_machine_config(config_path)
    conn, _ = _open_state_db(db_path)
    try:
        machine = db_module.apply_local_machine_config(conn, loaded.config)
        conn.commit()
    finally:
        conn.close()
    return _to_public_machine(machine)


__all__ = [
    "clear_machine_name",
    "list_machines",
    "machine_status",
    "set_machine_name",
]
