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


def write_jsonl_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )


def create_copilot_file(path: Path) -> None:
    write_jsonl_rows(
        path,
        [
            {
                "type": "span",
                "traceId": "trace-1",
                "spanId": "span-1",
                "name": "chat claude-sonnet-4",
                "endTime": [1775934264, 967317833],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.response.model": "claude-sonnet-4",
                    "gen_ai.conversation.id": "conv-1",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 5,
                },
            }
        ],
    )


def create_pi_session_file(path: Path) -> None:
    write_jsonl_rows(
        path,
        [
            {
                "type": "session",
                "id": "pi_ses_001",
                "timestamp": "2026-01-01T00:00:00.000Z",
                "cwd": "/tmp",
            },
            {
                "type": "message",
                "id": "msg_001",
                "parentId": None,
                "timestamp": "2026-01-01T00:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-3-5-sonnet",
                    "provider": "anthropic",
                    "usage": {
                        "input": 100,
                        "output": 50,
                        "cacheRead": 10,
                        "cacheWrite": 5,
                        "totalTokens": 165,
                    },
                },
            },
        ],
    )


def create_codex_session_file(path: Path) -> None:
    write_jsonl_rows(
        path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "source": "interactive",
                    "model_provider": "openai",
                    "agent_nickname": "builder",
                },
            },
            {
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5.2-codex",
                },
            },
            {
                "timestamp": "2099-01-01T00:00:01Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 120,
                            "cached_input_tokens": 20,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 5,
                        },
                        "last_token_usage": {
                            "input_tokens": 120,
                            "cached_input_tokens": 20,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 5,
                        },
                    },
                },
            },
        ],
    )
