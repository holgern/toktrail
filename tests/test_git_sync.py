from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from pathlib import Path
from shutil import which

import pytest

from toktrail.config import normalize_identity
from toktrail.db import (
    assign_area_to_source_session,
    connect,
    create_tracking_session,
    end_tracking_session,
    ensure_area,
    get_local_machine_id,
    insert_usage_events,
    migrate,
    summarize_usage,
    upsert_source_session_metadata,
)
from toktrail.git_sync import (
    analyze_git_sync_cleanup,
    cleanup_git_sync_worktree,
    ensure_git_repo,
    export_repo_archive,
    git_hooks_status,
    git_pull,
    git_sync_status,
    import_repo_archives,
    install_git_hooks,
    list_archives,
    reset_git_sync_history,
    rewrite_git_sync_history,
    uninstall_git_hooks,
)
from toktrail.git_sync_parts import core as git_sync_core
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
    harness: str = "opencode",
    source_session_id: str = "ses-1",
    provider_id: str = "anthropic",
    model_id: str = "claude-sonnet-4",
    agent: str | None = "build",
    origin_machine_id: str | None = None,
) -> UsageEvent:
    return UsageEvent(
        harness=harness,
        source_session_id=source_session_id,
        source_row_id=f"row-{dedup_suffix}",
        source_message_id=f"msg-{dedup_suffix}",
        source_dedup_key=f"msg-{dedup_suffix}",
        global_dedup_key=f"{harness}:msg-{dedup_suffix}",
        fingerprint_hash=f"fp-{dedup_suffix}",
        provider_id=provider_id,
        model_id=model_id,
        thinking_level=None,
        agent=agent,
        created_ms=created_ms,
        completed_ms=created_ms + 100,
        tokens=TokenBreakdown(input=100, output=20),
        source_cost_usd=Decimal("1.0"),
        raw_json=raw_json,
        origin_machine_id=origin_machine_id,
    )


def _seed_db(db_path: Path, *, event: UsageEvent, end_run_flag: bool = True) -> None:
    _seed_db_events(db_path, events=[event], end_run_flag=end_run_flag)


def _seed_db_events(
    db_path: Path,
    *,
    events: list[UsageEvent],
    end_run_flag: bool = True,
) -> None:
    assert events
    conn = connect(db_path)
    try:
        migrate(conn)
        run_id = create_tracking_session(
            conn,
            "seed-run",
            started_at_ms=min(event.created_ms for event in events),
        )
        insert_usage_events(conn, run_id, events)
        if end_run_flag:
            end_tracking_session(
                conn,
                run_id,
                ended_at_ms=max(
                    event.completed_ms or event.created_ms for event in events
                ),
            )
    finally:
        conn.close()


def _write_state_bytes(
    state_root: Path, relpath: str, content: bytes
) -> dict[str, object]:
    target = state_root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {"path": relpath, "sha256": git_sync_core._sha256_file(target)}


def _write_state_json(
    state_root: Path, relpath: str, payload: dict[str, object]
) -> dict[str, object]:
    return _write_state_bytes(
        state_root,
        relpath,
        (
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        ),
    )


def _write_state_manifest(
    state_root: Path,
    *,
    format_version: str,
    machine_id: str,
    tables: dict[str, list[dict[str, object]]],
) -> None:
    manifest = {
        "format": format_version,
        "schema_version": git_sync_core.db.SCHEMA_VERSION,
        "exported_at_ms": 1_777_801_200_000,
        "machine_id": machine_id,
        "machine_name": "legacy-host",
        "raw_json_redacted": True,
        "tables": {
            table: {
                "rows": len(tables.get(table, [])),
                "files": len(tables.get(table, [])),
                "entries": tables.get(table, []),
            }
            for table in git_sync_core._STATE_TABLES
        },
    }
    (state_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _init_git_sync_repo(
    tmp_path: Path,
    *,
    remote: Path | None = None,
) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    if remote is not None:
        _git(repo, "remote", "add", "origin", str(remote))
    _seed_db(db_path, event=_event("1", created_ms=1_777_801_200_000))
    export_repo_archive(
        db_path,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )
    return repo, db_path, config_path


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


def test_git_sync_export_writes_text_state_files(
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
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    assert result.state_path.exists()
    assert (repo / "state" / "manifest.json").is_file()
    assert list((repo / "state" / "usage-events").rglob("*.jsonl"))
    assert list((repo / "state" / "run-events").rglob("*.jsonl"))
    assert not list(repo.rglob("*.tar.gz"))

    import_result = import_repo_archives(db_b, repo, dry_run=False)
    assert import_result.archives_imported == 1
    assert _usage_total_tokens(db_a) == _usage_total_tokens(db_b)


def test_git_sync_export_replaces_existing_state_dir_on_windows_like_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("1", created_ms=1_777_801_200_000))

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="initial sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )
    assert (repo / "state" / "manifest.json").is_file()

    original_rename = Path.rename

    def windows_like_rename(self: Path, target: str | Path) -> Path:
        target_path = Path(target)
        if self.name.startswith(".state.staging.") and target_path.exists():
            raise FileExistsError(
                183,
                "Cannot create a file when that file already exists",
                str(target_path),
            )
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", windows_like_rename)

    # A second export replaces the already-existing state directory. This used to
    # depend on POSIX directory rename semantics and failed on Windows.
    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="second sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )
    assert (repo / "state" / "manifest.json").is_file()


def test_git_sync_export_updates_only_changed_files_and_removes_stale_files(
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

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message=None,
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )
    exported_files = sorted((repo / "state" / "usage-events").rglob("*.jsonl"))
    assert len(exported_files) == 1
    first_session_path = exported_files[0]
    first_session_mtime_ns = first_session_path.stat().st_mtime_ns

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message=None,
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )
    assert first_session_path.stat().st_mtime_ns == first_session_mtime_ns

    _seed_db(
        db_b,
        event=_event("2", created_ms=1_777_801_300_000, source_session_id="ses-2"),
    )
    export_repo_archive(
        db_b,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message=None,
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    second_session_files = sorted((repo / "state" / "usage-events").rglob("*.jsonl"))
    assert len(second_session_files) == 1
    second_session_path = second_session_files[0]
    second_session_payload = second_session_path.read_text(encoding="utf-8")
    assert second_session_path != first_session_path
    assert not first_session_path.exists()

    _seed_db(
        db_b,
        event=_event("3", created_ms=1_777_801_300_500, source_session_id="ses-2"),
    )
    export_repo_archive(
        db_b,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message=None,
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    updated_files = sorted((repo / "state" / "usage-events").rglob("*.jsonl"))
    assert updated_files == [second_session_path]
    assert updated_files[0].read_text(encoding="utf-8") != second_session_payload


def test_git_sync_export_groups_usage_events_by_source_session(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db_events(
        db_a,
        events=[
            _event("1", created_ms=1_777_801_200_000, source_session_id="ses-1"),
            _event("2", created_ms=1_777_801_200_500, source_session_id="ses-1"),
            _event("3", created_ms=1_777_801_201_000, source_session_id="ses-2"),
        ],
    )

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    usage_files = sorted((repo / "state" / "usage-events").rglob("*.jsonl"))
    run_event_files = sorted((repo / "state" / "run-events").rglob("*.jsonl"))

    assert len(usage_files) == 2
    assert len(run_event_files) == 1

    ses_1_header: dict[str, object] | None = None
    ses_1_records: list[dict[str, object]] = []
    for path in usage_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        if header["source_session_id"] != "ses-1":
            continue
        ses_1_header = header
        ses_1_records = [json.loads(line)["record"] for line in lines[1:]]
        break

    assert ses_1_header is not None
    assert ses_1_header["format"] == git_sync_core._USAGE_SESSION_FORMAT
    assert [record["created_ms"] for record in ses_1_records] == [
        1_777_801_200_000,
        1_777_801_200_500,
    ]
    assert len(ses_1_records) == 2

    run_lines = run_event_files[0].read_text(encoding="utf-8").splitlines()
    assert json.loads(run_lines[0])["format"] == git_sync_core._RUN_EVENTS_FORMAT
    assert len(run_lines[1:]) == 3

    import_result = import_repo_archives(db_b, repo, dry_run=False)

    assert import_result.archives_imported == 1
    assert _usage_total_tokens(db_a) == _usage_total_tokens(db_b)


def test_git_sync_import_reads_legacy_v3_usage_event_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "toktrail.db"
    state_root = repo / "state"
    machine_id = "legacy-machine"

    ensure_git_repo(repo, remote_url=None, branch="main")

    machine_entry = _write_state_json(
        state_root,
        f"machines/{machine_id}.json",
        {
            "machine_id": machine_id,
            "name": "legacy-host",
            "name_key": "legacy-host",
            "first_seen_ms": 1_777_801_200_000,
            "last_seen_ms": 1_777_801_200_000,
            "is_local": 1,
            "created_at_ms": 1_777_801_200_000,
            "updated_at_ms": 1_777_801_200_000,
            "imported_at_ms": 1_777_801_200_000,
        },
    )
    usage_entry = _write_state_json(
        state_root,
        "usage-events/opencode/event.json",
        {
            "harness": "opencode",
            "source_session_id": "ses-legacy",
            "source_row_id": "row-legacy",
            "source_message_id": "msg-legacy",
            "source_dedup_key": "msg-legacy",
            "global_dedup_key": "opencode:msg-legacy",
            "fingerprint_hash": "fp-legacy",
            "origin_machine_id": machine_id,
            "role": "assistant",
            "provider_id": "OpenAI",
            "provider_key": "openai",
            "model_id": "GPT-4o",
            "model_key": "gpt-4o",
            "thinking_level": None,
            "agent": "Build Agent",
            "agent_key": "build-agent",
            "created_ms": 1_777_801_200_000,
            "completed_ms": 1_777_801_200_100,
            "input_tokens": 10,
            "output_tokens": 2,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cache_output_tokens": 0,
            "source_cost_usd": "1.0",
            "raw_json": None,
            "imported_at_ms": 1_777_801_200_200,
        },
    )
    _write_state_manifest(
        state_root,
        format_version=git_sync_core._LEGACY_STATE_FORMAT,
        machine_id=machine_id,
        tables={
            "machines": [machine_entry],
            "usage_events": [usage_entry],
        },
    )

    result = import_repo_archives(db_path, repo, dry_run=False)

    assert result.archives_imported == 1
    assert result.import_results[0].usage_events_inserted == 1


def test_git_sync_export_replaces_legacy_usage_event_files_with_session_files(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    state_root = repo / "state"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_path, event=_event("1", created_ms=1_777_801_200_000))

    legacy_entry = _write_state_json(
        state_root,
        "usage-events/opencode/event.json",
        {"legacy": True},
    )
    _write_state_manifest(
        state_root,
        format_version=git_sync_core._LEGACY_STATE_FORMAT,
        machine_id="legacy-machine",
        tables={"usage_events": [legacy_entry]},
    )

    export_repo_archive(
        db_path,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    assert not (state_root / "usage-events" / "opencode" / "event.json").exists()
    assert not list((state_root / "usage-events").rglob("*.json"))
    assert list((state_root / "usage-events").rglob("*.jsonl"))


def test_git_sync_usage_session_grouping_includes_origin_machine_id(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    conn = connect(db_path)
    try:
        migrate(conn)
        run_id = create_tracking_session(
            conn,
            "seed-run",
            started_at_ms=1_777_801_200_000,
        )
        insert_usage_events(
            conn,
            run_id,
            [_event("1", created_ms=1_777_801_200_000)],
            origin_machine_id="machine-a",
        )
        insert_usage_events(
            conn,
            run_id,
            [_event("2", created_ms=1_777_801_200_500)],
            origin_machine_id="machine-b",
        )
        end_tracking_session(conn, run_id, ended_at_ms=1_777_801_200_600)
    finally:
        conn.close()

    export_repo_archive(
        db_path,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    usage_files = sorted((repo / "state" / "usage-events").rglob("*.jsonl"))
    headers = [
        json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        for path in usage_files
    ]

    assert len(usage_files) == 2
    assert {header["origin_machine_id"] for header in headers} == {
        "machine-a",
        "machine-b",
    }


def test_git_sync_round_trip_preserves_usage_identity_keys(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    event = _event(
        "keys",
        created_ms=1_777_801_200_000,
        provider_id="OpenAI",
        model_id="GPT 4o",
        agent="Build Agent",
    )
    _seed_db(db_a, event=event)

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
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
        row = conn.execute(
            """
            SELECT provider_key, model_key, agent_key
            FROM usage_events
            WHERE global_dedup_key = ?
            """,
            (event.global_dedup_key,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["provider_key"] == normalize_identity(event.provider_id)
    assert row["model_key"] == normalize_identity(event.model_id)
    assert row["agent_key"] == normalize_identity(event.agent or "")


def test_git_sync_export_records_machine_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    monkeypatch.setenv("TOKTRAIL_MACHINE_NAME", "thinkpad")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("1", created_ms=1_777_801_200_000))

    result = export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message=None,
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    machine_rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((repo / "state" / "machines").glob("*.json"))
    ]
    local_rows = [row for row in machine_rows if int(row.get("is_local", 0)) == 1]
    assert local_rows
    assert local_rows[0]["name"] == "thinkpad"
    assert result.export_result.machine_name == "thinkpad"


def test_git_sync_commit_message_uses_machine_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    monkeypatch.setenv("TOKTRAIL_MACHINE_NAME", "desktop")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("1", created_ms=1_777_801_200_000))

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message=None,
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    message = _git_output(repo, "log", "-1", "--pretty=%s").strip()
    assert message.startswith("toktrail sync: desktop ")


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
        archive_dir="state",
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
        archive_dir="state",
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
        archive_dir="state",
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


def test_git_sync_round_trip_preserves_areas(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("area-1", created_ms=1_777_801_200_000))

    conn = connect(db_a)
    try:
        migrate(conn)
        area = ensure_area(conn, "work/odoo")
        expected_sync_id = area.sync_id
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=get_local_machine_id(conn),
            harness="opencode",
            source_session_id="ses-1",
        )
        conn.commit()
    finally:
        conn.close()

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
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
        assignment = conn.execute(
            """
            SELECT a.path, a.sync_id
            FROM area_session_assignments asa
            JOIN areas a ON a.id = asa.area_id
            WHERE asa.harness = ?
              AND asa.source_session_id = ?
            """,
            ("opencode", "ses-1"),
        ).fetchone()
        usage = conn.execute(
            """
            SELECT a.path
            FROM usage_events ue
            JOIN areas a ON a.id = ue.area_id
            WHERE ue.global_dedup_key = ?
            """,
            ("opencode:msg-area-1",),
        ).fetchone()
    finally:
        conn.close()

    assert assignment is not None
    assert assignment["path"] == "work/odoo"
    assert assignment["sync_id"] == expected_sync_id
    assert usage is not None
    assert usage["path"] == "work/odoo"


def test_git_sync_round_trip_preserves_source_session_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("meta", created_ms=1_777_801_240_000))

    conn = connect(db_a)
    try:
        migrate(conn)
        machine_id = get_local_machine_id(conn)
        upsert_source_session_metadata(
            conn,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-1",
            source_paths=("/tmp/opencode.db",),
            cwd="/work/odoo",
            source_dir="/work/odoo",
            git_root="/work/odoo",
            git_remote="git@github.com:company/odoo.git",
            session_title="Round Trip",
            started_ms=1_777_801_240_000,
            last_seen_ms=1_777_801_240_100,
        )
        conn.commit()
    finally:
        conn.close()

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
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
        row = conn.execute(
            """
            SELECT
                source_paths_json,
                cwd,
                source_dir,
                git_root,
                git_remote,
                session_title
            FROM source_session_metadata
            WHERE harness = 'opencode' AND source_session_id = 'ses-1'
            """
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert json.loads(str(row["source_paths_json"])) == ["/tmp/opencode.db"]
    assert row["cwd"] == "/work/odoo"
    assert row["source_dir"] == "/work/odoo"
    assert row["git_root"] == "/work/odoo"
    assert row["git_remote"] == "git@github.com:company/odoo.git"
    assert row["session_title"] == "Round Trip"


def test_git_sync_area_numeric_id_is_local_when_pc2_has_existing_areas(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)

    conn = connect(db_a)
    try:
        migrate(conn)
        area_a = ensure_area(conn, "work/odoo")
        pc1_local_id = area_a.id
        pc1_sync_id = area_a.sync_id
        conn.commit()
    finally:
        conn.close()

    conn = connect(db_b)
    try:
        migrate(conn)
        ensure_area(conn, "privat/toktrail")
        conn.commit()
    finally:
        conn.close()

    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
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
        area_b = conn.execute(
            "SELECT id, sync_id FROM areas WHERE path = ?",
            ("work/odoo",),
        ).fetchone()
    finally:
        conn.close()

    assert area_b is not None
    assert area_b["sync_id"] == pc1_sync_id
    assert area_b["id"] != pc1_local_id


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
        archive_dir="state",
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
            archive_dir="state",
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
        archive_dir="state",
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

    first = repo / "state" / "b.json"
    second = repo / "state" / "a.json"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    paths = list_archives(repo)

    assert [path.name for path in paths] == ["a.json", "b.json"]


def test_git_sync_cleanup_analysis_reports_obsolete_archives(tmp_path: Path) -> None:
    repo, _, _ = _init_git_sync_repo(tmp_path)
    archive_file = repo / "archives" / "old.tar.gz"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(b"archive")
    _git(repo, "add", "-f", "archives/old.tar.gz")
    _git(repo, "commit", "-m", "add old archive")

    analysis = analyze_git_sync_cleanup(repo)

    assert analysis.recommended_action == "reset-history"
    assert any(item.path == "archives" for item in analysis.obsolete_sizes)
    assert analysis.state_size is not None


def test_git_sync_cleanup_worktree_removes_archives_and_commits(tmp_path: Path) -> None:
    repo, _, _ = _init_git_sync_repo(tmp_path)
    archive_file = repo / "archives" / "old.tar.gz"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(b"archive")
    _git(repo, "add", "-f", "archives/old.tar.gz")
    _git(repo, "commit", "-m", "add old archive")

    result = cleanup_git_sync_worktree(repo, commit=True, dry_run=False)

    assert result.committed is True
    assert result.commit_hash is not None
    assert not archive_file.exists()
    assert "archives/old.tar.gz" not in _git_output(repo, "ls-files")


def test_git_sync_cleanup_worktree_dry_run_changes_nothing(tmp_path: Path) -> None:
    repo, _, _ = _init_git_sync_repo(tmp_path)
    archive_file = repo / "archives" / "old.tar.gz"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(b"archive")
    _git(repo, "add", "-f", "archives/old.tar.gz")
    _git(repo, "commit", "-m", "add old archive")

    result = cleanup_git_sync_worktree(repo, commit=False, dry_run=True)

    assert result.dry_run is True
    assert archive_file.exists()
    assert _git_output(repo, "status", "--short").strip() == ""


def test_git_sync_cleanup_refuses_live_sqlite_unless_worktree_cleanup(
    tmp_path: Path,
) -> None:
    repo, db_path, config_path = _init_git_sync_repo(tmp_path)
    sqlite_path = repo / "toktrail.db"
    sqlite_path.write_bytes(b"sqlite")

    analysis = analyze_git_sync_cleanup(repo)

    assert "toktrail.db" in analysis.live_sqlite_paths
    with pytest.raises(ValueError, match="live sqlite state files"):
        export_repo_archive(
            db_path,
            repo,
            archive_dir="state",
            config_path=config_path,
            include_config=False,
            redact_raw_json=True,
            commit_message="sync",
            remote="origin",
            branch="main",
            push=False,
            allow_dirty=False,
        )

    cleanup_git_sync_worktree(repo, commit=False, dry_run=False)

    assert not sqlite_path.exists()
    export_repo_archive(
        db_path,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )


def test_git_sync_cleanup_reset_history_keeps_only_allowed_paths(
    tmp_path: Path,
) -> None:
    repo, db_path, config_path = _init_git_sync_repo(tmp_path)
    archive_file = repo / "archives" / "old.tar.gz"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(b"archive")
    (repo / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    _git(repo, "add", "-f", "archives/old.tar.gz", "scratch.txt")
    _git(repo, "commit", "-m", "add obsolete files")

    result = reset_git_sync_history(
        db_path,
        repo,
        config_path=config_path,
        tracked_config_paths=(),
        redact_raw_json=True,
        force=True,
        push=False,
    )

    tracked = set(_git_output(repo, "ls-files").splitlines())
    assert result.committed is True
    assert result.backup_bundle is not None and result.backup_bundle.exists()
    assert "state/manifest.json" in tracked
    assert "archives/old.tar.gz" not in tracked
    assert "scratch.txt" not in tracked
    assert not archive_file.exists()
    assert _git_output(repo, "log", "--all", "--", "archives/old.tar.gz").strip() == ""


def test_git_sync_cleanup_reset_history_writes_backup_bundle(tmp_path: Path) -> None:
    repo, db_path, config_path = _init_git_sync_repo(tmp_path)

    result = reset_git_sync_history(
        db_path,
        repo,
        config_path=config_path,
        tracked_config_paths=(),
        redact_raw_json=True,
        force=True,
        push=False,
    )

    assert result.backup_bundle is not None
    assert result.backup_bundle.exists()
    assert result.backup_bundle.stat().st_size > 0


def test_git_sync_cleanup_reset_history_accepts_legacy_git_sync_format(
    tmp_path: Path,
) -> None:
    repo, db_path, config_path = _init_git_sync_repo(tmp_path)
    format_path = repo / "meta" / "format.json"
    payload = json.loads(format_path.read_text(encoding="utf-8"))
    payload["format"] = "toktrail.git-sync.v1"
    format_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _git(repo, "add", "meta/format.json")
    _git(repo, "commit", "-m", "use legacy format marker")

    result = reset_git_sync_history(
        db_path,
        repo,
        config_path=config_path,
        tracked_config_paths=(),
        redact_raw_json=True,
        force=True,
        push=False,
    )

    assert result.committed is True
    assert (repo / "state" / "manifest.json").is_file()


def test_git_sync_cleanup_rewrite_history_requires_filter_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _ = _init_git_sync_repo(tmp_path)
    monkeypatch.setattr(git_sync_core, "_filter_repo_available", lambda: False)

    with pytest.raises(ValueError, match="git-filter-repo"):
        rewrite_git_sync_history(repo, force=True)


def test_git_sync_cleanup_destructive_requires_force(tmp_path: Path) -> None:
    repo, db_path, config_path = _init_git_sync_repo(tmp_path)

    with pytest.raises(ValueError, match="--force"):
        reset_git_sync_history(
            db_path,
            repo,
            config_path=config_path,
            tracked_config_paths=(),
            redact_raw_json=True,
            force=False,
            push=False,
        )
    with pytest.raises(ValueError, match="--force"):
        rewrite_git_sync_history(repo, force=False)


def test_git_sync_cleanup_does_not_push_without_push_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, db_path, config_path = _init_git_sync_repo(tmp_path)
    monkeypatch.setattr(
        git_sync_core,
        "_remote_branch_exists",
        lambda repo_path, remote, branch: True,
    )

    def _unexpected_push(repo_path: Path, *, remote: str, branch: str) -> None:
        raise AssertionError("cleanup must not push without push=True")

    monkeypatch.setattr(git_sync_core, "_run_git_force_with_lease", _unexpected_push)

    result = reset_git_sync_history(
        db_path,
        repo,
        config_path=config_path,
        tracked_config_paths=(),
        redact_raw_json=True,
        force=True,
        push=False,
    )

    assert result.pushed is False


def test_git_sync_cleanup_push_uses_force_with_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    repo, db_path, config_path = _init_git_sync_repo(tmp_path, remote=remote)
    _git(repo, "push", "-u", "origin", "main")
    archive_file = repo / "archives" / "old.tar.gz"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(b"archive")
    _git(repo, "add", "-f", "archives/old.tar.gz")
    _git(repo, "commit", "-m", "add old archive")

    original_run_git = git_sync_core._run_git
    push_commands: list[tuple[str, ...]] = []

    def _recording_run_git(repo_path: Path, *args: str):
        if args and args[0] == "push":
            push_commands.append(args)
        return original_run_git(repo_path, *args)

    monkeypatch.setattr(git_sync_core, "_run_git", _recording_run_git)

    result = reset_git_sync_history(
        db_path,
        repo,
        config_path=config_path,
        tracked_config_paths=(),
        redact_raw_json=True,
        force=True,
        push=True,
    )

    assert result.pushed is True
    assert any("--force-with-lease" in command for command in push_commands)
    assert all("--force" not in command for command in push_commands)


def test_git_sync_existing_empty_main_branch_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(tmp_path, "init", str(repo), "--initial-branch=main")

    ensure_git_repo(repo, remote_url=None, branch="main")
    ensure_git_repo(repo, remote_url=None, branch="main")


def test_git_sync_status_reports_pending_import(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("pending", created_ms=1_777_801_200_000))
    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )

    before = git_sync_status(db_b, repo)
    assert before.pending_import_count == 1

    import_repo_archives(db_b, repo, dry_run=False)
    after = git_sync_status(db_b, repo)
    assert after.pending_import_count == 0


def test_git_sync_import_rejects_state_checksum_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("checksum", created_ms=1_777_801_200_000))
    export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )
    usage_file = next((repo / "state" / "usage-events").rglob("*.jsonl"))
    usage_file.write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        import_repo_archives(db_b, repo, dry_run=False)


def test_git_sync_stale_staging_dir_does_not_block_export(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("staging", created_ms=1_777_801_200_000))
    stale = repo / ".state.staging.deadbeef"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "junk.txt").write_text("junk\n", encoding="utf-8")

    result = export_repo_archive(
        db_a,
        repo,
        archive_dir="state",
        config_path=config_path,
        include_config=False,
        redact_raw_json=True,
        commit_message="sync",
        remote="origin",
        branch="main",
        push=False,
        allow_dirty=False,
    )
    assert result.state_path.exists()


def test_git_sync_include_config_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_a = tmp_path / "a.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")

    ensure_git_repo(repo, remote_url=None, branch="main")
    _configure_git_identity(repo)
    _seed_db(db_a, event=_event("cfg", created_ms=1_777_801_200_000))

    with pytest.raises(ValueError, match="track"):
        export_repo_archive(
            db_a,
            repo,
            archive_dir="state",
            config_path=config_path,
            include_config=True,
            redact_raw_json=True,
            commit_message="sync",
            remote="origin",
            branch="main",
            push=False,
            allow_dirty=False,
        )


def test_git_sync_repo_relative_tracked_paths_are_posix_for_git(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    prices_file = repo / "config" / "prices.toml"
    provider_dir = repo / "config" / "prices"

    ensure_git_repo(repo, remote_url=None, branch="main")
    prices_file.parent.mkdir(parents=True)
    prices_file.write_text("config_version = 1\n", encoding="utf-8")

    relpaths, prefixes = git_sync_core._repo_relative_tracked_paths(
        repo,
        (prices_file, provider_dir),
    )

    assert "config/prices.toml" in relpaths
    assert "config/prices/" in prefixes
    assert not any("\\" in item for item in (*relpaths, *prefixes))


def test_git_sync_state_fingerprint_uses_posix_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    state_file = state_root / "usage-events" / "opencode" / "event.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(git_sync_core, "_sha256_file", lambda path: "file-digest")

    expected = (
        __import__("hashlib")
        .sha256(b"usage-events/opencode/event.json\0file-digest\n")
        .hexdigest()
    )
    assert git_sync_core._state_files_fingerprint(state_root, [state_file]) == expected


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
        archive_dir="state",
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
            archive_dir="state",
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
