from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.helpers import VALID_ASSISTANT, create_opencode_db, insert_message
from toktrail import config as config_module
from toktrail.api.imports import import_usage
from toktrail.api.models import PriceRow
from toktrail.api.prices import (
    delete_manual_price,
    list_prices,
    list_unconfigured_models,
    upsert_manual_price,
)
from toktrail.api.sessions import init_state
from toktrail.errors import ConfigurationError, InvalidAPIUsageError


def _future_assistant() -> dict[str, object]:
    assistant = deepcopy(VALID_ASSISTANT)
    created_ms = float(int(datetime.now(timezone.utc).timestamp() * 1000) + 60_000)
    time_block = assistant["time"]
    assert isinstance(time_block, dict)
    time_block["created"] = created_ms
    return assistant


def _write_prices(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def test_list_prices_loads_manual_and_provider_prices(tmp_path: Path) -> None:
    config_path = tmp_path / "toktrail.toml"
    prices_path = config_path.with_name("prices.toml")
    prices_dir = config_path.with_name("prices")
    prices_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    _write_prices(
        prices_path,
        """
        config_version = 1
        [[pricing.virtual]]
        provider = "openai"
        model = "gpt-5-mini"
        input_usd_per_1m = 1
        output_usd_per_1m = 2
        """,
    )
    _write_prices(
        prices_dir / "anthropic.toml",
        """
        config_version = 1
        [[pricing.virtual]]
        provider = "anthropic"
        model = "claude-sonnet-4"
        input_usd_per_1m = 3
        output_usd_per_1m = 15
        """,
    )

    effective = list_prices(
        config_path=config_path,
        prices_path=prices_path,
        prices_dir=prices_dir,
        include_provider_prices=True,
    )
    manual_only = list_prices(
        config_path=config_path,
        prices_path=prices_path,
        prices_dir=prices_dir,
        include_provider_prices=False,
    )

    assert any(row.provider == "anthropic" for row in effective)
    assert all(row.source_kind == "effective" for row in effective)
    assert len(manual_only) == 1
    assert manual_only[0].provider == "openai"
    assert manual_only[0].source_kind == "manual"


def test_upsert_manual_price_creates_and_replaces_row(tmp_path: Path) -> None:
    prices_path = tmp_path / "prices.toml"
    first = PriceRow(
        table="virtual",
        provider="openai",
        model="gpt-5.3-codex",
        input_usd_per_1m=1.25,
        output_usd_per_1m=10.0,
    )
    second = PriceRow(
        table="virtual",
        provider="openai",
        model="gpt-5.3-codex",
        input_usd_per_1m=1.5,
        output_usd_per_1m=12.0,
    )

    written = upsert_manual_price(first, prices_path=prices_path)
    upsert_manual_price(second, prices_path=prices_path)
    loaded = config_module.load_pricing_config(prices_path)

    assert written == prices_path
    assert len(loaded.virtual_prices) == 1
    assert loaded.virtual_prices[0].input_usd_per_1m == 1.5
    assert prices_path.with_name("prices.toml.bak").exists()


def test_upsert_manual_price_preserves_unrelated_rows(tmp_path: Path) -> None:
    prices_path = tmp_path / "prices.toml"
    _write_prices(
        prices_path,
        """
        config_version = 1
        [[pricing.virtual]]
        provider = "anthropic"
        model = "claude-sonnet-4"
        input_usd_per_1m = 3
        output_usd_per_1m = 15
        """,
    )
    upsert_manual_price(
        PriceRow(
            table="virtual",
            provider="openai",
            model="gpt-5.3-codex",
            input_usd_per_1m=1.25,
            output_usd_per_1m=10.0,
        ),
        prices_path=prices_path,
    )
    loaded = config_module.load_pricing_config(prices_path)
    models = {(row.provider, row.model) for row in loaded.virtual_prices}
    assert ("anthropic", "claude-sonnet-4") in models
    assert ("openai", "gpt-5.3-codex") in models


def test_delete_manual_price_removes_only_exact_variant(tmp_path: Path) -> None:
    prices_path = tmp_path / "prices.toml"
    _write_prices(
        prices_path,
        """
        config_version = 1
        [[pricing.virtual]]
        provider = "openai"
        model = "gpt-5.3-codex"
        context_min_tokens = 0
        context_max_tokens = 32000
        input_usd_per_1m = 1
        output_usd_per_1m = 2

        [[pricing.virtual]]
        provider = "openai"
        model = "gpt-5.3-codex"
        context_min_tokens = 32001
        context_max_tokens = 64000
        input_usd_per_1m = 2
        output_usd_per_1m = 4
        """,
    )

    delete_manual_price(
        table="virtual",
        provider="openai",
        model="gpt-5.3-codex",
        context_min_tokens=0,
        context_max_tokens=32000,
        prices_path=prices_path,
    )

    loaded = config_module.load_pricing_config(prices_path)
    assert len(loaded.virtual_prices) == 1
    assert loaded.virtual_prices[0].context_min_tokens == 32001


def test_upsert_rejects_invalid_and_overlapping_rows(tmp_path: Path) -> None:
    prices_path = tmp_path / "prices.toml"
    with pytest.raises(InvalidAPIUsageError, match="non-negative"):
        upsert_manual_price(
            PriceRow(
                table="virtual",
                provider="openai",
                model="gpt-5.3-codex",
                input_usd_per_1m=-1.0,
                output_usd_per_1m=10.0,
            ),
            prices_path=prices_path,
        )

    upsert_manual_price(
        PriceRow(
            table="virtual",
            provider="openai",
            model="gpt-5.3-codex",
            context_min_tokens=0,
            context_max_tokens=32000,
            input_usd_per_1m=1.0,
            output_usd_per_1m=10.0,
        ),
        prices_path=prices_path,
    )
    with pytest.raises(ConfigurationError, match="invalid"):
        upsert_manual_price(
            PriceRow(
                table="virtual",
                provider="openai",
                model="gpt-5.3-codex",
                context_min_tokens=20000,
                context_max_tokens=64000,
                input_usd_per_1m=1.0,
                output_usd_per_1m=10.0,
            ),
            prices_path=prices_path,
        )


def test_upsert_failure_does_not_modify_existing_file(tmp_path: Path) -> None:
    prices_path = tmp_path / "prices.toml"
    baseline = "config_version = 1\n"
    prices_path.write_text(baseline, encoding="utf-8")
    with pytest.raises(InvalidAPIUsageError):
        upsert_manual_price(
            PriceRow(
                table="virtual",
                provider="openai",
                model="gpt-5.3-codex",
                input_usd_per_1m=-0.1,
                output_usd_per_1m=10.0,
            ),
            prices_path=prices_path,
        )
    assert prices_path.read_text(encoding="utf-8") == baseline


def test_list_unconfigured_models_supports_price_seed(tmp_path: Path) -> None:
    db_path = tmp_path / "toktrail.db"
    source_db = tmp_path / "opencode.db"
    config_path = tmp_path / "toktrail.toml"
    config_path.write_text("config_version = 1\n", encoding="utf-8")
    init_state(db_path)

    conn = create_opencode_db(source_db)
    assistant = _future_assistant()
    assistant["providerID"] = "openai-codex"
    assistant["modelID"] = "gpt-5.3-codex"
    insert_message(conn, row_id="row-1", session_id="ses-1", data=assistant)
    conn.commit()
    conn.close()
    import_usage(db_path, "opencode", source_path=source_db, use_active_session=False)

    rows = list_unconfigured_models(db_path, period="today", config_path=config_path)
    assert any(row.provider_id == "openai-codex" for row in rows)
