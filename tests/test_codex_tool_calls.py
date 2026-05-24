"""Tests for toktrail.adapters.codex_tool_calls."""

from __future__ import annotations

import json
from pathlib import Path

from toktrail.adapters.codex_tool_calls import (
    scan_codex_tool_calls,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def write_codex_tool_rows(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _success_command(
    call_id: str = "call_ok_1", command: str = "echo hi"
) -> dict[str, object]:
    return {
        "timestamp": "2026-05-24T05:41:18Z",
        "type": "event_msg",
        "payload": {
            "type": "exec_command",
            "call_id": call_id,
            "cwd": "/tmp/project",
            "command": command,
            "exit_code": 0,
            "duration_ms": 100,
            "stdout": "hi\n",
            "stderr": "",
        },
    }


def _failed_command(
    call_id: str = "call_bad_1", command: str = "rg -n missing toktrail"
) -> dict[str, object]:
    return {
        "timestamp": "2026-05-24T05:41:18Z",
        "type": "event_msg",
        "payload": {
            "type": "exec_command",
            "call_id": call_id,
            "cwd": "/tmp/project",
            "command": command,
            "exit_code": 2,
            "duration_ms": 183,
            "stderr": "No files were searched...",
            "stdout": "",
        },
    }


def _timeout_command(call_id: str = "call_timeout_1") -> dict[str, object]:
    return {
        "timestamp": "2026-05-24T05:41:22Z",
        "type": "event_msg",
        "payload": {
            "type": "exec_command",
            "call_id": call_id,
            "command": "pytest -q",
            "timed_out": True,
            "duration_ms": 300000,
        },
    }


def _paired_call(
    call_id: str = "call_paired",
    name: str = "exec_command",
    arguments: str = '{"cmd": "ls"}',
) -> dict[str, object]:
    return {
        "timestamp": "2026-05-24T05:41:18Z",
        "type": "response_item",
        "payload": {
            "item": {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            },
        },
    }


def _paired_result(
    call_id: str = "call_paired",
    exit_code: int = 4,
    output: str = "ERROR: file not found",
) -> dict[str, object]:
    return {
        "timestamp": "2026-05-24T05:41:20Z",
        "type": "response_item",
        "payload": {
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "exit_code": exit_code,
                "output": output,
            },
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScanCodexToolCallsExtractsFailure:
    """1. test_scan_codex_tool_calls_extracts_exec_command_failure"""

    def test_one_success_one_failure(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-1.jsonl"
        rows = [_success_command(), _failed_command()]
        write_codex_tool_rows(source, rows)

        result = scan_codex_tool_calls(source)

        assert result.tool_call_count == 2
        assert result.failure_count == 1
        assert result.timeout_count == 0
        bad = result.calls[1]
        assert bad.tool_name == "exec_command"
        assert bad.exit_code == 2
        assert bad.stderr_snippet is None  # not included by default
        assert bad.line_number >= 1
        assert bad.status == "failed"
        assert bad.failure_kind == "exit_code"


class TestScanCodexToolCallsExtractsTimeout:
    """2. test_scan_codex_tool_calls_extracts_timeout"""

    def test_timeout_status(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-t.jsonl"
        rows = [_timeout_command()]
        write_codex_tool_rows(source, rows)

        result = scan_codex_tool_calls(source, include_output=True)

        assert result.tool_call_count == 1
        assert result.timeout_count == 1
        assert result.failure_count == 1
        call = result.calls[0]
        assert call.status == "timeout"
        assert call.failure_kind == "timeout"


class TestScanCodexToolCallsPairsCallAndResult:
    """3. test_scan_codex_tool_calls_pairs_call_and_result_by_call_id"""

    def test_paired_call_and_result_merged(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-p.jsonl"
        rows = [
            _paired_call(call_id="call_p1", name="exec_command"),
            _paired_result(call_id="call_p1", exit_code=1, output="fail"),
        ]
        write_codex_tool_rows(source, rows)

        result = scan_codex_tool_calls(source, include_output=True)

        # Should merge into 1 call (paired call + result), not 2
        assert result.tool_call_count == 1
        call = result.calls[0]
        assert call.call_id == "call_p1"
        assert call.status == "failed"
        assert call.exit_code == 1
        assert call.tool_name == "exec_command"


class TestScanCodexToolCallsIgnoresTokenCountRows:
    """4. test_scan_codex_tool_calls_ignores_token_count_rows"""

    def test_token_count_not_counted(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-tc.jsonl"
        rows = [
            {
                "type": "turn.completed",
                "model": "gpt-5",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
            _success_command(),
        ]
        write_codex_tool_rows(source, rows)

        result = scan_codex_tool_calls(source)

        assert result.tool_call_count == 1
        assert result.failure_count == 0


class TestScanCodexToolCallsFiltersSourceSession:
    """5. test_scan_codex_tool_calls_filters_source_session"""

    def test_directory_scan_filtered(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "codex"
        write_codex_tool_rows(source_dir / "session-a.jsonl", [_success_command()])
        write_codex_tool_rows(source_dir / "session-b.jsonl", [_failed_command()])

        result_a = scan_codex_tool_calls(source_dir, source_session_id="session-a")
        assert result_a.tool_call_count == 1
        assert result_a.failure_count == 0

        result_b = scan_codex_tool_calls(source_dir, source_session_id="session-b")
        assert result_b.tool_call_count == 1
        assert result_b.failure_count == 1


class TestScanCodexToolCallsNoRawJsonByDefault:
    """6. test_scan_codex_tool_calls_no_raw_json_by_default"""

    def test_raw_json_none(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-rj.jsonl"
        write_codex_tool_rows(source, [_success_command()])

        result = scan_codex_tool_calls(source, include_raw_json=False)
        assert result.tool_call_count == 1
        call = result.calls[0]
        assert call.raw_json is None


class TestScanCodexToolCallsRawJsonOptIn:
    """7. test_scan_codex_tool_calls_raw_json_opt_in"""

    def test_raw_json_present_when_requested(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-rrj.jsonl"
        write_codex_tool_rows(source, [_success_command()])

        result = scan_codex_tool_calls(source, include_raw_json=True)
        assert result.tool_call_count == 1
        call = result.calls[0]
        assert call.raw_json is not None
        assert "exec_command" in call.raw_json


class TestScanCodexToolCallsMissingSource:
    """Additional edge case: missing source path."""

    def test_missing_source_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "codex" / "no-such-file.jsonl"
        result = scan_codex_tool_calls(missing)
        assert result.tool_call_count == 0
        assert result.files_seen == 0


class TestScanCodexToolCallsCancelled:
    """Verify cancelled status is classified correctly."""

    def test_cancelled_status(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-cancel.jsonl"
        row = {
            "timestamp": "2026-05-24T05:41:18Z",
            "type": "event_msg",
            "payload": {
                "type": "exec_command",
                "call_id": "call_cancel",
                "command": "long-task",
                "status": "cancelled",
            },
        }
        write_codex_tool_rows(source, [row])

        result = scan_codex_tool_calls(source)
        assert result.tool_call_count == 1
        call = result.calls[0]
        assert call.status == "cancelled"
        assert call.failure_kind == "cancelled"


class TestScanCodexToolCallsShowOutput:
    """Verify include_output=True surfaces stderr/stdout snippets."""

    def test_show_output_includes_stderr(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-so.jsonl"
        rows = [_failed_command()]
        write_codex_tool_rows(source, rows)

        result = scan_codex_tool_calls(source, include_output=True)
        call = result.calls[0]
        assert call.stderr_snippet == "No files were searched..."

    def test_hide_output_excludes_stderr(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-ho.jsonl"
        rows = [_failed_command()]
        write_codex_tool_rows(source, rows)

        result = scan_codex_tool_calls(source, include_output=False)
        call = result.calls[0]
        assert call.stderr_snippet is None


class TestScanCodexToolCallsDuration:
    """Verify duration_ms and completed_ms are extracted."""

    def test_duration_and_completed(self, tmp_path: Path) -> None:
        source = tmp_path / "codex" / "session-dur.jsonl"
        rows = [_success_command()]
        write_codex_tool_rows(source, rows)

        result = scan_codex_tool_calls(source)
        call = result.calls[0]
        assert call.duration_ms == 100
        assert call.completed_ms is not None
