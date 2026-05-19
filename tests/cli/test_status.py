from __future__ import annotations

import json

from typer.testing import CliRunner

from tests.cli.helpers import (
    create_copilot_file,
    create_pi_session_file,
    create_source_db,
    create_thinking_source_db,
    setup_pricing_status_fixture,
)
from toktrail.cli import app


def test_cli_status_supports_thinking_filter_and_collapse(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_thinking_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
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

    filtered_split = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "run",
            "status",
            "1",
            "--json",
            "--thinking",
            "high",
            "--split-thinking",
        ],
    )
    split_thinking = runner.invoke(
        app,
        ["--db", str(state_db), "run", "status", "1", "--json", "--split-thinking"],
    )
    collapsed_default = runner.invoke(
        app,
        ["--db", str(state_db), "run", "status", "1", "--json"],
    )
    human = runner.invoke(app, ["--db", str(state_db), "run", "status", "1"])

    assert filtered_split.exit_code == 0, filtered_split.output
    assert split_thinking.exit_code == 0, split_thinking.output
    assert collapsed_default.exit_code == 0, collapsed_default.output
    filtered_split_payload = json.loads(filtered_split.output)
    split_thinking_payload = json.loads(split_thinking.output)
    collapsed_payload = json.loads(collapsed_default.output)
    assert [
        (row["thinking_level"], row["message_count"])
        for row in filtered_split_payload["by_model"]
    ] == [("high", 1)]
    assert sorted(
        [
            (row["thinking_level"], row["message_count"])
            for row in split_thinking_payload["by_model"]
        ]
    ) == [("high", 1), ("low", 1)]
    assert [
        (row["thinking_level"], row["message_count"])
        for row in collapsed_payload["by_model"]
    ] == [(None, 2)]
    assert "reasoning" in human.output


def test_cli_status_filters_by_harness_and_source_session(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    session_file = tmp_path / "sessions" / "encoded-cwd" / "session.jsonl"
    create_source_db(source_db)
    create_pi_session_file(session_file)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
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
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "pi",
            "--source",
            str(session_file),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "run",
            "status",
            "--harness",
            "pi",
            "--source-session",
            "pi_ses_001",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["filters"]["harness"] == "pi"
    assert payload["filters"]["source_session_id"] == "pi_ses_001"
    assert isinstance(payload["filters"]["since_ms"], int)
    assert payload["filters"]["since_ms"] == payload["session"]["started_at_ms"]
    assert payload["totals"]["input"] == 100
    assert payload["totals"]["output"] == 50
    assert payload["by_harness"] == [
        {
            "harness": "pi",
            "message_count": 1,
            "input": 100,
            "output": 50,
            "reasoning": 0,
            "cache_read": 10,
            "cache_write": 5,
            "cache_output": 0,
            "total": 150,
            "prompt_total": 115,
            "output_total": 50,
            "accounting_total": 165,
            "source_cost_usd": "0",
            "actual_cost_usd": "0",
            "virtual_cost_usd": "0",
            "savings_usd": "0",
            "unpriced_count": 1,
        }
    ]


def test_cli_status_with_template_config_computes_copilot_virtual_cost(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "config" / "toktrail.toml"
    copilot_file = tmp_path / "copilot.jsonl"
    create_copilot_file(copilot_file)

    runner.invoke(
        app,
        ["--config", str(config_path), "config", "init", "--template", "copilot"],
    )
    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
    runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "refresh",
            "--harness",
            "copilot",
            "--source",
            str(copilot_file),
        ],
    )

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["totals"]["source_cost_usd"] in ("0", "0.0")
    assert payload["totals"]["actual_cost_usd"] in ("0", "0.0")
    assert float(payload["totals"]["virtual_cost_usd"]) > 0.0
    assert payload["totals"]["savings_usd"] == payload["totals"]["virtual_cost_usd"]


def test_cli_status_human_output_contains_actual_virtual_and_savings(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    create_source_db(source_db)

    runner.invoke(app, ["--db", str(state_db), "init"])
    runner.invoke(
        app, ["--db", str(state_db), "run", "start", "--name", "test-session"]
    )
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

    result = runner.invoke(app, ["--db", str(state_db), "run", "status", "1"])

    assert result.exit_code == 0, result.output
    assert "Costs" in result.output
    assert "actual:" in result.output
    assert "virtual:" in result.output
    assert "savings:" in result.output


def test_cli_status_human_output_lists_unconfigured_models(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["--db", str(state_db), "--config", str(config_path), "run", "status", "1"],
    )

    assert result.exit_code == 0, result.output
    assert "Unconfigured models" in result.output
    assert "openai-codex/gpt-5.2-codex" in result.output
    assert result.output.index("Unconfigured models") < result.output.index(
        "By harness"
    )


def test_cli_status_json_contains_unconfigured_models(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["unconfigured_models"] == [
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


def test_cli_status_price_state_unpriced_filters_model_table(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--json",
            "--price-state",
            "unpriced",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["display_filters"]["price_state"] == "unpriced"
    assert [row["model_id"] for row in payload["by_model"]] == ["gpt-5.2-codex"]
    assert payload["unconfigured_models"][0]["model_id"] == "gpt-5.2-codex"


def test_cli_status_sort_and_limit_apply_to_model_rows_only(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--json",
            "--sort",
            "provider",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["display_filters"] == {
        "price_state": "all",
        "min_messages": None,
        "min_tokens": None,
        "sort": "provider",
        "limit": 1,
    }
    assert [row["provider_id"] for row in payload["by_model"]] == ["anthropic"]
    assert payload["totals"]["total"] == 1940
    assert payload["unconfigured_models"][0]["provider_id"] == "openai-codex"


def test_cli_status_rejects_invalid_display_filter_values(tmp_path) -> None:
    runner, state_db, config_path = setup_pricing_status_fixture(tmp_path)

    bad_price_state = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--price-state",
            "bogus",
        ],
    )
    bad_sort = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "run",
            "status",
            "1",
            "--sort",
            "bogus",
        ],
    )

    assert bad_price_state.exit_code == 1
    assert "Unsupported --price-state" in bad_price_state.output
    assert bad_sort.exit_code == 1
    assert "Unsupported --sort" in bad_sort.output
