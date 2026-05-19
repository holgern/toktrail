from __future__ import annotations

import json
from decimal import Decimal

from typer.testing import CliRunner

from tests.cli.helpers import (
    _ms,
    _toml_path_value,
    create_opencode_go_source_db,
    create_zai_source_db,
    write_subscriptions_config,
)
from toktrail.cli import app
from toktrail.db import (
    connect,
    insert_usage_events,
)
from toktrail.models import TokenBreakdown, UsageEvent


def test_cli_subscriptions_auto_refreshes_before_summarizing(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
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
            "subscriptions",
            "--json",
            "--no-refresh",
            "--now-ms",
            "1700000000000",
        ],
    )
    assert stale.exit_code == 0, stale.output
    stale_payload = json.loads(stale.output)
    stale_used = sum(
        float(period["used_usd"])
        for sub in stale_payload["subscriptions"]
        for period in sub["periods"]
    )
    assert stale_used == 0.0

    refreshed = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--json",
            "--now-ms",
            "1700000000000",
        ],
    )
    assert refreshed.exit_code == 0, refreshed.output
    refreshed_payload = json.loads(refreshed.output)
    refreshed_used = sum(
        float(period["used_usd"])
        for sub in refreshed_payload["subscriptions"]
        for period in sub["periods"]
    )
    assert refreshed_used > 0.0


def test_cli_subscriptions_prints_5h_window(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    write_subscriptions_config(config_path)

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

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "OpenCode Go (opencode-go)" in result.output
    assert "Billing" in result.output
    assert "net savings" in result.output
    assert "5h" in result.output
    assert "weekly" in result.output
    assert "monthly" in result.output


def test_cli_subscriptions_provider_filter_json_shape(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    write_subscriptions_config(config_path)

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

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--provider",
            "opencode-go",
            "--timezone",
            "Europe/Berlin",
            "--json",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "generated_at_ms" in payload
    assert len(payload["subscriptions"]) == 1
    assert payload["subscriptions"][0]["subscription_id"] == "opencode-go"
    assert payload["subscriptions"][0]["usage_provider_ids"] == ["opencode-go"]
    assert payload["subscriptions"][0]["quota_cost_basis"] == "source"
    assert [period["period"] for period in payload["subscriptions"][0]["periods"]] == [
        "5h",
        "weekly",
        "monthly",
    ]
    assert "billing" in payload["subscriptions"][0]
    assert "status" in payload["subscriptions"][0]["periods"][0]
    assert "reset_mode" in payload["subscriptions"][0]["periods"][0]
    assert "reset_at" in payload["subscriptions"][0]["periods"][0]


def test_cli_subscriptions_period_filter_accepts_5h(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    write_subscriptions_config(config_path)

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

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--period",
            "5h",
            "--json",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    periods = payload["subscriptions"][0]["periods"]
    assert [period["period"] for period in periods] == ["5h"]
    assert "billing" in payload["subscriptions"][0]


def test_cli_subscriptions_plan_id_can_cover_different_usage_provider(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "zai.db"
    config_path = tmp_path / "toktrail.toml"
    create_zai_source_db(source_db, source_cost=0.0)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"

[pricing]
[[pricing.virtual]]
provider = "zai"
model = "glm-4.5"
input_usd_per_1m = 4.0
output_usd_per_1m = 8.0

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "Europe/Berlin"
quota_cost_basis = "virtual"

[[subscriptions.windows]]
period = "5h"
limit_usd = 12
reset_mode = "first_use"
reset_at = "2023-11-01T00:00:00+00:00"
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
            "subscriptions",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Plan: Zai Coding Plan (zai-coding-plan)" in result.output
    assert "providers: zai" in result.output
    assert "active" in result.output


def test_cli_subscriptions_deduplicates_zero_cost_warnings_across_windows(
    tmp_path,
) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "zai.db"
    config_path = tmp_path / "toktrail.toml"
    create_zai_source_db(source_db, source_cost=0.0)
    config_path.write_text(
        f"""
config_version = 1

[imports]
harnesses = ["opencode"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = "{_toml_path_value(source_db)}"

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 12
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "weekly"
limit_usd = 60
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
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
            "subscriptions",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("zero cost for basis=source") == 1


def test_cli_subscriptions_prints_yearly_billing(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go-plan"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "UTC"
quota_cost_basis = "source"
fixed_cost_usd = 120
fixed_cost_period = "yearly"
fixed_cost_reset_at = "2026-01-01T00:00:00+00:00"

[[subscriptions.windows]]
period = "monthly"
limit_usd = 200
reset_mode = "fixed"
reset_at = "2026-05-01T00:00:00+00:00"
""".strip(),
        encoding="utf-8",
    )

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
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--utc",
            "--now-ms",
            "1777802400000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "yearly" in result.output
    assert "2026-01-01..2027-01-01" in result.output


def test_cli_subscriptions_disabled_window_is_not_printed(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
enabled = false

[[subscriptions.windows]]
period = "weekly"
limit_usd = 50
reset_mode = "fixed"
reset_at = "2023-11-01T00:00:00+00:00"
enabled = true
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
            "subscriptions",
            "--now-ms",
            "1700000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "weekly" in result.output
    assert "5h" not in result.output


def test_cli_subscriptions_first_use_waiting_human_output_is_clear(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "codex"
usage_providers = ["codex"]
display_name = "Codex"
timezone = "UTC"
quota_cost_basis = "source"

[[subscriptions.windows]]
period = "5h"
limit_usd = 20
reset_mode = "first_use"
reset_at = "2026-05-03T00:00:00+00:00"
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
            "subscriptions",
            "--now-ms",
            "1777802400000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "starts on first use" in result.output


def test_cli_subscriptions_first_use_human_output_hides_reset_anchor(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "Asia/Singapore"
quota_cost_basis = "virtual"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "first_use"
reset_at = "2026-05-01T00:00:00+08:00"
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
            "subscriptions",
            "--timezone",
            "Europe/Berlin",
            "--now-ms",
            "1778000000000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "first_use @" not in result.output
    assert "reset_at" not in result.output
    assert "resets (Europe/Berlin)" in result.output


def test_cli_subscriptions_display_timezone_converts_first_use_window(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text(
        """
config_version = 1

[pricing]
[[pricing.virtual]]
provider = "zai"
model = "glm-4.5"
input_usd_per_1m = 4.0
output_usd_per_1m = 8.0

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "Asia/Singapore"
quota_cost_basis = "virtual"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "first_use"
reset_at = "2026-05-01T00:00:00+08:00"
""".strip(),
        encoding="utf-8",
    )

    runner.invoke(app, ["--db", str(state_db), "init"])
    conn = connect(state_db)
    try:
        insert_usage_events(
            conn,
            None,
            [
                UsageEvent(
                    harness="opencode",
                    source_session_id="ses-zai",
                    source_row_id="row-zai",
                    source_message_id="msg-zai",
                    source_dedup_key="dedup-zai",
                    global_dedup_key="global-zai",
                    fingerprint_hash="fp-zai",
                    provider_id="zai",
                    model_id="glm-4.5",
                    thinking_level=None,
                    agent="build",
                    created_ms=_ms("2026-05-05T23:37:00+08:00"),
                    completed_ms=_ms("2026-05-05T23:37:01+08:00"),
                    tokens=TokenBreakdown(input=1_000_000, output=100_000),
                    source_cost_usd=Decimal("0"),
                    raw_json=None,
                )
            ],
        )
    finally:
        conn.close()

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--timezone",
            "Europe/Berlin",
            "--now-ms",
            str(_ms("2026-05-06T00:00:00+08:00")),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Display timezone: Europe/Berlin" in result.output
    assert "plan timezone: Asia/Singapore" in result.output
    assert "2026-05-05 17:37" in result.output
    assert "2026-05-05 22:37" in result.output
    assert "2026-05-05 23:37" not in result.output


def test_cli_subscriptions_rejects_timezone_and_utc(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "subscriptions",
            "--timezone",
            "Europe/Berlin",
            "--utc",
            "--no-refresh",
        ],
    )

    assert result.exit_code != 0
    assert "Use either --timezone or --utc" in result.output


def test_cli_subscriptions_no_configured_subscriptions_is_clear(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"

    runner.invoke(app, ["--db", str(state_db), "init"])
    result = runner.invoke(app, ["--db", str(state_db), "subscriptions"])

    assert result.exit_code == 0, result.output
    assert "No provider subscriptions configured." in result.output


def test_cli_subscriptions_unknown_provider_filter_is_clear(tmp_path) -> None:
    runner = CliRunner()
    state_db = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode-go.db"
    config_path = tmp_path / "toktrail.toml"
    create_opencode_go_source_db(source_db)
    write_subscriptions_config(config_path)

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

    result = runner.invoke(
        app,
        [
            "--db",
            str(state_db),
            "--config",
            str(config_path),
            "subscriptions",
            "--provider",
            "unknown-provider",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "No subscriptions matched provider unknown-provider." in result.output
