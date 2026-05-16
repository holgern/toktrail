from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path
from shutil import which

import pytest

from toktrail.db import (
    connect,
    create_tracking_session,
    end_tracking_session,
    insert_usage_events,
    migrate,
    summarize_usage,
)
from toktrail.git_sync import (
    ensure_git_repo,
    export_repo_archive,
    git_hooks_status,
    git_pull,
    import_repo_archives,
    install_git_hooks,
    list_archives,
    uninstall_git_hooks,
)
from toktrail.models import TokenBreakdown, UsageEvent
from toktrail.reporting import UsageReportFilter

pytestmark = pytest.mark.skipif(
    which("git") is None,
    reason="git executable is required for git sync tests",
)


def _git(cwd: Path, *args: str) -> None:
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
    _git(repo_path, "config", "user.name", "Toktrail Tests")
    _git(repo_path, "config", "user.email", "toktrail-tests@example.com")


def _event(
    dedup_suffix: str,
    *,
    created_ms: int,
    raw_json: str | None = "{}",
) -> UsageEvent:
    return UsageEvent(
        harness="opencode",
        source_session_id="ses-1",
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"msg-{dedup_suffix}",
        global_dedup_key=f"opencode:msg-{dedup_suffix}",
        fingerprint_hash=f"fp-{dedup_suffix}",
        provider_id="anthropic",
        model_id="claude-sonnet-4",
        thinking_level=None,
        agent="build",
        created_ms=created_ms,
        completed_ms=created_ms + 100,
        tokens=TokenBreakdown(input=100, output=20),
        source_cost_usd=Decimal("1.0"),
        raw_json=raw_json,
    )


def _seed_db(db_path: Path, *, event: UsageEvent, end_run_flag: bool = True) -> None:
    conn = connect(db_path)
    try:
        migrate(conn)
        run_id = create_tracking_session(
            conn,
            "seed-run",
            started_at_ms=event.created_ms,
        )
        insert_usage_events(conn, run_id, [event])
        if end_run_flag:
            end_tracking_session(conn, run_id, ended_at_ms=event.completed_ms)
    finally:
        conn.close()


def _usage_total_tokens(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        migrate(conn)
        report = summarize_usage(conn, UsageReportFilter())
        return report.totals.tokens.total
    finally:
        conn.close()


def test_git_sync_init_creates_repo_layout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    ensure_git_repo(repo, remote_url=None, branch="main")

    assert (repo / ".git").is_dir()
    assert (repo / "meta" / "format.json").is_file()
    assert (repo / ".gitignore").is_file()


def test_git_sync_export_writes_immutable_archive_under_machine_id(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("1", created_ms=1_777_801_200_000))

    result = export_repo_archive(
        db_a,
        repo,
        archive_dir="archives",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    assert result.archive_path.exists()
    rel = result.archive_path.relative_to(repo)
    assert rel.parts[0] == "archives"
    assert rel.parts[1] == result.export_result.machine_id

    import_result = import_repo_archives(db_b, repo, dry_run=False)
    assert import_result.archives_imported == 1
    assert _usage_total_tokens(db_a) == _usage_total_tokens(db_b)


def test_git_sync_import_skips_already_imported_archive(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("1", created_ms=1_777_801_200_000))
    export_repo_archive(
        db_a,
        repo,
        archive_dir="archives",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    first = import_repo_archives(db_b, repo, dry_run=False)
    second = import_repo_archives(db_b, repo, dry_run=False)

    assert first.archives_imported == 1
    assert second.archives_imported == 0
    assert second.archives_skipped >= 1


def test_git_sync_two_machine_round_trip(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    _git(tmp_path, "init", "--bare", str(remote))

    ensure_git_repo(repo_a, remote_url=str(remote), branch="main")
    _configure_git_identity(repo_a)
    _seed_db(db_a, event=_event("a1", created_ms=1_777_801_200_000))
    export_repo_archive(
        db_a,
        repo_a,
        archive_dir="archives",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync-a",
        remote="origin",
        branch="main",
        push=True,
        allow_dirty=False,
    )

    ensure_git_repo(repo_b, remote_url=str(remote), branch="main")
    _configure_git_identity(repo_b)
    git_pull(repo_b, remote="origin", branch="main")
    import_repo_archives(db_b, repo_b, dry_run=False)

    _seed_db(db_b, event=_event("b1", created_ms=1_777_801_210_000))
    export_repo_archive(
        db_b,
        repo_b,
        archive_dir="archives",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync-b",
        remote="origin",
        branch="main",
        push=True,
        allow_dirty=False,
    )

    git_pull(repo_a, remote="origin", branch="main")
    import_repo_archives(db_a, repo_a, dry_run=False)

    assert _usage_total_tokens(db_a) == _usage_total_tokens(db_b)


def test_git_sync_redacts_raw_json_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(
        db_a,
        event=_event("1", created_ms=1_777_801_200_000, raw_json='{"secret": true}'),
    )

    export_repo_archive(
        db_a,
        repo,
        archive_dir="archives",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )
    import_repo_archives(db_b, repo, dry_run=False)

    conn = connect(db_b)
    try:
        migrate(conn)
        row = conn.execute("SELECT raw_json FROM usage_events LIMIT 1").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["raw_json"] is None


def test_git_sync_dirty_repo_protection(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("1", created_ms=1_777_801_200_000))
    (repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="uncommitted changes"):
        export_repo_archive(
            db_a,
            repo,
            archive_dir="archives",
            config_path=config_path,
            include_config=False,
            redact_raw_json=True,
            commit_message="sync",
            remote="origin",
            branch="main",
            push=False,
            allow_dirty=False,
        )


def test_git_sync_remote_active_default_close_at_export(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_remote = tmp_path / "remote.db"
    db_local = tmp_path / "local.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)

    _seed_db(
        db_remote,
        event=_event("remote", created_ms=1_777_801_200_000),
        end_run_flag=False,
    )
    export_repo_archive(
        db_remote,
        repo,
        archive_dir="archives",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync-remote",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    _seed_db(
        db_local,
        event=_event("local", created_ms=1_777_801_210_000),
        end_run_flag=False,
    )

    result = import_repo_archives(db_local, repo, dry_run=False)

    assert result.archives_imported == 1


def test_git_sync_list_archives_returns_sorted_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ensure_git_repo(repo, remote_url=None, branch="main")

    first = repo / "archives" / "machine-a" / "b.tar.gz"
    second = repo / "archives" / "machine-a" / "a.tar.gz"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    paths = list_archives(repo)

    assert [path.name for path in paths] == ["a.tar.gz", "b.tar.gz"]


def test_git_sync_export_stages_tracked_config_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    prices_file = repo / "config" / "prices.toml"
    subscriptions_file = repo / "config" / "subscriptions.toml"
    provider_dir = repo / "config" / "prices"
    provider_file = provider_dir / "openai.toml"
    nested_provider_file = provider_dir / "tiers" / "zai.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_path, event=_event("1", created_ms=1_777_801_200_000))

    prices_file.parent.mkdir(parents=True, exist_ok=True)
    provider_dir.mkdir(parents=True, exist_ok=True)
    prices_file.write_text("config_version = 1\n", encoding="utf-8")
    subscriptions_file.write_text("config_version = 1\n", encoding="utf-8")
    provider_file.write_text("config_version = 1\n", encoding="utf-8")
    nested_provider_file.parent.mkdir(parents=True, exist_ok=True)
    nested_provider_file.write_text("config_version = 1\n", encoding="utf-8")

    export_repo_archive(
        db_path,
        repo,
        archive_dir="archives",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
        tracked_config_paths=(prices_file, subscriptions_file, provider_dir),
    )

    tracked = set(_git_output(repo, "ls-files").splitlines())
    assert "config/prices.toml" in tracked
    assert "config/subscriptions.toml" in tracked
    assert "config/prices/openai.toml" in tracked
    assert "config/prices/tiers/zai.toml" in tracked


def test_git_sync_export_still_rejects_untracked_dirty_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    provider_dir = repo / "config" / "prices"
    provider_file = provider_dir / "openai.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_path, event=_event("1", created_ms=1_777_801_200_000))

    provider_dir.mkdir(parents=True, exist_ok=True)
    provider_file.write_text("config_version = 1\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="uncommitted changes"):
        export_repo_archive(
            db_path,
            repo,
            archive_dir="archives",
            config_path=config_path,
            include_config=False,
            redact_raw_json=True,
            commit_message="sync",
            remote="origin",
            branch="main",
            push=False,
            allow_dirty=False,
            tracked_config_paths=(provider_dir,),
        )


def test_install_git_hooks_writes_managed_hooks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ensure_git_repo(repo, remote_url=None, branch="main")

    result = install_git_hooks(
        repo,
        toktrail_command=("toktrail",),
        db_path=tmp_path / "toktrail.db",
        config_path=tmp_path / "config.toml",
        force=False,
    )

    assert set(result.installed) == {"post-merge", "post-checkout", "post-rewrite"}
    for hook_name in result.installed:
            hook_path = repo / ".git" / "hooks" / hook_name
            assert hook_path.exists()
            text = hook_path.read_text(encoding="utf-8")
            assert "# toktrail-managed-hook v1" in text
            assert "sync git import-local --repo" in text
            assert "--quiet" in text


def test_install_git_hooks_preserves_foreign_hook_without_force(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ensure_git_repo(repo, remote_url=None, branch="main")
    foreign_hook = repo / ".git" / "hooks" / "post-merge"
    foreign_hook.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")

    result = install_git_hooks(repo, force=False)

    assert "post-merge" in result.skipped
    assert "post-merge" not in result.overwritten
    assert "foreign" in foreign_hook.read_text(encoding="utf-8")
    assert (repo / ".git" / "hooks" / "post-merge.toktrail.sample").exists()


def test_install_git_hooks_overwrites_foreign_hook_with_force(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ensure_git_repo(repo, remote_url=None, branch="main")
    foreign_hook = repo / ".git" / "hooks" / "post-rewrite"
    foreign_hook.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")

    result = install_git_hooks(repo, force=True)

    assert "post-rewrite" in result.overwritten
    assert "# toktrail-managed-hook v1" in foreign_hook.read_text(encoding="utf-8")


def test_git_hooks_status_reports_installed_missing_foreign(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ensure_git_repo(repo, remote_url=None, branch="main")
    install_git_hooks(repo)
    (repo / ".git" / "hooks" / "post-checkout").write_text(
        "#!/bin/sh\necho foreign\n",
        encoding="utf-8",
    )
    (repo / ".git" / "hooks" / "post-rewrite").unlink()

    status = git_hooks_status(repo)

    assert status.hooks["post-merge"] == "installed"
    assert status.hooks["post-checkout"] == "foreign"
    assert status.hooks["post-rewrite"] == "missing"


def test_uninstall_git_hooks_removes_only_managed_hooks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ensure_git_repo(repo, remote_url=None, branch="main")
    install_git_hooks(repo)
    (repo / ".git" / "hooks" / "post-checkout").write_text(
        "#!/bin/sh\necho foreign\n",
        encoding="utf-8",
    )

    result = uninstall_git_hooks(repo)

    assert "post-merge" in result.overwritten
    assert "post-rewrite" in result.overwritten
    assert "post-checkout" in result.skipped
    assert not (repo / ".git" / "hooks" / "post-merge").exists()
    assert (repo / ".git" / "hooks" / "post-checkout").exists()
