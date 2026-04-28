from __future__ import annotations

import json
import sqlite3
from pathlib import Path

VALID_ASSISTANT: dict[str, object] = {
    "id": "msg_123",
    "role": "assistant",
    "modelID": "claude-sonnet-4",
    "providerID": "anthropic",
    "cost": 0.05,
    "tokens": {
        "input": 1000,
        "output": 500,
        "reasoning": 100,
        "cache": {"read": 200, "write": 50},
    },
    "time": {"created": 1700000000000.0, "completed": 1700000000500.0},
    "mode": "build",
}


def create_opencode_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            data TEXT NOT NULL
        )
        """
    )
    return conn


def insert_message(
    conn: sqlite3.Connection,
    *,
    row_id: str,
    session_id: str,
    data: dict[str, object],
) -> None:
    conn.execute(
        "INSERT INTO message (id, session_id, data) VALUES (?, ?, ?)",
        (row_id, session_id, json.dumps(data)),
    )
