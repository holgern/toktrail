from __future__ import annotations

from dataclasses import dataclass

_BAD_STATUSES = frozenset({"failed", "timeout", "cancelled"})


@dataclass(frozen=True)
class ToolCallRow:
    """A single tool call in a session tool-call report."""

    ordinal: int
    tool_name: str
    status: str
    source_path: str
    line_number: int
    created_ms: int | None = None
    completed_ms: int | None = None
    call_id: str | None = None
    cwd: str | None = None
    command: str | None = None
    arguments: dict[str, object] | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    stdout_snippet: str | None = None
    stderr_snippet: str | None = None

    @property
    def is_bad(self) -> bool:
        return self.status in _BAD_STATUSES

    def as_dict(
        self,
        *,
        include_output: bool = False,
        include_raw_json: bool = False,
        raw_json: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "ordinal": self.ordinal,
            "tool_name": self.tool_name,
            "status": self.status,
            "source_path": self.source_path,
            "line_number": self.line_number,
        }
        if self.created_ms is not None:
            payload["created_ms"] = self.created_ms
        if self.completed_ms is not None:
            payload["completed_ms"] = self.completed_ms
        if self.call_id is not None:
            payload["call_id"] = self.call_id
        if self.cwd is not None:
            payload["cwd"] = self.cwd
        if self.command is not None:
            payload["command"] = self.command
        if self.arguments is not None:
            payload["arguments"] = self.arguments
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        if self.error is not None:
            payload["error"] = self.error
        if include_output:
            if self.stdout_snippet is not None:
                payload["stdout_snippet"] = self.stdout_snippet
            if self.stderr_snippet is not None:
                payload["stderr_snippet"] = self.stderr_snippet
        if include_raw_json and raw_json is not None:
            payload["raw_json"] = raw_json
        return payload


@dataclass(frozen=True)
class SessionToolCallReport:
    """Report for tool-call analysis of a single source session."""

    harness: str
    source_session_id: str
    source_paths: tuple[str, ...]
    tool_call_count: int
    failure_count: int
    timeout_count: int
    calls: tuple[ToolCallRow, ...]
    warnings: tuple[str, ...] = ()

    @property
    def bad_calls(self) -> tuple[ToolCallRow, ...]:
        return tuple(row for row in self.calls if row.is_bad)

    def as_dict(
        self,
        *,
        include_calls: bool = True,
        include_output: bool = False,
        include_raw_json: bool = False,
        raw_json_by_ordinal: dict[int, str] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "session_tool_calls",
            "harness": self.harness,
            "source_session_id": self.source_session_id,
            "source_paths": list(self.source_paths),
            "tool_call_count": self.tool_call_count,
            "failure_count": self.failure_count,
            "timeout_count": self.timeout_count,
        }
        if include_calls:
            bad = self.bad_calls
            raw_map = raw_json_by_ordinal or {}
            payload["bad_calls"] = [
                row.as_dict(
                    include_output=include_output,
                    include_raw_json=include_raw_json,
                    raw_json=raw_map.get(row.ordinal),
                )
                for row in bad
            ]
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload


__all__ = ["ToolCallRow", "SessionToolCallReport"]
