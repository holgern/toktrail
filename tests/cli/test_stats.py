"""CLI tests for toktrail stats command."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("cli_config")



@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    from toktrail.api.config import init_config
    from toktrail.api.sessions import init_state

    db_path = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    init_state(db_path)
    init_config(config_path, template="copilot")
    monkeypatch.setenv("TOKTRAIL_DB", str(db_path))
    monkeypatch.setenv("TOKTRAIL_CONFIG", str(config_path))


def _app():
    from toktrail.cli import app

    return app


def test_stats_human_output() -> None:
    result = runner.invoke(_app(), ["stats"])
    assert result.exit_code == 0
    assert "Stats v1" in result.output
    assert "Messages:" in result.output
    assert "Tokens:" in result.output


def test_stats_json_output() -> None:
    result = runner.invoke(_app(), ["stats", "--format", "json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert data["schema_version"] == 1
    assert "totals" in data
    assert "range" in data
    assert "cache" in data
    assert "distributions" in data
    assert "archetypes" in data
    assert "health" in data
    assert "area_mix" in data
    assert "generated_at_ms" in data


def test_stats_json_has_archetype_counts() -> None:
    result = runner.invoke(_app(), ["stats", "--format", "json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    archetypes = data["archetypes"]
    assert "counts" in archetypes
    assert "fractions" in archetypes
    for key in ("automation", "quick", "standard", "deep", "marathon"):
        assert key in archetypes["counts"]


def test_stats_json_has_distribution_sections() -> None:
    result = runner.invoke(_app(), ["stats", "--format", "json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    dist = data["distributions"]
    assert "user_messages" in dist
    assert "total_messages" in dist
    assert "histogram" in dist["user_messages"]


def test_stats_json_cache_section() -> None:
    result = runner.invoke(_app(), ["stats", "--format", "json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    cache = data["cache"]
    assert "cache_read_ratio" in cache
    assert "cache_write_ratio" in cache
    assert "cache_read_tokens" in cache
    assert "cache_write_tokens" in cache


def test_stats_json_area_mix_empty_db() -> None:
    result = runner.invoke(_app(), ["stats", "--format", "json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert isinstance(data["area_mix"], list)
    assert len(data["area_mix"]) == 0


def test_stats_invalid_format() -> None:
    result = runner.invoke(_app(), ["stats", "--format", "xml"])
    assert result.exit_code != 0
    assert "human" in result.output or "json" in result.output
