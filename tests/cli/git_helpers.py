from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

from typer.testing import CliRunner

from tests.cli.helpers import (
    create_source_db,
)
from toktrail.cli import app

HAS_GIT = which("git") is not None


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_output(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _configure_git_identity(repo_path: Path) -> None:
    _run_git(repo_path, "config", "user.name", "Toktrail Tests")
    _run_git(repo_path, "config", "user.email", "toktrail-tests@example.com")


def _init_git_sync_repo_via_cli(tmp_path: Path) -> tuple[CliRunner, Path, Path]:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    repo = tmp_path / "toktrail-state"
    create_source_db(source_db)

    init_result = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init_result.exit_code == 0, init_result.output
    refresh_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--no-run",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )
    assert refresh_result.exit_code == 0, refresh_result.output
    repo_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "git",
            "init",
            "--repo",
            str(repo),
            "--no-hooks",
            "--no-import-existing",
        ],
    )
    assert repo_result.exit_code == 0, repo_result.output
    _configure_git_identity(repo)
    export_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "git",
            "export-local",
            "--repo",
            str(repo),
            "--no-refresh",
        ],
    )
    assert export_result.exit_code == 0, export_result.output
    return runner, state_db, repo
