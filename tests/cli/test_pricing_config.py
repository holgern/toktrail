from __future__ import annotations

import json

from typer.testing import CliRunner

from tests.cli.helpers import (
    _toml_path_value,
    create_source_db,
    setup_pricing_status_fixture,
)
from toktrail.cli import app


def test_cli_config_path_init_and_validate(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    prices_path = config_path.with_name("prices.toml")
    prices_dir = config_path.with_name("prices")
    subscriptions_path = config_path.with_name("subscriptions.toml")

    path_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "path"],
    )
    assert path_result.exit_code == 0, path_result.output
    assert f"config:        {config_path}" in path_result.output
    assert f"prices:        {prices_path}" in path_result.output
    assert f"prices dir:    {prices_dir}" in path_result.output
    assert f"subscriptions: {subscriptions_path}" in path_result.output

    init_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )
    assert init_result.exit_code == 0, init_result.output
    assert config_path.exists()
    assert prices_path.exists()
    assert prices_dir.exists()
    assert subscriptions_path.exists()

    validate_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "validate"],
    )
    assert validate_result.exit_code == 0, validate_result.output
    assert "Config valid:" in validate_result.output
    assert "virtual prices:" in validate_result.output


def test_cli_config_prices_lists_virtual_prices(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    result = runner.invoke(
        app,
        ["--config", str(config_path), "prices", "list", "--provider", "openai"],
    )

    assert result.exit_code == 0, result.output
    assert "table" in result.output
    assert "provider" in result.output
    assert "model" in result.output
    assert "gpt-5-mini" in result.output


def test_cli_config_prices_json_includes_effective_fallback_prices(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "list",
            "--model",
            "gpt-5-mini",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["cache_write_usd_per_1m"] is None
    assert payload[0]["effective_cache_write_usd_per_1m"] == 0.25
    assert payload[0]["reasoning_usd_per_1m"] is None
    assert payload[0]["effective_reasoning_usd_per_1m"] == 2.0


def test_cli_config_prices_filters_provider_model_query_category_release(
    tmp_path,
) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "list",
            "--table",
            "all",
            "--provider",
            "openai",
            "--model",
            "GPT 5 mini",
            "--query",
            "mini",
            "--category",
            "Lightweight",
            "--release-status",
            "ga",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["provider"] == "openai"
    assert payload[0]["model"] == "gpt-5-mini"


def test_cli_config_prices_rejects_invalid_filter_values(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )

    bad_table = runner.invoke(
        app,
        ["--config", str(config_path), "prices", "list", "--table", "bogus"],
    )
    bad_sort = runner.invoke(
        app,
        ["--config", str(config_path), "prices", "list", "--sort", "bogus"],
    )

    assert bad_table.exit_code == 1
    assert "Unsupported --table" in bad_table.output
    assert bad_sort.exit_code == 1
    assert "Unsupported --sort" in bad_sort.output


def test_cli_root_prices_and_subscriptions_overrides(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    prices_path = tmp_path / "prices.toml"
    subscriptions_path = tmp_path / "subscriptions.toml"
    config_path.write_text(
        """
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false
""".strip(),
        encoding="utf-8",
    )
    prices_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5-mini"
input_usd_per_1m = 0.25
output_usd_per_1m = 2.0
""".strip(),
        encoding="utf-8",
    )
    subscriptions_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]

[[subscriptions.windows]]
period = "monthly"
limit_usd = 100
reset_at = "2026-05-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

    prices_result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "--prices",
            str(prices_path),
            "prices",
            "list",
            "--provider",
            "openai",
            "--json",
        ],
    )
    assert prices_result.exit_code == 0, prices_result.output
    prices_payload = json.loads(prices_result.output)
    assert prices_payload[0]["provider"] == "openai"

    show_result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "--prices",
            str(prices_path),
            "--subscriptions",
            str(subscriptions_path),
            "config",
            "show",
        ],
    )
    assert show_result.exit_code == 0, show_result.output
    assert f"subs path:       {subscriptions_path}" in show_result.output


def test_cli_config_show_uses_git_tracked_costing_paths(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo = tmp_path / "toktrail-state"
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[sync.git]
repo = "{_toml_path_value(repo)}"
track = ["prices", "provider-prices", "subscriptions"]
""".strip(),
        encoding="utf-8",
    )

    show_result = runner.invoke(
        app,
        ["--config", str(config_path), "config", "show"],
    )

    assert show_result.exit_code == 0, show_result.output
    assert f"prices path:     {repo / 'config' / 'prices.toml'}" in show_result.output
    assert f"prices dir:      {repo / 'config' / 'prices'}" in show_result.output
    assert (
        f"subs path:       {repo / 'config' / 'subscriptions.toml'}"
        in show_result.output
    )


def test_cli_prices_parse_uses_git_tracked_provider_prices_dir(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo = tmp_path / "toktrail-state"
    input_path = tmp_path / "openai-pricing.jsx"
    config_path.write_text(
        f"""
config_version = 1

[sync.git]
repo = "{_toml_path_value(repo)}"
track = ["provider-prices"]
""".strip(),
        encoding="utf-8",
    )
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
        ],
    )

    assert result.exit_code == 0, result.output
    provider_path = repo / "config" / "prices" / "openai.toml"
    assert provider_path.exists()
    assert 'provider = "openai"' in provider_path.read_text(encoding="utf-8")


def test_cli_pricing_parse_openai_standard_to_stdout(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--out",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[[pricing.virtual]]" in result.output
    assert 'provider = "openai"' in result.output


def test_cli_pricing_parse_defaults_to_provider_file(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    runner.invoke(app, ["--config", str(config_path), "config", "init"])
    target_path = config_path.with_name("prices") / "openai.toml"
    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert target_path.exists()
    assert f"Wrote prices TOML: {target_path}" in result.output


def test_cli_pricing_parse_accepts_output_alias(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "openai-pricing.jsx"
    output_path = tmp_path / "custom-openai.toml"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exists()


def test_cli_pricing_parse_output_dash_prints_stdout(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--output",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[[pricing.virtual]]" in result.output
    assert not (config_path.with_name("prices") / "openai.toml").exists()


def test_cli_pricing_parse_json_writes_file(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["wrote"] is True
    assert (config_path.with_name("prices") / "openai.toml").exists()


def test_cli_pricing_parse_json_dry_run_does_not_write(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--json",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["wrote"] is False
    assert payload["dry_run"] is True
    assert not (config_path.with_name("prices") / "openai.toml").exists()


def test_cli_pricing_parse_zai_to_stdout(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "zai-pricing.md"
    input_path.write_text(
        """
### Text Models
| Model | Input | Cached Input | Output |
| --- | --- | --- | --- |
| GLM-5.1 | $1.4 | $0.26 | $4.4 |
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "zai",
            "--input",
            str(input_path),
            "--out",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert 'provider = "zai"' in result.output
    assert 'model = "glm-5.1"' in result.output


def test_cli_pricing_parse_opencode_go_actual_to_stdout(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "opencode-go.txt"
    input_path.write_text(
        """
Model        Input    Output    Cached Read    Cached Write
GLM 5.1      $1.40    $4.40     $0.26          -
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "opencode-go",
            "--table",
            "actual",
            "--input",
            str(input_path),
            "--out",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "[[pricing.actual]]" in result.output
    assert 'provider = "opencode-go"' in result.output


def test_cli_pricing_parse_github_copilot_to_stdout(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "github-copilot.md"
    input_path.write_text(
        """
OpenAI
Model\tRelease status\tCategory\tInput\tCached input\tOutput
GPT-5.2\tGA\tVersatile\t$1.75\t$0.175\t$14.00
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "github-copilot",
            "--input",
            str(input_path),
            "--out",
            "-",
        ],
    )

    assert result.exit_code == 0, result.output
    assert 'provider = "github-copilot"' in result.output
    assert 'model = "gpt-5.2"' in result.output


def test_cli_pricing_parse_merge_replaces_provider_rows(tmp_path) -> None:
    runner = CliRunner()
    input_path = tmp_path / "openai-pricing.jsx"
    prices_path = tmp_path / "prices.toml"
    input_path.write_text(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 6, 0.6, 36] ]}',
        encoding="utf-8",
    )
    prices_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30

[[pricing.virtual]]
provider = "anthropic"
model = "claude-sonnet-4"
input_usd_per_1m = 3
output_usd_per_1m = 15
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--out",
            str(prices_path),
            "--merge",
        ],
    )

    assert result.exit_code == 0, result.output
    merged = prices_path.read_text(encoding="utf-8")
    assert "input_usd_per_1m = 6.0" in merged
    assert 'provider = "anthropic"' in merged


def test_cli_pricing_parse_context_tier_preserves_variants_without_warning(
    tmp_path,
) -> None:
    runner = CliRunner()
    input_path = tmp_path / "openai-pricing.jsx"
    input_path.write_text(
        """
TextTokenPricingTables tier="standard" rows={[
  ["gpt-5.5 (<272K context length)", 5, 0.5, 30],
  ["GPT 5.5 (> 272K tokens)", 6, 0.6, 36],
]}
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "prices",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["warnings"] == []
    assert payload["price_count"] == 2


def test_cli_config_prices_loads_provider_directory(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(tmp_path / "missing-opencode.db")}"
""".strip(),
        encoding="utf-8",
    )
    provider_path = config_path.with_name("prices") / "openai.toml"
    provider_path.parent.mkdir(parents=True, exist_ok=True)
    provider_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "prices",
            "list",
            "--provider",
            "openai",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload
    assert payload[0]["provider"] == "openai"
    assert "context_min_tokens" in payload[0]
    assert "context_max_tokens" in payload[0]
    assert payload[0]["context_basis"] == "prompt_like"


def test_cli_config_show_lists_price_paths(tmp_path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "config" / "toktrail.toml"
    runner.invoke(app, ["--config", str(config_path), "config", "init"])
    provider_path = config_path.with_name("prices") / "openai.toml"
    provider_path.write_text(
        """
config_version = 1

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.5"
input_usd_per_1m = 5
output_usd_per_1m = 30
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--config", str(config_path), "config", "show"])

    assert result.exit_code == 0, result.output
    assert f"prices dir:      {config_path.with_name('prices')}" in result.output
    assert "price files:" in result.output
    assert str(provider_path) in result.output


def test_cli_pricing_list_used_only_reports_used_models(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "prices",
            "list",
            "--used-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [row["provider_id"] for row in payload] == ["anthropic", "openai-codex"]
    assert [row["model_id"] for row in payload] == [
        "claude-sonnet-4",
        "gpt-5.2-codex",
    ]


def test_cli_pricing_list_missing_only_reports_unconfigured_used_models(
    tmp_path,
) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "prices",
            "list",
            "--missing-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == [
        {
            "required": ["virtual"],
            "harness": "opencode",
            "provider_id": "openai-codex",
            "model_id": "gpt-5.2-codex",
            "thinking_level": None,
            "message_count": 1,
            "input": 400,
            "output": 40,
            "reasoning": 10,
            "cache_read": 0,
            "cache_write": 0,
            "cache_output": 0,
            "total": 440,
            "prompt_total": 400,
            "output_total": 40,
            "accounting_total": 450,
        }
    ]


def test_cli_pricing_list_used_only_auto_refreshes_configured_sources(tmp_path) -> None:
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
    stale = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "prices",
            "list",
            "--used-only",
            "--json",
            "--no-refresh",
        ],
    )
    assert stale.exit_code == 0, stale.output
    assert json.loads(stale.output) == []

    refreshed = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "prices",
            "list",
            "--used-only",
            "--json",
        ],
    )
    assert refreshed.exit_code == 0, refreshed.output
    payload = json.loads(refreshed.output)
    assert len(payload) > 0
