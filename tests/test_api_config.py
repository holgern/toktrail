from __future__ import annotations

import pytest

from toktrail.api.config import (
    config_exists,
    config_summary,
    init_config,
    render_config_template,
)
from toktrail.errors import ConfigurationError, InvalidAPIUsageError


def test_api_config_init_and_summary(tmp_path) -> None:
    config_path = tmp_path / "config" / "toktrail.toml"
    prices_path = config_path.with_name("prices.toml")
    prices_dir = config_path.with_name("prices")

    assert config_exists(config_path) is False
    created = init_config(config_path, template="copilot")
    summary = config_summary(config_path)

    assert created == config_path
    assert config_exists(config_path) is True
    assert prices_path.exists()
    assert prices_dir.exists()
    assert summary["path"] == str(config_path)
    assert summary["exists"] is True
    assert summary["manual_prices_path"] == str(prices_path)
    assert summary["provider_prices_dir"] == str(prices_dir)
    assert summary["manual_prices_exists"] is True
    assert summary["provider_prices_exists"] is True
    assert summary["price_paths"] == [str(prices_path)]
    assert summary["virtual_price_count"] > 0


def test_api_config_init_requires_force_to_overwrite(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    init_config(config_path)

    with pytest.raises(InvalidAPIUsageError, match="already exists"):
        init_config(config_path)


def test_api_config_invalid_toml_raises_configuration_error(tmp_path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("config_version = [", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Invalid toktrail config"):
        config_summary(config_path)


def test_api_render_config_template_rejects_unknown_template() -> None:
    with pytest.raises(
        ConfigurationError, match="Unsupported toktrail config template"
    ):
        render_config_template("unknown")
