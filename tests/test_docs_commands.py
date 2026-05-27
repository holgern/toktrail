from __future__ import annotations

import re
import shlex
from functools import cache
from pathlib import Path

import pytest
from typer.testing import CliRunner

from toktrail.cli import app

DOC_FILES = [
    Path("README.md"),
    Path("API.md"),
    Path("docs/usage.rst"),
    Path("docs/api_usage.rst"),
    Path("docs/harnesses.rst"),
]
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
GLOBAL_OPTIONS_WITH_VALUE = {
    "--db",
    "--config",
    "--prices",
    "--prices-dir",
    "--subscriptions",
}


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _iter_toktrail_commands(text: str) -> list[str]:
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("toktrail "):
            continue
        commands.append(stripped)
    return commands


def _extract_command_tokens(command_line: str) -> list[str]:
    parts = shlex.split(command_line)
    if not parts or parts[0] != "toktrail":
        return []
    args = list(parts[1:])
    while args and args[0].startswith("-"):
        option = args.pop(0)
        if option in GLOBAL_OPTIONS_WITH_VALUE and args:
            args.pop(0)
    return args


def _is_placeholder_or_path(token: str) -> bool:
    return (
        token == "--"
        or token.startswith("<")
        or token.startswith("/")
        or token.startswith("~")
    )


@cache
def _help_output_for(path: tuple[str, ...]) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(app, [*path, "--help"])
    return result.exit_code, _strip_ansi(result.output)


def _path_exists(path: tuple[str, ...]) -> bool:
    exit_code, _ = _help_output_for(path)
    return exit_code == 0


def _path_has_subcommands(path: tuple[str, ...]) -> bool:
    _, output = _help_output_for(path)
    return "Commands" in output


def _extract_command_path(command_line: str) -> list[str]:
    args = _extract_command_tokens(command_line)
    command_path: list[str] = []
    for token in args:
        if token.startswith("-") or _is_placeholder_or_path(token):
            break
        candidate = tuple([*command_path, token])
        if _path_exists(candidate):
            command_path.append(token)
            continue
        if command_path and _path_has_subcommands(tuple(command_path)):
            return []
        break
    return command_path


def _known_root_commands(runner: CliRunner) -> set[str]:
    help_text = _strip_ansi(runner.invoke(app, ["--help"]).output)
    roots = set(re.findall(r"(?m)^\s*│\s*([a-z][a-z0-9-]*)\b", help_text))
    if roots:
        return roots
    return set(re.findall(r"(?m)^\s{2,}([a-z][a-z0-9-]*)\s{2,}", help_text))


def test_docs_command_roots_exist() -> None:
    runner = CliRunner()
    known_roots = _known_root_commands(runner)
    for doc_path in DOC_FILES:
        commands = _iter_toktrail_commands(doc_path.read_text(encoding="utf-8"))
        for command in commands:
            path = _extract_command_path(command)
            if not path:
                continue
            assert path[0] in known_roots, (
                f"Invalid command root {path[0]!r} from {doc_path}: {command}"
            )
            result = runner.invoke(app, [*path, "--help"])
            assert result.exit_code == 0, (
                f"Invalid command path {' '.join(path)!r} from {doc_path}: {command}"
            )


def test_docs_have_no_stale_root_start_stop_status_examples() -> None:
    stale_pattern = re.compile(r"^toktrail (start|stop|status)(?:\s|$)")
    for doc_path in DOC_FILES:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
        stale = [line.strip() for line in lines if stale_pattern.match(line.strip())]
        assert not stale, f"Stale root command examples in {doc_path}: {stale}"


@pytest.mark.parametrize(
    "line",
    [
        "toktrail source-session show --harness pi pi_ses_001",
        "toktrail source-sessions --harness pi",
        "toktrail run runs",
        "toktrail sessions",
        "toktrail config prices",
    ],
)
def test_stale_command_examples_are_invalid(line: str) -> None:
    assert _extract_command_path(line) == []


def test_docs_list_all_supported_harnesses() -> None:
    from toktrail.api.harnesses import supported_harnesses

    expected = {h.name for h in supported_harnesses()}
    paths = [Path("README.md"), Path("docs/usage.rst"), Path("docs/harnesses.rst")]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        missing = sorted(name for name in expected if name not in text)
        assert not missing, f"{path} missing supported harnesses: {missing}"


def test_config_default_harnesses_covers_all_registry_entries() -> None:
    """The [imports].harnesses example in README.md and docs/config.rst must
    include every harness from the registry."""
    from toktrail.api.harnesses import supported_harnesses

    expected = {h.name for h in supported_harnesses()}
    paths = [Path("README.md"), Path("docs/config.rst")]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        # Look for the harnesses list in config examples (either TOML or inline)
        missing = sorted(name for name in expected if name not in text)
        assert not missing, f"{path} missing harnesses in config examples: {missing}"


def test_harnesses_rst_matrix_covers_all_11_readers() -> None:
    """docs/harnesses.rst reader matrix table must mention all 11 registry
    names by their short name and display name."""
    from toktrail.api.harnesses import supported_harnesses

    text = Path("docs/harnesses.rst").read_text(encoding="utf-8")
    for h in supported_harnesses():
        assert h.name in text, f"docs/harnesses.rst missing harness name {h.name!r}"
        # Display name should also appear (case-insensitive, ignoring spaces)
        display_normalized = h.display_name.lower().replace(" ", "")
        text_normalized = text.lower().replace(" ", "")
        assert display_normalized in text_normalized, (
            f"docs/harnesses.rst missing display name {h.display_name!r}"
        )


def test_provider_aliases_docs_coverage() -> None:
    """Provider aliases must be documented in docs/config.rst and API.md."""
    config_text = Path("docs/config.rst").read_text(encoding="utf-8")
    api_text = Path("API.md").read_text(encoding="utf-8")

    assert "provider_aliases" in config_text, (
        "docs/config.rst missing provider_aliases documentation"
    )
    assert "provider_aliases" in api_text, (
        "API.md missing provider_aliases documentation"
    )

    # Verify example form: key = value pattern in config.rst
    assert "ocgo-launch = " in config_text, (
        "docs/config.rst should include a provider_aliases TOML example"
    )
