from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.cli.git_helpers import (
    HAS_GIT,
    _configure_git_identity,
    _git_output,
    _init_git_sync_repo_via_cli,
    _run_git,
)
from tests.cli.helpers import (
    _toml_path_value,
    create_source_db,
)
from toktrail.cli import app


def test_cli_sync_export_and_import_dry_run_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    import_db = tmp_path / "toktrail-import.db"
    source_db = tmp_path / "opencode.db"
    archive_path = tmp_path / "state.tar.gz"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
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

    export_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "export",
            "--out",
            str(archive_path),
            "--no-refresh",
        ],
    )
    assert export_result.exit_code == 0, export_result.output
    assert archive_path.exists()

    import_result = runner.invoke(
        app,
        [
            "--db",
            str(import_db),
            "sync",
            "import",
            str(archive_path),
            "--dry-run",
            "--json",
        ],
    )
    assert import_result.exit_code == 0, import_result.output
    payload = json.loads(import_result.output)
    assert payload["dry_run"] is True
    assert "runs_inserted" in payload
    assert "source_sessions_inserted" in payload
    assert "usage_events_inserted" in payload
    assert "usage_events_skipped" in payload
    assert "run_events_inserted" in payload
    assert "conflicts" in payload


def test_cli_sync_import_does_not_auto_export_git_state(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    import_db = tmp_path / "toktrail-import.db"
    source_db = tmp_path / "opencode.db"
    archive_path = tmp_path / "state.tar.gz"
    config_path = tmp_path / "config.toml"
    repo = tmp_path / "toktrail-state"
    create_source_db(source_db)
    config_path.write_text(
        f"""
config_version = 1

[sync.git]
repo = "{_toml_path_value(repo)}"
auto_push = true
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
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

    export_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "export",
            "--out",
            str(archive_path),
            "--no-refresh",
        ],
    )
    assert export_result.exit_code == 0, export_result.output

    def fail_export(*args, **kwargs):
        raise AssertionError("sync import must not export git state")

    monkeypatch.setattr("toktrail.cli_sync.export_repo_archive", fail_export)

    import_result = runner.invoke(
        app,
        [
            "--db",
            str(import_db),
            "--config",
            str(config_path),
            "sync",
            "import",
            str(archive_path),
            "--json",
        ],
    )
    assert import_result.exit_code == 0, import_result.output
    payload = json.loads(import_result.output)
    assert payload["usage_events_inserted"] > 0


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_init_status(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    repo = tmp_path / "toktrail-state"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
config_version = 1

[sync.git]
repo = "{_toml_path_value(repo)}"
""".strip(),
        encoding="utf-8",
    )

    init_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "sync",
            "git",
            "init",
        ],
    )
    assert init_result.exit_code == 0, init_result.output

    hooks_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "sync",
            "git",
            "hooks",
            "status",
            "--json",
        ],
    )
    assert hooks_result.exit_code == 0, hooks_result.output
    hooks_payload = json.loads(hooks_result.output)
    assert hooks_payload["hooks"]["post-merge"] == "installed"

    status_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "sync",
            "git",
            "status",
        ],
    )
    assert status_result.exit_code == 0, status_result.output
    assert "Git sync status" in status_result.output
    assert "pending imports:" in status_result.output
    assert (repo / "meta" / "format.json").exists()


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_cleanup_analysis_json_shape(tmp_path: Path) -> None:
    runner, _, repo = _init_git_sync_repo_via_cli(tmp_path)
    archive_file = repo / "archives" / "old.tar.gz"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(b"archive")

    result = runner.invoke(
        app,
        [
            "sync",
            "git",
            "cleanup",
            "--repo",
            str(repo),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "analysis"
    assert payload["repo_path"] == str(repo)
    assert set(payload["sizes"]) == {"git", "state", "obsolete"}
    assert "recommendation" in payload


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_cleanup_worktree_human_output(tmp_path: Path) -> None:
    runner, _, repo = _init_git_sync_repo_via_cli(tmp_path)
    archive_file = repo / "archives" / "old.tar.gz"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(b"archive")

    result = runner.invoke(
        app,
        [
            "sync",
            "git",
            "cleanup",
            "--repo",
            str(repo),
            "--working-tree",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Git sync cleanup" in result.output
    assert "mode: working-tree" in result.output
    assert "removed paths:" in result.output


def test_cli_sync_git_cleanup_modes_are_mutually_exclusive(tmp_path: Path) -> None:
    runner = CliRunner()
    repo = tmp_path / "repo"

    result = runner.invoke(
        app,
        [
            "sync",
            "git",
            "cleanup",
            "--repo",
            str(repo),
            "--working-tree",
            "--reset-history",
        ],
    )

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_cli_sync_git_cleanup_reset_requires_force(tmp_path: Path) -> None:
    runner = CliRunner()
    repo = tmp_path / "repo"

    result = runner.invoke(
        app,
        [
            "sync",
            "git",
            "cleanup",
            "--repo",
            str(repo),
            "--reset-history",
        ],
    )

    assert result.exit_code != 0
    assert "--force" in result.output


def test_cli_sync_git_cleanup_prints_reclone_warning_after_push(
    tmp_path: Path, monkeypatch
) -> None:
    runner = CliRunner()
    repo = tmp_path / "repo"

    class _FakeResult:
        def as_dict(self) -> dict[str, object]:
            return {
                "repo_path": str(repo),
                "mode": "reset-history",
                "dry_run": False,
                "removed_paths": ["archives/old.tar.gz"],
                "backup_bundle": str(tmp_path / "backup.bundle"),
                "committed": True,
                "commit_hash": "abc123",
                "pushed": True,
                "before_git_size": {"size-pack": "10.0 MiB"},
                "after_git_size": {"size-pack": "1.0 MiB"},
            }

    monkeypatch.setattr(
        "toktrail.cli_sync.reset_git_sync_history",
        lambda *args, **kwargs: _FakeResult(),
    )

    result = runner.invoke(
        app,
        [
            "sync",
            "git",
            "cleanup",
            "--repo",
            str(repo),
            "--reset-history",
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "reclone or hard-reset" in result.output


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_sync_json_shape(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    repo = tmp_path / "toktrail-state"
    remote = tmp_path / "remote.git"
    create_source_db(source_db)
    _run_git(tmp_path, "init", "--bare", str(remote))

    runner.invoke(app, ["--db", str(state_db), "init"])
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

    init_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "git",
            "init",
            "--repo",
            str(repo),
            "--remote",
            str(remote),
            "--branch",
            "main",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    _configure_git_identity(repo)

    sync_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "git",
            "sync",
            "--repo",
            str(repo),
            "--no-refresh",
            "--json",
        ],
    )
    assert sync_result.exit_code == 0, sync_result.output
    payload = json.loads(sync_result.output)
    assert payload["repo_path"] == str(repo)
    assert set(payload["pull"]) == {
        "state_files_seen",
        "state_imported",
        "state_skipped",
    }
    assert payload["push"] is not None
    assert set(payload["push"]) == {"state_path", "committed", "pushed", "commit_hash"}


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_push_no_refresh(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    repo = tmp_path / "toktrail-state"
    remote = tmp_path / "remote.git"
    create_source_db(source_db)
    _run_git(tmp_path, "init", "--bare", str(remote))

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
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
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "git",
            "init",
            "--repo",
            str(repo),
            "--remote",
            str(remote),
            "--branch",
            "main",
        ],
    )
    _configure_git_identity(repo)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "git",
            "push",
            "--repo",
            str(repo),
            "--no-refresh",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["committed"] is True
    assert payload["pushed"] is True
    assert "state" in payload["state_path"]


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_import_local_does_not_call_git_pull(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    producer_db = tmp_path / "producer.db"
    consumer_db = tmp_path / "consumer.db"
    source_db = tmp_path / "opencode.db"
    repo = tmp_path / "toktrail-state"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(producer_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(producer_db),
            "refresh",
            "--no-run",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )
    init_result = runner.invoke(
        app,
        [
            "--db",
            str(producer_db),
            "sync",
            "git",
            "init",
            "--repo",
            str(repo),
            "--no-hooks",
            "--no-import-existing",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    _configure_git_identity(repo)
    export_result = runner.invoke(
        app,
        [
            "--db",
            str(producer_db),
            "sync",
            "git",
            "export-local",
            "--repo",
            str(repo),
            "--no-refresh",
        ],
    )
    assert export_result.exit_code == 0, export_result.output

    def _unexpected_git_pull(*args, **kwargs):
        raise AssertionError("git_pull should not be called by import-local")

    monkeypatch.setattr("toktrail.cli_sync.git_pull", _unexpected_git_pull)

    result = runner.invoke(
        app,
        [
            "--db",
            str(consumer_db),
            "sync",
            "git",
            "import-local",
            "--repo",
            str(repo),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["state_files_seen"] >= 1
    assert payload["state_imported"] is True


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_export_local_no_refresh_does_not_push(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    repo = tmp_path / "toktrail-state"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
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
    init_result = runner.invoke(
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
    assert init_result.exit_code == 0, init_result.output
    _configure_git_identity(repo)

    result = runner.invoke(
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
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["committed"] is True
    assert payload["pushed"] is False


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_refresh_does_not_auto_export_git_state(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    repo = tmp_path / "toktrail-state"
    config_path = tmp_path / "config.toml"
    create_source_db(source_db)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"

[sync.git]
repo = "{_toml_path_value(repo)}"
auto_push = true
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    init_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "sync",
            "git",
            "init",
            "--no-hooks",
            "--no-import-existing",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    _configure_git_identity(repo)

    first = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "refresh",
        ],
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "refresh",
        ],
    )
    assert second.exit_code == 0, second.output

    assert not (repo / "state" / "manifest.json").exists()


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_push_stages_tracked_config_files(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "config.toml"
    repo = tmp_path / "toktrail-state"
    remote = tmp_path / "remote.git"
    create_source_db(source_db)
    _run_git(tmp_path, "init", "--bare", str(remote))
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"

[sync.git]
repo = "{_toml_path_value(repo)}"
remote = "origin"
branch = "main"
track = ["prices", "provider-prices", "subscriptions"]
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "sync",
            "git",
            "init",
            "--remote",
            str(remote),
            "--branch",
            "main",
        ],
    )
    _configure_git_identity(repo)

    (repo / "config").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "prices.toml").write_text(
        "config_version = 1\n",
        encoding="utf-8",
    )
    (repo / "config" / "subscriptions.toml").write_text(
        "config_version = 1\n",
        encoding="utf-8",
    )
    (repo / "config" / "prices").mkdir(parents=True, exist_ok=True)
    (repo / "config" / "prices" / "openai.toml").write_text(
        "config_version = 1\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "sync",
            "git",
            "push",
            "--no-refresh",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    tracked = set(_git_output(repo, "ls-files").splitlines())
    assert "config/prices.toml" in tracked
    assert "config/subscriptions.toml" in tracked
    assert "config/prices/openai.toml" in tracked


@pytest.mark.skipif(not HAS_GIT, reason="git executable is required")
def test_cli_sync_git_pull_dry_run_does_not_record_imports(tmp_path: Path) -> None:
    runner = CliRunner()
    producer_db = tmp_path / "producer.db"
    consumer_db = tmp_path / "consumer.db"
    source_db = tmp_path / "opencode.db"
    producer_repo = tmp_path / "repo-producer"
    consumer_repo = tmp_path / "repo-consumer"
    remote = tmp_path / "remote.git"
    create_source_db(source_db)
    _run_git(tmp_path, "init", "--bare", str(remote))

    runner.invoke(app, ["--db", str(producer_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(producer_db),
            "refresh",
            "--no-run",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )
    runner.invoke(
        app,
        [
            "--db",
            str(producer_db),
            "sync",
            "git",
            "init",
            "--repo",
            str(producer_repo),
            "--remote",
            str(remote),
            "--branch",
            "main",
            "--no-hooks",
        ],
    )
    _configure_git_identity(producer_repo)
    push_result = runner.invoke(
        app,
        [
            "--db",
            str(producer_db),
            "sync",
            "git",
            "push",
            "--repo",
            str(producer_repo),
            "--no-refresh",
        ],
    )
    assert push_result.exit_code == 0, push_result.output

    runner.invoke(
        app,
        [
            "--db",
            str(consumer_db),
            "sync",
            "git",
            "init",
            "--repo",
            str(consumer_repo),
            "--remote",
            str(remote),
            "--branch",
            "main",
            "--no-import-existing",
            "--no-hooks",
        ],
    )

    first_pull = runner.invoke(
        app,
        [
            "--db",
            str(consumer_db),
            "sync",
            "git",
            "pull",
            "--repo",
            str(consumer_repo),
            "--dry-run",
            "--json",
        ],
    )
    second_pull = runner.invoke(
        app,
        [
            "--db",
            str(consumer_db),
            "sync",
            "git",
            "pull",
            "--repo",
            str(consumer_repo),
            "--dry-run",
            "--json",
        ],
    )
    assert first_pull.exit_code == 0, first_pull.output
    assert second_pull.exit_code == 0, second_pull.output
    first_payload = json.loads(first_pull.output)
    second_payload = json.loads(second_pull.output)
    assert first_payload["archives_imported"] >= 1
    assert second_payload["archives_imported"] >= 1


def test_cli_sync_git_rejects_missing_git_binary(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    repo = tmp_path / "repo"

    def _raise_missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("toktrail.git_sync.subprocess.run", _raise_missing)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "sync",
            "git",
            "init",
            "--repo",
            str(repo),
        ],
    )

    assert result.exit_code != 0
    assert "git executable not found in PATH." in result.output
