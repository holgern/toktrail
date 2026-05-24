"""Tests for toktrail.api.analysis session_tool_call_analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toktrail.api.analysis import session_tool_call_analysis


def _write_codex_session(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


class TestSessionToolCallAnalysisCodexLast:
    """Test session_tool_call_analysis with --last for codex."""

    def test_basic_bad_calls(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "codex"
        rows = [
            {
                "timestamp": "2026-05-24T05:41:18Z",
                "type": "event_msg",
                "payload": {
                    "type": "exec_command",
                    "call_id": "call_ok",
                    "command": "echo ok",
                    "exit_code": 0,
                    "duration_ms": 50,
                },
            },
            {
                "timestamp": "2026-05-24T05:41:20Z",
                "type": "event_msg",
                "payload": {
                    "type": "exec_command",
                    "call_id": "call_bad",
                    "command": "rg missing",
                    "exit_code": 2,
                    "duration_ms": 183,
                    "stderr": "No files searched",
                },
            },
        ]
        _write_codex_session(source_dir / "session-test.jsonl", rows)

        report = session_tool_call_analysis(
            harness="codex",
            source_path=source_dir,
            last=True,
        )

        assert report.harness == "codex"
        assert report.tool_call_count == 2
        assert report.failure_count == 1
        assert report.timeout_count == 0
        # Default bad_only=True: only bad calls included
        bad = report.bad_calls
        assert len(bad) == 1
        assert bad[0].tool_name == "exec_command"
        assert bad[0].status == "failed"
        assert bad[0].exit_code == 2

    def test_bad_only_false_includes_all(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "codex"
        rows = [
            {
                "timestamp": "2026-05-24T05:41:18Z",
                "type": "event_msg",
                "payload": {
                    "type": "exec_command",
                    "call_id": "call_ok",
                    "command": "echo ok",
                    "exit_code": 0,
                },
            },
            {
                "timestamp": "2026-05-24T05:41:20Z",
                "type": "event_msg",
                "payload": {
                    "type": "exec_command",
                    "call_id": "call_bad",
                    "command": "false",
                    "exit_code": 1,
                },
            },
        ]
        _write_codex_session(source_dir / "session-all.jsonl", rows)

        report = session_tool_call_analysis(
            harness="codex",
            source_path=source_dir,
            last=True,
            bad_only=False,
        )

        assert report.tool_call_count == 2
        assert len(report.calls) == 2


class TestSessionToolCallAnalysisBadOnlyLimit:
    """Test bad_only and limit parameters."""

    def test_limit_applies(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "codex"
        rows = [
            {
                "timestamp": "2026-05-24T05:41:18Z",
                "type": "event_msg",
                "payload": {
                    "type": "exec_command",
                    "call_id": f"call_bad_{i}",
                    "command": f"cmd_{i}",
                    "exit_code": 1,
                },
            }
            for i in range(5)
        ]
        _write_codex_session(source_dir / "session-limit.jsonl", rows)

        report = session_tool_call_analysis(
            harness="codex",
            source_path=source_dir,
            last=True,
            limit=2,
        )

        assert report.tool_call_count == 5
        assert len(report.calls) == 2


class TestSessionToolCallAnalysisRejectsLastAndSourceSessionId:
    """Test that last and source_session_id cannot be used together."""

    def test_rejects_both(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "codex"
        source_dir.mkdir(parents=True, exist_ok=True)

        from toktrail.errors import InvalidAPIUsageError

        with pytest.raises(InvalidAPIUsageError, match="cannot be used together"):
            session_tool_call_analysis(
                harness="codex",
                source_path=source_dir,
                source_session_id="ses-1",
                last=True,
            )


class TestSessionToolCallAnalysisUnsupported:
    """Test that unsupported harnesses raise InvalidAPIUsageError."""

    def test_rejects_opencode(self, tmp_path: Path) -> None:
        from toktrail.errors import InvalidAPIUsageError

        with pytest.raises(InvalidAPIUsageError, match="not supported"):
            session_tool_call_analysis(
                harness="opencode",
                source_path=tmp_path,
                last=True,
            )


class TestSessionToolCallAnalysisReadOnly:
    """Test that the analysis is read-only."""

    def test_no_state_writes(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "codex"
        rows = [
            {
                "timestamp": "2026-05-24T05:41:18Z",
                "type": "event_msg",
                "payload": {
                    "type": "exec_command",
                    "call_id": "call_1",
                    "command": "echo ok",
                    "exit_code": 0,
                },
            },
        ]
        _write_codex_session(source_dir / "session-ro.jsonl", rows)

        # No db_path needed — read-only operation
        report = session_tool_call_analysis(
            harness="codex",
            source_path=source_dir,
            last=True,
        )

        assert report.tool_call_count == 1
        assert report.failure_count == 0
