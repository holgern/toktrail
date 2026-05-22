from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.cli.helpers import (
    _assert_rich_result_or_missing_dependency,
    _future_ms,
    _toml_path_value,
    create_codex_session_file_with_cwd,
    create_source_db,
    make_cli_usage_event,
    setup_pricing_status_fixture,
)
from toktrail.cli import app
from toktrail.cli_parts import main_cli as cli_module
from toktrail.db import (
    archive_tracking_session,
    assign_area_to_source_session,
    connect,
    create_tracking_session,
    end_tracking_session,
    ensure_area,
    get_local_machine_id,
    insert_usage_events,
    migrate,
    upsert_machine,
)
from toktrail.models import TokenBreakdown


def test_cli_usage_today_reports_unscoped_refreshes(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
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
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime.now(tz=tz),
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--utc",
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["session"] is None
    assert payload["filters"]["period"] == "today"
    assert payload["filters"]["timezone"] == "UTC"
    assert payload["totals"]["total"] == 1500
    assert "refresh" not in payload


def test_cli_usage_today_does_not_export_git_state(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    repo = tmp_path / "toktrail-state"
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
    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime.now(tz=tz),
    )

    def fail_export(*args, **kwargs):
        raise AssertionError("usage today must not export git state")

    monkeypatch.setattr("toktrail.cli_sync.export_repo_archive", fail_export)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--utc",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output


def test_cli_usage_no_refresh_uses_existing_state_only(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
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
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime.now(tz=tz),
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--utc",
            "--json",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["total"] == 0


def test_cli_usage_runs_excludes_archived_by_default(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        run_id = create_tracking_session(conn, "archived", started_at_ms=1_000)
        insert_usage_events(
            conn,
            run_id,
            [
                make_cli_usage_event(
                    "archived",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=10, output=5),
                )
            ],
        )
        end_tracking_session(conn, run_id, ended_at_ms=1_100)
        archive_tracking_session(conn, run_id, archived_at_ms=1_200)
    finally:
        conn.close()

    default_result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--json", "--no-refresh"],
    )
    archived_result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--json", "--all", "--no-refresh"],
    )

    assert default_result.exit_code == 0, default_result.output
    assert archived_result.exit_code == 0, archived_result.output
    assert json.loads(default_result.output)["runs"] == []
    assert len(json.loads(archived_result.output)["runs"]) == 1


def test_cli_usage_refresh_details_prints_compact_refresh_summary(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
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
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--refresh-details",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Refreshed usage" in result.output
    assert "files" in result.output
    assert "inserted" in result.output
    assert "fingerprint ms" in result.output
    assert "scan ms" in result.output
    assert "db ms" in result.output
    assert "total ms" in result.output
    assert "source path" not in result.output.lower()


def test_cli_usage_refresh_details_json_wraps_refresh_and_report(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
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
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--json",
            "--refresh-details",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "refresh" in payload
    assert "report" in payload
    assert "elapsed_ms" in payload["refresh"][0]
    assert "fingerprint_ms" in payload["refresh"][0]
    assert "scan_ms" in payload["refresh"][0]
    assert "db_write_ms" in payload["refresh"][0]
    assert payload["report"]["totals"]["total"] == 1500


def test_cli_usage_auto_refresh_skips_recent_report_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
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

[reports]
refresh = "auto"
min_refresh_interval_secs = 60
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    calls: list[str] = []
    original = cli_module.import_configured_usage_api

    def counted_import(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(str(kwargs.get("refresh_mode")))
        return original(*args, **kwargs)

    monkeypatch.setattr(cli_module, "import_configured_usage_api", counted_import)

    first = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "usage", "today"],
    )
    second = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "usage", "today"],
    )
    third = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "today",
            "--refresh",
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert third.exit_code == 0, third.output
    assert calls == ["quick", "full"]


def test_cli_usage_supports_explicit_since_until_boundaries(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
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
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "--config", str(config_path), "refresh"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--since",
            "2000-01-01T00:00:00Z",
            "--until",
            "2100-01-01T00:00:00Z",
            "--utc",
            "--json",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["filters"]["since_ms"] == 946684800000
    assert payload["filters"]["until_ms"] == 4102444800000
    assert payload["filters"]["timezone"] == "UTC"
    assert payload["totals"]["total"] == 1500


def test_cli_usage_supports_price_state_sort_limit(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "usage",
            "summary",
            "--json",
            "--price-state",
            "unpriced",
            "--sort",
            "tokens",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["display_filters"]["sort"] == "tokens"
    assert payload["display_filters"]["limit"] == 1
    assert [row["model_id"] for row in payload["by_model"]] == ["gpt-5.2-codex"]
    assert payload["totals"]["total"] == 1940


def test_cli_usage_summary_human_output_contains_by_provider(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "test"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(app, ["--db", str(state_db), "usage", "summary"])

    assert result.exit_code == 0, result.output
    assert "By provider" in result.output
    assert "By harness" in result.output

    provider_section = result.output.split("By provider", 1)[1].split("By harness", 1)[
        0
    ]
    harness_section = result.output.split("By harness", 1)[1].split("By model", 1)[0]
    assert "(none)" not in provider_section
    assert "(none)" not in harness_section


def test_cli_usage_machines_json_and_machine_filter(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    remote_machine_id = "a17b91de00000001"
    conn = connect(state_db)
    try:
        migrate(conn)
        local_machine_id = get_local_machine_id(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "local-machine-1",
                    created_ms=1_777_801_200_000,
                    tokens=TokenBreakdown(input=10, output=5),
                )
            ],
            origin_machine_id=local_machine_id,
        )
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "remote-machine-2",
                    created_ms=1_777_801_201_000,
                    tokens=TokenBreakdown(input=8, output=2),
                )
            ],
            origin_machine_id=remote_machine_id,
        )
    finally:
        conn.close()

    all_result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "machines", "--json", "--no-refresh"],
    )
    assert all_result.exit_code == 0, all_result.output
    all_payload = json.loads(all_result.output)
    assert "by_machine" in all_payload
    assert len(all_payload["by_machine"]) == 2

    filtered = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--machine",
            remote_machine_id[:8],
            "--json",
            "--no-refresh",
        ],
    )
    assert filtered.exit_code == 0, filtered.output
    payload = json.loads(filtered.output)
    assert len(payload["by_machine"]) == 1
    assert payload["by_machine"][0]["machine_id"] == remote_machine_id


def test_cli_usage_machine_selector_ambiguous_name_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    machine_a = "1234abcd11112222"
    machine_b = "1234abcd33334444"
    conn = connect(state_db)
    try:
        migrate(conn)
        upsert_machine(
            conn,
            machine_id=machine_a,
            name=None,
            seen_ms=1_777_801_200_000,
            is_local=False,
        )
        upsert_machine(
            conn,
            machine_id=machine_b,
            name=None,
            seen_ms=1_777_801_201_000,
            is_local=False,
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--machine",
            "1234abcd",
            "--json",
            "--no-refresh",
        ],
    )
    assert result.exit_code != 0
    assert "ambiguous" in result.output
    assert "1234abcd" in result.output


def test_cli_usage_today_plain_default_is_borderless(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "today", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "By provider" in result.output
    assert "By harness" in result.output
    assert "By model" in result.output
    assert "By activity" in result.output
    assert not any(ch in result.output for ch in "┏┌╭┳┬╮")


def test_cli_usage_today_rich_applies_to_table_sections(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "today", "--rich", "--no-refresh"],
    )
    _assert_rich_result_or_missing_dependency(result)
    if result.exit_code == 0:
        assert "By provider" in result.output
        assert "By harness" in result.output
        assert "By model" in result.output
        assert "By activity" in result.output


def test_cli_usage_summary_json_contains_by_provider(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "test"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(app, ["--db", str(state_db), "usage", "summary", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "by_provider" in payload
    assert payload["by_provider"][0]["provider_id"] == "anthropic"


def test_cli_usage_sessions_human_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "toktrail usage sessions" in result.output
    assert "Area:" in result.output
    assert "Source:" in result.output
    assert "Token usage:" in result.output
    assert "Costs:" in result.output


def test_cli_usage_sessions_last_human_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--last", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Token usage:") == 1


def test_cli_usage_sessions_limit_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--limit",
            "5",
            "--json",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "usage_sessions"
    assert isinstance(payload["sessions"], list)
    assert len(payload["sessions"]) <= 5


def test_cli_usage_sessions_breakdown(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--breakdown",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Breakdown:" in result.output


def test_cli_usage_sessions_filters_harness_and_source_session(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--harness",
            "opencode",
            "--source-session",
            "ses-1",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ses-1" in result.output


def test_cli_usage_sessions_human_output_shows_cwd_when_available(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_file = tmp_path / "codex" / "session.jsonl"
    create_codex_session_file_with_cwd(source_file, "/tmp/project")

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "codex",
            "--source",
            str(source_file),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--no-refresh"],
    )

    assert result.exit_code == 0, result.output
    assert "CWD:  /tmp/project" in result.output


def test_cli_usage_sessions_no_refresh_uses_existing_state_only(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    # Import once
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    # --no-refresh should not import new rows
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "toktrail usage sessions" in result.output


def test_cli_usage_sessions_today_filters_to_current_day(monkeypatch, tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "today",
                    source_session_id="ses-today",
                    created_ms=int(
                        datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
                make_cli_usage_event(
                    "yesterday",
                    source_session_id="ses-yesterday",
                    created_ms=int(
                        datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
            ],
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2026, 5, 11, 12, 0, tzinfo=tz),
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--today",
            "--timezone",
            "UTC",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ses-today" in result.output
    assert "ses-yesterday" not in result.output


def test_cli_usage_sessions_yesterday_filters_to_previous_day(
    monkeypatch, tmp_path
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "today-2",
                    source_session_id="ses-today-2",
                    created_ms=int(
                        datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
                make_cli_usage_event(
                    "yesterday-2",
                    source_session_id="ses-yesterday-2",
                    created_ms=int(
                        datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc).timestamp()
                        * 1000
                    ),
                    tokens=TokenBreakdown(input=10, output=2),
                ),
            ],
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2026, 5, 11, 12, 0, tzinfo=tz),
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--yesterday",
            "--timezone",
            "UTC",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ses-yesterday-2" in result.output
    assert "ses-today-2" not in result.output


def test_cli_usage_sessions_period_conflicts_with_since(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--today",
            "--since",
            "2026-05-11",
            "--no-refresh",
        ],
    )
    assert result.exit_code != 0
    assert "Use either a named period or --since/--until" in result.output


def test_cli_usage_sessions_table_restores_legacy_columns(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)
    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--table", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "machine" in result.output
    assert "cache_r" in result.output
    assert "unpriced" in result.output


def test_cli_usage_sessions_period_default_limit_is_unbounded(
    monkeypatch, tmp_path
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        events = [
            make_cli_usage_event(
                f"bulk-{idx}",
                source_session_id=f"ses-{idx}",
                created_ms=int(
                    datetime(2026, 5, 11, 8, idx % 60, tzinfo=timezone.utc).timestamp()
                    * 1000
                ),
                tokens=TokenBreakdown(input=1, output=1),
            )
            for idx in range(12)
        ]
        insert_usage_events(conn, None, events)
    finally:
        conn.close()

    monkeypatch.setattr(
        "toktrail.periods.current_time_in_zone",
        lambda tz: datetime(2026, 5, 11, 12, 0, tzinfo=tz),
    )
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--today",
            "--timezone",
            "UTC",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Token usage:") == 12


def test_cli_usage_runs_human_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "toktrail usage runs" in result.output


def test_cli_usage_runs_rich_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(app, ["--db", str(state_db), "run", "start", "--name", "usage-runs"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    plain = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--no-refresh"],
    )
    assert plain.exit_code == 0, plain.output
    assert not any(ch in plain.output for ch in "┏┌╭")

    rich = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--rich", "--no-refresh"],
    )
    _assert_rich_result_or_missing_dependency(rich)
    if rich.exit_code == 0:
        assert "toktrail usage runs" in rich.output


def test_cli_usage_sessions_rich_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    plain = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--no-refresh"],
    )
    assert plain.exit_code == 0, plain.output
    assert not any(ch in plain.output for ch in "┏┌╭")

    rich = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "sessions",
            "--rich",
            "--table",
            "--no-refresh",
        ],
    )
    _assert_rich_result_or_missing_dependency(rich)
    if rich.exit_code == 0:
        assert "toktrail usage sessions" in rich.output


def test_cli_usage_daily_rich_output(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    plain = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "daily", "--no-refresh"],
    )
    assert plain.exit_code == 0, plain.output
    assert not any(ch in plain.output for ch in "┏┌╭")

    rich = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "daily", "--rich", "--no-refresh"],
    )
    _assert_rich_result_or_missing_dependency(rich)
    if rich.exit_code == 0:
        assert "toktrail usage daily" in rich.output


def test_cli_usage_day_and_week_aliases(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    refresh = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )
    assert refresh.exit_code == 0, refresh.output

    day = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "day", "--json", "--no-refresh"],
    )
    assert day.exit_code == 0, day.output
    assert json.loads(day.output)["filters"]["period"] == "today"

    week = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "week", "--json", "--no-refresh"],
    )
    assert week.exit_code == 0, week.output
    assert json.loads(week.output)["filters"]["period"] == "this-week"


def test_cli_usage_runs_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "opencode",
            "--source",
            str(source_db),
        ],
    )

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "runs", "--json", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "usage_runs"
    assert "runs" in payload
    assert "totals" in payload


def test_cli_usage_summary_prints_area_filter_semantics(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "summary-area",
                    source_session_id="ses-summary-area",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=30, output=5),
                )
            ],
        )
        machine_id = get_local_machine_id(conn)
        area = ensure_area(conn, "work/odoo")
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-summary-area",
        )
        conn.commit()
    finally:
        conn.close()

    descendants = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--area",
            "work",
            "--no-refresh",
        ],
    )
    assert descendants.exit_code == 0, descendants.output
    assert "Area filter: work (including descendants)" in descendants.output

    exact = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--area",
            "work",
            "--area-exact",
            "--no-refresh",
        ],
    )
    assert exact.exit_code == 0, exact.output
    assert "Area filter: work (exact only)" in exact.output


def test_cli_usage_summary_area_filters(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "area-child",
                    source_session_id="ses-area-child",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=100, output=20),
                ),
                make_cli_usage_event(
                    "area-unassigned",
                    source_session_id="ses-area-unassigned",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=50, output=5),
                ),
            ],
        )
        machine_id = get_local_machine_id(conn)
        area = ensure_area(conn, "work/odoo")
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-area-child",
        )
        conn.commit()
    finally:
        conn.close()

    parent_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--area",
            "work",
            "--json",
            "--no-refresh",
        ],
    )
    assert parent_result.exit_code == 0, parent_result.output
    parent_payload = json.loads(parent_result.output)
    assert parent_payload["totals"]["total"] == 120

    exact_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--area",
            "work",
            "--area-exact",
            "--json",
            "--no-refresh",
        ],
    )
    assert exact_result.exit_code == 0, exact_result.output
    exact_payload = json.loads(exact_result.output)
    assert exact_payload["totals"]["total"] == 0

    unassigned_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--unassigned-area",
            "--json",
            "--no-refresh",
        ],
    )
    assert unassigned_result.exit_code == 0, unassigned_result.output
    unassigned_payload = json.loads(unassigned_result.output)
    assert unassigned_payload["totals"]["total"] == 55

    conflict_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--area",
            "work",
            "--unassigned-area",
            "--no-refresh",
        ],
    )
    assert conflict_result.exit_code != 0
    assert (
        "Use only one of --area, --area-leaf, or --unassigned-area."
        in conflict_result.output
    )


def test_cli_usage_summary_area_unique_suffix_selector(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "suffix-toktrail",
                    source_session_id="ses-suffix-toktrail",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=9, output=3),
                ),
                make_cli_usage_event(
                    "suffix-work",
                    source_session_id="ses-suffix-work",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=5, output=1),
                ),
            ],
        )
        machine_id = get_local_machine_id(conn)
        toktrail_area = ensure_area(conn, "privat/toktrail")
        work_area = ensure_area(conn, "work/odoo")
        assign_area_to_source_session(
            conn,
            area_id=toktrail_area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-suffix-toktrail",
        )
        assign_area_to_source_session(
            conn,
            area_id=work_area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-suffix-work",
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--area",
            "toktrail",
            "--json",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["total"] == 12
    assert payload["filters"]["area"] == "toktrail"
    assert payload["filters"]["area_match"] == "unique_suffix"
    assert payload["filters"]["area_matches"] == ["privat/toktrail"]


def test_cli_usage_summary_area_unique_suffix_ambiguous(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        ensure_area(conn, "privat/toktrail")
        ensure_area(conn, "work/toktrail")
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--area",
            "toktrail",
            "--no-refresh",
        ],
    )

    assert result.exit_code != 0
    assert "Area selector 'toktrail' is ambiguous." in result.output
    assert "privat/toktrail" in result.output
    assert "work/toktrail" in result.output


def test_cli_usage_summary_area_leaf_selector(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "leaf-a",
                    source_session_id="ses-leaf-a",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=4, output=1),
                ),
                make_cli_usage_event(
                    "leaf-b",
                    source_session_id="ses-leaf-b",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=7, output=2),
                ),
                make_cli_usage_event(
                    "leaf-docs",
                    source_session_id="ses-leaf-docs",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=30, output=5),
                ),
            ],
        )
        machine_id = get_local_machine_id(conn)
        toktrail_tests = ensure_area(conn, "privat/toktrail/tests")
        taskledger_tests = ensure_area(conn, "privat/taskledger/tests")
        docs_area = ensure_area(conn, "privat/toktrail/docs")
        assign_area_to_source_session(
            conn,
            area_id=toktrail_tests.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-leaf-a",
        )
        assign_area_to_source_session(
            conn,
            area_id=taskledger_tests.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-leaf-b",
        )
        assign_area_to_source_session(
            conn,
            area_id=docs_area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-leaf-docs",
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "usage",
            "summary",
            "--area-leaf",
            "tests",
            "--json",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["total"] == 14
    assert payload["filters"]["area"] == "tests"
    assert payload["filters"]["area_match"] == "leaf"
    assert payload["filters"]["area_matches"] == [
        "privat/taskledger/tests",
        "privat/toktrail/tests",
    ]


def test_cli_usage_sessions_table_shows_area_column(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "table-area",
                    source_session_id="ses-table-area",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=8, output=1),
                )
            ],
        )
        machine_id = get_local_machine_id(conn)
        area = ensure_area(conn, "work/odoo")
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-table-area",
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "sessions", "--table", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    assert "area" in result.output
    assert "work/odoo" in result.output


def test_cli_usage_areas_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "areas-child",
                    source_session_id="ses-areas-child",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=20, output=4),
                ),
                make_cli_usage_event(
                    "areas-unassigned",
                    source_session_id="ses-areas-unassigned",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=12, output=3),
                ),
            ],
        )
        machine_id = get_local_machine_id(conn)
        area = ensure_area(conn, "work/odoo")
        assign_area_to_source_session(
            conn,
            area_id=area.id,
            origin_machine_id=machine_id,
            harness="opencode",
            source_session_id="ses-areas-child",
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        ["--db", str(state_db), "usage", "areas", "--json", "--no-refresh"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "usage_areas"
    assert "areas" in payload
    assert "totals" in payload
    assert all("area_sync_id" in row for row in payload["areas"])
    assert any(row["path"] == "work" for row in payload["areas"])
    assert any(row["path"] == "work/odoo" for row in payload["areas"])
