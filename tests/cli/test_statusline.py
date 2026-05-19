from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.cli.helpers import (
    _future_ms,
    _toml_path_value,
    make_cli_usage_event,
    write_jsonl_rows,
)
from toktrail.cli import app
from toktrail.db import (
    connect,
    ensure_area,
    insert_usage_events,
    migrate,
    set_active_area,
)
from toktrail.models import TokenBreakdown


def test_cli_statusline_top_level_matches_usage_wrapper(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "statusline",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=1_200, output=300, cache_read=500),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    top_level = runner.invoke(
        app,
        ["--db", str(state_db), "statusline", "--no-refresh"],
    )
    legacy = runner.invoke(
        app, ["--db", str(state_db), "usage", "statusline", "--no-refresh"]
    )

    assert top_level.exit_code == 0, top_level.output
    assert legacy.exit_code == 0, legacy.output
    assert top_level.output == legacy.output
    assert "opencode" in top_level.output
    assert "tok" in top_level.output


def test_cli_statusline_json_shape(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "json",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=400, output=100, cache_read=200),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        app, ["--db", str(state_db), "statusline", "--no-refresh", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "statusline"
    assert payload["harness"] == "opencode"
    assert payload["tokens"]["total"] == 500
    assert "line" in payload
    assert payload["cache"]["cached_tokens"] == 200


def test_cli_statusline_area_element(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[statusline]
elements = ["area", "tokens"]
""".strip(),
        encoding="utf-8",
    )
    conn = connect(state_db)
    try:
        migrate(conn)
        area = ensure_area(conn, "privat/toktrail")
        set_active_area(conn, area.id)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "statusline-area",
                    source_session_id="ses-statusline-area",
                    created_ms=_future_ms(),
                    tokens=TokenBreakdown(input=9, output=2),
                )
            ],
        )
        conn.commit()
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "statusline",
            "--no-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "area privat/toktrail" in result.output


def test_cli_statusline_harnessbridge_refresh_always_bypasses_cached_empty_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_db = tmp_path / "toktrail.db"
    source_dir = tmp_path / "harnessbridge-sessions"
    source_file = source_dir / "hb.jsonl"
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["harnessbridge"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
harnessbridge = "{_toml_path_value(source_dir)}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(config_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))

    runner = CliRunner()
    init_result = runner.invoke(app, ["--db", str(state_db), "init"])
    assert init_result.exit_code == 0, init_result.output

    empty_result = runner.invoke(
        app,
        ["--db", str(state_db), "statusline", "--harness", "harnessbridge"],
    )
    assert empty_result.exit_code == 0, empty_result.output
    assert "no usage sources" in empty_result.output

    write_jsonl_rows(
        source_file,
        [
            {
                "type": "session",
                "id": "hb_20260513T161435Z_8f434346",
                "harness": "pi",
                "accounting": "primary",
                "started_at": "2026-05-13T16:14:35.963000+00:00",
            },
            {
                "type": "usage",
                "id": "usage_0001",
                "harness": "pi",
                "timestamp": "2026-05-13T16:14:44.720215+00:00",
                "provider": "zai",
                "model": "zai/glm-5.1",
                "dedup_key": "harnessbridge:hb_20260513T161435Z_8f434346:usage_0001",
                "tokens": {"input": 815, "output": 48, "cacheRead": 1024},
                "cost": {"total": "0.0009686"},
            },
        ],
    )

    refreshed_result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "statusline",
            "--harness",
            "harnessbridge",
            "--refresh",
            "always",
        ],
    )

    assert refreshed_result.exit_code == 0, refreshed_result.output
    assert "no usage sources" not in refreshed_result.output
    assert "pi" in refreshed_result.output
    assert "glm-5.1" in refreshed_result.output


def test_cli_statusline_shows_compact_unpriced_marker(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "unpriced",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=400, output=100),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["--db", str(state_db), "statusline", "--no-refresh"])

    assert result.exit_code == 0, result.output
    assert "?1" in result.output


def test_cli_statusline_renders_stale_element_when_configured(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
config_version = 1

[statusline]
elements = ["harness", "stale", "tokens"]

[statusline.cache]
stale_after_secs = 60
""".strip(),
        encoding="utf-8",
    )
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "stale",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=400, output=100),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "statusline",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "stale " in result.output
    assert "opencode" in result.output


def test_cli_statusline_test_outputs_diagnostics(tmp_path: Path) -> None:
    state_db = tmp_path / "toktrail.db"
    conn = connect(state_db)
    try:
        migrate(conn)
        insert_usage_events(
            conn,
            None,
            [
                make_cli_usage_event(
                    "diag",
                    created_ms=1_000,
                    tokens=TokenBreakdown(input=700, output=150, cache_read=50),
                )
            ],
        )
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--db", str(state_db), "statusline", "test", "--no-refresh"],
    )

    assert result.exit_code == 0, result.output
    assert "Source:" in result.output
    assert "Model:" in result.output
    assert "Output cache: miss" in result.output
    assert "Line:" in result.output


def test_cli_statusline_install_starship_prints_snippet() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["statusline", "install", "--target", "starship"])

    assert result.exit_code == 0, result.output
    assert "[custom.toktrail]" in result.output
    assert "toktrail statusline --no-refresh" in result.output


def test_cli_statusline_config_show_and_set(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    runner = CliRunner()

    set_result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "statusline",
            "config",
            "set",
            "basis",
            "source",
        ],
    )
    show_result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "statusline",
            "config",
            "show",
        ],
    )

    assert set_result.exit_code == 0, set_result.output
    assert 'basis = "source"' in config_path.read_text(encoding="utf-8")
    assert show_result.exit_code == 0, show_result.output
    assert "basis:         source" in show_result.output
