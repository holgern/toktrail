from __future__ import annotations

import pytest

from toktrail.api.environment import prepare_environment
from toktrail.errors import InvalidAPIUsageError


def test_prepare_environment_for_opencode_pi_and_codex_returns_empty_env(
    tmp_path,
) -> None:
    opencode_path = tmp_path / "opencode.db"
    pi_path = tmp_path / "sessions"
    codex_path = tmp_path / "codex-sessions"

    opencode_env = prepare_environment("opencode", source_path=opencode_path)
    pi_env = prepare_environment("pi", source_path=pi_path)
    codex_env = prepare_environment("codex", source_path=codex_path)

    assert opencode_env.source_path == opencode_path
    assert opencode_env.env == {}
    assert opencode_env.shell_exports == ()
    assert pi_env.source_path == pi_path
    assert pi_env.env == {}
    assert pi_env.shell_exports == ()
    assert codex_env.source_path == codex_path
    assert codex_env.env == {}
    assert codex_env.shell_exports == ()


@pytest.mark.parametrize(
    ("shell", "expected_prefix"),
    (
        ("bash", "export COPILOT_OTEL_ENABLED="),
        ("zsh", "export COPILOT_OTEL_ENABLED="),
        ("fish", "set -gx COPILOT_OTEL_ENABLED "),
        ("nu", "$env.COPILOT_OTEL_ENABLED = "),
        ("nushell", "$env.COPILOT_OTEL_ENABLED = "),
        ("powershell", "$env:COPILOT_OTEL_ENABLED = "),
        ("pwsh", "$env:COPILOT_OTEL_ENABLED = "),
    ),
)
def test_prepare_environment_for_copilot_supports_all_shells(
    tmp_path,
    shell: str,
    expected_prefix: str,
) -> None:
    otel_file = tmp_path / "copilot.jsonl"

    environment = prepare_environment("copilot", source_path=otel_file, shell=shell)

    assert environment.source_path == otel_file
    assert environment.env["COPILOT_OTEL_ENABLED"] == "true"
    assert environment.env["COPILOT_OTEL_FILE_EXPORTER_PATH"] == str(otel_file)
    assert environment.shell_exports[0].startswith(expected_prefix)


def test_prepare_environment_unknown_shell_raises(tmp_path) -> None:
    with pytest.raises(InvalidAPIUsageError, match="Unsupported shell"):
        prepare_environment("copilot", source_path=tmp_path / "otel.jsonl", shell="cmd")
