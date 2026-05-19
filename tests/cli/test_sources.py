from __future__ import annotations

import json

from typer.testing import CliRunner

from tests.cli.helpers import (
    _toml_path_value,
    create_amp_source,
    create_codex_session_file,
    create_copilot_file,
    create_droid_source,
    create_goose_source_db,
    create_pi_session_file,
    create_source_db,
    write_jsonl_rows,
)
from toktrail.cli import app


def test_cli_opencode_sessions_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    result = runner.invoke(
        app,
        ["sources", "sessions", "opencode", "--source", str(source_db)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "ses-1" in result.output
    assert "1,500" in result.output
    assert "202" in result.output


def test_cli_sources_lists_filtered_source(tmp_path) -> None:
    runner = CliRunner()
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    result = runner.invoke(
        app,
        [
            "sources",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == [
        {
            "harness": "opencode",
            "source_path": str(source_db),
            "exists": True,
            "sessions": 1,
            "messages": 1,
            "tokens": 1500,
            "warning": "",
            "config_key": "opencode_db",
            "id_prefix": "opencode",
            "watch_subdirs": [],
            "file_based": True,
            "effective_roots": [str(source_db)],
        }
    ]


def test_cli_sources_reports_missing_configured_source(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "toktrail.toml"
    missing_db = tmp_path / "missing.db"
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]

[imports.sources]
opencode = "{_toml_path_value(missing_db)}"
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["--config", str(config_path), "sources", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["harness"] == "opencode"
    assert payload[0]["exists"] is False
    assert payload[0]["sessions"] == 0
    assert "OpenCode database not found" in payload[0]["warning"]


def test_cli_sessions_droid_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    source_path = tmp_path / "factory" / "sessions"
    create_droid_source(source_path)

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "droid",
            "--source",
            str(source_path),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Droid source session droid-1" in result.output
    assert "token usage:" in result.output


def test_cli_pi_sessions_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd" / "session.jsonl")

    result = runner.invoke(
        app,
        ["sources", "sessions", "pi", "--source", str(session_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "pi_ses_001" in result.output
    assert "150" in result.output
    assert "2026-" in result.output


def test_cli_sessions_codex_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)

    result = runner.invoke(
        app,
        ["sources", "sessions", "codex", "--source", str(codex_file)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "session-001" in result.output
    assert "130" in result.output
    assert "2026-" in result.output


def test_cli_sessions_code_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    code_file = tmp_path / "code" / "session-001.jsonl"
    create_codex_session_file(code_file)

    result = runner.invoke(
        app,
        ["sources", "sessions", "code", "--source", str(code_file)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "session-001" in result.output
    assert "130" in result.output
    assert "2026-" in result.output


def test_cli_sessions_amp_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    source_path = tmp_path / "amp" / "threads"
    create_amp_source(source_path)

    result = runner.invoke(
        app,
        ["sources", "sessions", "amp", "--source", str(source_path)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "thread-1" in result.output
    assert "120" in result.output
    assert "2026-" in result.output


def test_cli_sessions_copilot_lists_source_sessions(tmp_path) -> None:
    runner = CliRunner()
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    result = runner.invoke(
        app,
        ["sources", "sessions", "copilot", "--source", str(copilot_file)],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "conv-1" in result.output
    assert "105" in result.output


def test_cli_harness_first_sessions_are_removed(tmp_path) -> None:
    runner = CliRunner()
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd" / "session.jsonl")
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    commands = (
        ["opencode", "sessions", "--opencode-db", str(source_db)],
        ["pi", "sessions", "--source", str(session_dir)],
        ["copilot", "sessions", "--source", str(copilot_file)],
    )

    for args in commands:
        result = runner.invoke(app, args)
        assert result.exit_code != 0


def test_cli_sessions_pi_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd" / "session.jsonl")

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "pi",
            "--source",
            str(session_dir),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "claude-3-5-sonnet" in result.output


def test_cli_sessions_codex_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    codex_file = tmp_path / "codex" / "session-001.jsonl"
    create_codex_session_file(codex_file)

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "codex",
            "--source",
            str(codex_file),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "gpt-5.2-codex" in result.output


def test_cli_sessions_code_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    code_file = tmp_path / "code" / "session-001.jsonl"
    create_codex_session_file(code_file)

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "code",
            "--source",
            str(code_file),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "gpt-5.2-codex" in result.output


def test_cli_sessions_goose_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    goose_db = tmp_path / "goose" / "sessions.db"
    create_goose_source_db(goose_db)

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "goose",
            "--source",
            str(goose_db),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "claude-sonnet-4-20250514" in result.output


def test_cli_sessions_amp_breakdown_shows_token_columns(tmp_path) -> None:
    runner = CliRunner()
    source_path = tmp_path / "amp" / "threads"
    create_amp_source(source_path)

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "amp",
            "--source",
            str(source_path),
            "--last",
            "--breakdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Amp source session thread-1" in result.output
    assert "By model" in result.output
    assert "provider/model" in result.output
    assert "input" in result.output
    assert "claude-sonnet-4-0" in result.output


def test_cli_sessions_codex_supports_limit_sort_and_columns(tmp_path) -> None:
    runner = CliRunner()
    codex_dir = tmp_path / "codex"
    create_codex_session_file(codex_dir / "session-001.jsonl")
    write_jsonl_rows(
        codex_dir / "session-002.jsonl",
        [
            {
                "timestamp": "2026-01-01T00:00:02Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "model": "gpt-5.2-codex",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 200,
                            "output_tokens": 20,
                        }
                    },
                },
            }
        ],
    )

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "codex",
            "--source",
            str(codex_dir),
            "--sort",
            "tokens",
            "--limit",
            "1",
            "--columns",
            "source_session_id,total",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "total" in result.output
    assert "session-002" in result.output
    assert "session-001" not in result.output


def test_cli_sessions_pi_supports_limit_sort_and_columns(tmp_path) -> None:
    runner = CliRunner()
    session_dir = tmp_path / "sessions"
    create_pi_session_file(session_dir / "encoded-cwd-a" / "session-a.jsonl")
    write_jsonl_rows(
        session_dir / "encoded-cwd-b" / "session-b.jsonl",
        [
            {
                "type": "session",
                "id": "pi_ses_999",
                "timestamp": "2026-01-01T00:00:00.000Z",
                "cwd": "/tmp",
            },
            {
                "type": "message",
                "id": "msg_999",
                "parentId": None,
                "timestamp": "2026-01-01T00:00:02.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-3-5-sonnet",
                    "provider": "anthropic",
                    "usage": {
                        "input": 200,
                        "output": 100,
                        "cacheRead": 20,
                        "cacheWrite": 10,
                        "totalTokens": 330,
                    },
                },
            },
        ],
    )

    result = runner.invoke(
        app,
        [
            "sources",
            "sessions",
            "pi",
            "--source",
            str(session_dir),
            "--sort",
            "tokens",
            "--limit",
            "1",
            "--columns",
            "source_session_id,total",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "source_session_id" in result.output
    assert "total" in result.output
    assert "pi_ses_999" in result.output
    assert "pi_ses_001" not in result.output


def test_cli_sessions_copilot_supports_virtual_and_savings_sort(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    copilot_dir = tmp_path / "copilot"
    write_jsonl_rows(
        copilot_dir / "first.jsonl",
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
    write_jsonl_rows(
        copilot_dir / "second.jsonl",
        [
            {
                "type": "span",
                "traceId": "trace-2",
                "spanId": "span-2",
                "name": "chat claude-sonnet-4",
                "endTime": [1775934265, 967317833],
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.response.model": "claude-sonnet-4",
                    "gen_ai.conversation.id": "conv-2",
                    "gen_ai.usage.input_tokens": 300,
                    "gen_ai.usage.output_tokens": 10,
                },
            }
        ],
    )
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    for sort_value in ("virtual", "savings"):
        result = runner.invoke(
            app,
            [
                "--config",
                str(config_path),
                "sources",
                "sessions",
                "copilot",
                "--source",
                str(copilot_dir),
                "--sort",
                sort_value,
                "--limit",
                "1",
                "--columns",
                "source_session_id,actual,virtual,savings",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "actual" in result.output
        assert "virtual" in result.output
        assert "savings" in result.output
        assert "conv-2" in result.output
        assert "conv-1" not in result.output
