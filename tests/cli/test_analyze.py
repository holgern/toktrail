from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.cli.helpers import (
    create_opencode_cache_analysis_source_db,
)
from toktrail.cli import app


def test_cli_analyze_session_opencode_last_human_output(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "--source",
            str(source_db),
            "--last",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "opencode source session ses-cache" in result.output
    assert "estimated source cache loss:" in result.output
    assert "Per call" in result.output
    assert "Clusters" in result.output


def test_cli_analyze_session_opencode_json_shape(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "--source",
            str(source_db),
            "--last",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "session_cache_analysis"
    assert payload["harness"] == "opencode"
    assert payload["source_session_id"] == "ses-cache"
    assert payload["call_count"] == 2
    assert payload["totals"]["cache_read"] == 120000
    assert payload["totals"]["unpriced_count"] == 2
    assert payload["calls"][0]["ordinal"] == 1
    assert "context_tokens" in payload["calls"][0]
    assert "virtual_price_context_label" in payload["calls"][0]
    assert "missing_price_kinds" in payload["calls"][0]
    assert "call_ordinals" in payload["clusters"][0]
    assert payload["clusters"][0]["call_count"] == 2


def test_cli_analyze_session_opencode_known_source_session_id(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "ses-cache",
            "--source",
            str(source_db),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ses-cache" in result.output


def test_cli_analyze_session_rejects_last_and_source_session_id(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "ses-cache",
            "--source",
            str(source_db),
            "--last",
        ],
    )

    assert result.exit_code == 1
    assert "cannot be used together" in result.output


def test_cli_analyze_session_no_raw_json_in_output(tmp_path: Path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_opencode_cache_analysis_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "analyze",
            "cache",
            "opencode",
            "--source",
            str(source_db),
            "--last",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "raw_json" not in result.output
    assert all("raw_json" not in row for row in payload["calls"])
