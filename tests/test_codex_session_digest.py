from __future__ import annotations

import json
from pathlib import Path

from toktrail.session_digests import (
    extract_codex_session_events,
    summarize_tool_health,
)


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_codex_session_digest_extracts_failed_command(tmp_path: Path) -> None:
    source = tmp_path / "sessions" / "codex_ses_1.jsonl"
    _write_rows(
        source,
        [
            {
                "type": "turn.completed",
                "model": "gpt-5",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command",
                    "cmd": "pytest tests/test_codex_session_digest.py",
                    "exit_code": 1,
                    "stderr": "failed",
                    "path": "tests/test_codex_session_digest.py",
                },
            },
        ],
    )

    events = extract_codex_session_events(
        tmp_path / "sessions",
        source_session_id="codex_ses_1",
    )
    health = summarize_tool_health(events)

    assert len(events) == 1
    assert events[0].kind == "error"
    assert events[0].name == "shell"
    assert health.tool_call_count == 1
    assert health.tool_failure_count == 1
    assert health.failed_tools == {"shell": 1}
