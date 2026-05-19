from __future__ import annotations

import json
import shlex
import subprocess

from typer.testing import CliRunner

from toktrail.cli import app


def test_cli_copilot_run_sets_otel_environment(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        env: dict[str, str],
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("toktrail.cli.subprocess.run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(app, ["copilot", "run", "--no-import", "--", "echo", "hi"])

    assert result.exit_code == 0, result.output
    assert captured["command"] == ["echo", "hi"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["COPILOT_OTEL_ENABLED"] == "true"
    assert env["COPILOT_OTEL_EXPORTER_TYPE"] == "file"
    assert env["COPILOT_OTEL_FILE_EXPORTER_PATH"].endswith(".jsonl")
    assert env["TOKTRAIL_COPILOT_FILE"] == env["COPILOT_OTEL_FILE_EXPORTER_PATH"]
    assert "Copilot OTEL file:" in result.output


def test_cli_copilot_env_outputs_shell_exports(tmp_path) -> None:
    runner = CliRunner()
    otel_file = tmp_path / "otel dir" / "copilot file.jsonl"
    otel_file_str = str(otel_file)

    expected_lines = {
        "bash": [
            "export COPILOT_OTEL_ENABLED=true",
            "export COPILOT_OTEL_EXPORTER_TYPE=file",
            f"export COPILOT_OTEL_FILE_EXPORTER_PATH={shlex.quote(otel_file_str)}",
            f"export TOKTRAIL_COPILOT_FILE={shlex.quote(otel_file_str)}",
        ],
        "zsh": [
            "export COPILOT_OTEL_ENABLED=true",
            "export COPILOT_OTEL_EXPORTER_TYPE=file",
            f"export COPILOT_OTEL_FILE_EXPORTER_PATH={shlex.quote(otel_file_str)}",
            f"export TOKTRAIL_COPILOT_FILE={shlex.quote(otel_file_str)}",
        ],
        "fish": [
            "set -gx COPILOT_OTEL_ENABLED 'true'",
            "set -gx COPILOT_OTEL_EXPORTER_TYPE 'file'",
            f"set -gx COPILOT_OTEL_FILE_EXPORTER_PATH '{otel_file_str}'",
            f"set -gx TOKTRAIL_COPILOT_FILE '{otel_file_str}'",
        ],
        "nu": [
            '$env.COPILOT_OTEL_ENABLED = "true"',
            '$env.COPILOT_OTEL_EXPORTER_TYPE = "file"',
            f"$env.COPILOT_OTEL_FILE_EXPORTER_PATH = {json.dumps(otel_file_str)}",
            f"$env.TOKTRAIL_COPILOT_FILE = {json.dumps(otel_file_str)}",
        ],
        "powershell": [
            "$env:COPILOT_OTEL_ENABLED = 'true'",
            "$env:COPILOT_OTEL_EXPORTER_TYPE = 'file'",
            f"$env:COPILOT_OTEL_FILE_EXPORTER_PATH = '{otel_file_str}'",
            f"$env:TOKTRAIL_COPILOT_FILE = '{otel_file_str}'",
        ],
    }

    for shell, lines in expected_lines.items():
        result = runner.invoke(
            app,
            ["copilot", "env", shell, "--otel-file", otel_file_str],
        )
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == lines


def test_cli_copilot_env_accepts_shell_aliases(tmp_path) -> None:
    runner = CliRunner()
    otel_file = tmp_path / "copilot.jsonl"
    otel_file_str = str(otel_file)

    result_nushell = runner.invoke(
        app,
        ["copilot", "env", "nushell", "--otel-file", otel_file_str],
    )
    result_nu = runner.invoke(
        app,
        ["copilot", "env", "nu", "--otel-file", otel_file_str],
    )
    assert result_nushell.exit_code == 0, result_nushell.output
    assert result_nu.exit_code == 0, result_nu.output
    assert result_nushell.output == result_nu.output

    result_pwsh = runner.invoke(
        app,
        ["copilot", "env", "pwsh", "--otel-file", otel_file_str],
    )
    result_powershell = runner.invoke(
        app,
        ["copilot", "env", "powershell", "--otel-file", otel_file_str],
    )
    assert result_pwsh.exit_code == 0, result_pwsh.output
    assert result_powershell.exit_code == 0, result_powershell.output
    assert result_pwsh.output == result_powershell.output


def test_cli_copilot_env_rejects_unknown_shell() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["copilot", "env", "csh"])

    assert result.exit_code == 1
    assert "Unsupported shell. Use bash, zsh, fish, nu, or powershell." in result.output


def test_cli_copilot_env_json_outputs_valid_json(tmp_path) -> None:
    runner = CliRunner()
    otel_file = tmp_path / "copilot.jsonl"
    otel_file_str = str(otel_file)

    for shell in ("nu", "bash", "fish", "powershell"):
        result = runner.invoke(
            app,
            ["copilot", "env", shell, "--otel-file", otel_file_str, "--json"],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert set(parsed.keys()) == {
            "COPILOT_OTEL_ENABLED",
            "COPILOT_OTEL_EXPORTER_TYPE",
            "COPILOT_OTEL_FILE_EXPORTER_PATH",
            "TOKTRAIL_COPILOT_FILE",
        }
        assert parsed["COPILOT_OTEL_ENABLED"] == "true"
        assert parsed["COPILOT_OTEL_EXPORTER_TYPE"] == "file"
        assert parsed["COPILOT_OTEL_FILE_EXPORTER_PATH"] == otel_file_str
        assert parsed["TOKTRAIL_COPILOT_FILE"] == otel_file_str


def test_cli_copilot_env_json_does_not_affect_default_output(tmp_path) -> None:
    runner = CliRunner()
    otel_file = tmp_path / "copilot.jsonl"
    otel_file_str = str(otel_file)

    result = runner.invoke(
        app,
        ["copilot", "env", "nu", "--otel-file", otel_file_str],
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("$env.COPILOT_OTEL_ENABLED")
    # Must not be valid JSON object output
    assert not result.output.strip().startswith("{")
