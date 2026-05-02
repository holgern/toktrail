from __future__ import annotations

import re
import shlex
from pathlib import Path

from typer.testing import CliRunner

from toktrail.cli import app

DOC_FILES = [
    Path("README.md"),
    Path("API.md"),
    Path("docs/usage.rst"),
]
GLOBAL_OPTIONS_WITH_VALUE = {"--db", "--config"}


def _iter_toktrail_commands(text: str) -> list[str]:
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("toktrail "):
            continue
        commands.append(stripped)
    return commands


def _extract_root_command(command_line: str) -> str | None:
    parts = shlex.split(command_line)
    if not parts or parts[0] != "toktrail":
        return None
    args = list(parts[1:])
    while args and args[0].startswith("-"):
        option = args.pop(0)
        if option in GLOBAL_OPTIONS_WITH_VALUE and args:
            args.pop(0)
    if not args:
        return None
    return args[0]


def _known_root_commands(runner: CliRunner) -> set[str]:
    help_text = runner.invoke(app, ["--help"]).output
    return set(re.findall(r"^\s{2}([a-z][a-z0-9-]*)\s", help_text, flags=re.MULTILINE))


def test_docs_command_roots_exist() -> None:
    runner = CliRunner()
    known_roots = _known_root_commands(runner)
    for doc_path in DOC_FILES:
        commands = _iter_toktrail_commands(doc_path.read_text(encoding="utf-8"))
        for command in commands:
            root = _extract_root_command(command)
            if root is None or root not in known_roots:
                continue
            result = runner.invoke(app, [root, "--help"])
            assert result.exit_code == 0, (
                f"Invalid command root {root!r} from {doc_path}: {command}"
            )


def test_docs_have_no_stale_root_start_stop_status_examples() -> None:
    stale_prefixes = ("toktrail start", "toktrail stop", "toktrail status")
    for doc_path in DOC_FILES:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
        stale = [
            line.strip() for line in lines if line.strip().startswith(stale_prefixes)
        ]
        assert not stale, f"Stale root command examples in {doc_path}: {stale}"
