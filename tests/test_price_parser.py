from __future__ import annotations

import pytest

from toktrail.price_parser import (
    parse_openai_pricing,
    parse_opencode_go_pricing,
    parse_price_document,
    parse_zai_pricing,
    render_prices_toml,
)


def test_parse_openai_pricing_filters_tier_and_warns_on_context_duplicate() -> None:
    text = '''
TextTokenPricingTables tier="standard" rows={[
  ["gpt-5.5 (<272K context length)", 5, 0.5, 30],
  ["GPT 5.5 (> 272K tokens)", 6, 0.6, 36],
  ["gpt-5.5-pro (<272K context length)", 30, "", 180],
]}
TextTokenPricingTables tier="batch" rows={[
  ["gpt-5.5 (<272K context length)", 2.5, 0.25, 15],
]}
'''
    parsed = parse_openai_pricing(text, tier="standard")

    assert parsed.provider == "openai"
    assert len(parsed.prices) == 2
    assert any(price.model == "gpt-5.5" for price in parsed.prices)
    assert any(price.model == "gpt-5.5-pro" for price in parsed.prices)
    assert parsed.warnings


def test_parse_openai_pricing_reads_markdown_table() -> None:
    text = """
| Model | Input | Cached Input | Output |
| --- | --- | --- | --- |
| GPT-5.5 | $5.00 | $0.50 | $30.00 |
""".strip()

    parsed = parse_openai_pricing(text, tier="standard")

    assert parsed.provider == "openai"
    assert parsed.prices[0].model == "gpt-5.5"
    assert parsed.prices[0].cached_input_usd_per_1m == 0.5


def test_parse_openai_pricing_warns_when_no_rows_parsed() -> None:
    parsed = parse_openai_pricing("not a pricing document", tier="standard")

    assert parsed.prices == ()
    assert any(
        "No OpenAI pricing rows were parsed" in warning
        for warning in parsed.warnings
    )


def test_parse_zai_pricing_reads_markdown_tables() -> None:
    text = '''
### Text Models

| Model | Input | Cached Input | Cached Input Storage | Output |
| --- | --- | --- | --- | --- |
| GLM-5.1 | $1.4 | $0.26 | Limited-time Free | $4.4 |

### Vision Models

| Model | Input | Output |
| --- | --- | --- |
| GLM-5V | Free | $0.2 |
'''
    parsed = parse_zai_pricing(text)

    assert len(parsed.prices) == 2
    text_row = next(price for price in parsed.prices if price.model == "glm-5.1")
    assert text_row.cached_input_usd_per_1m == 0.26
    assert text_row.category == "text"


def test_parse_opencode_go_pricing_maps_cached_columns() -> None:
    text = '''
Model        Input    Output    Cached Read    Cached Write
GLM 5.1      $1.40    $4.40     $0.26          -
Big Pickle   Free     Free      Free           -
'''
    parsed = parse_opencode_go_pricing(text, table="actual")

    assert parsed.table == "actual"
    glm = next(price for price in parsed.prices if price.model == "glm-5.1")
    assert glm.cached_input_usd_per_1m == 0.26
    assert glm.cache_write_usd_per_1m is None
    free = next(price for price in parsed.prices if price.model == "big-pickle")
    assert free.input_usd_per_1m == 0.0
    assert free.output_usd_per_1m == 0.0


def test_parse_price_document_dispatches_provider() -> None:
    text = 'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}'
    parsed = parse_price_document(text, provider="openai")

    assert parsed.provider == "openai"
    assert parsed.prices[0].model == "gpt-5.5"


def test_parse_price_document_lists_supported_providers_on_error() -> None:
    with pytest.raises(ValueError, match="Supported providers"):
        parse_price_document("x", provider="mistral")


def test_render_prices_toml_outputs_sorted_sections() -> None:
    parsed = parse_openai_pricing(
        'TextTokenPricingTables tier="standard" rows={[ ["gpt-5.5", 5, 0.5, 30] ]}',
        tier="standard",
    )
    rendered = render_prices_toml(virtual_prices=parsed.prices)

    assert "[[pricing.virtual]]" in rendered
    assert 'provider = "openai"' in rendered
    assert "input_usd_per_1m = 5.0" in rendered
