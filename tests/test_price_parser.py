from __future__ import annotations

import pytest

from toktrail.config import Price
from toktrail.price_parser import (
    ParsedPriceDocument,
    merge_prices_document,
    parse_github_copilot_pricing,
    parse_openai_pricing,
    parse_opencode_go_pricing,
    parse_price_document,
    parse_zai_pricing,
    render_prices_toml,
)


def test_parse_openai_pricing_filters_tier_and_deduplicates_context_variants() -> None:
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
    assert parsed.warnings == ()


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


def test_parse_openai_pricing_markdown_prefers_short_context_variant() -> None:
    text = """
| Model | Input | Cached Input | Output |
| --- | --- | --- | --- |
| GPT-5.5 (> 272K tokens) | $10.00 | $1.00 | $45.00 |
| GPT-5.5 (≤ 272K tokens) | $5.00 | $0.50 | $30.00 |
""".strip()

    parsed = parse_openai_pricing(text, tier="standard")

    assert len(parsed.prices) == 1
    assert parsed.prices[0].model == "gpt-5.5"
    assert parsed.prices[0].input_usd_per_1m == 5.0
    assert parsed.prices[0].cached_input_usd_per_1m == 0.5
    assert parsed.prices[0].output_usd_per_1m == 30.0


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


def test_parse_zai_pricing_accepts_backslash_none_markers() -> None:
    text = '''
### Vision Models

| Model | Input | Cached Input | Cached Input Storage | Output |
| --- | --- | --- | --- | --- |
| GLM-OCR | $0.03 | \\\\ | \\\\ | $0.03 |
'''
    parsed = parse_zai_pricing(text)

    assert len(parsed.prices) == 1
    row = parsed.prices[0]
    assert row.model == "glm-ocr"
    assert row.cached_input_usd_per_1m is None


def test_parse_github_copilot_pricing_reads_sections_and_cache_write() -> None:
    text = """
Pricing tables
All prices are per 1 million tokens.

OpenAI
Model\tRelease status\tCategory\tInput\tCached input\tOutput
GPT-5.2\tGA\tVersatile\t$1.75\t$0.175\t$14.00
Anthropic
Model\tRelease status\tCategory\tInput\tCached input\tCache write\tOutput
Claude Sonnet 4.5\tGA\tVersatile\t$3.00\t$0.30\t$3.75\t$15.00
""".strip()
    parsed = parse_github_copilot_pricing(text)

    assert parsed.provider == "github-copilot"
    assert len(parsed.prices) == 2
    gpt = next(price for price in parsed.prices if price.model == "gpt-5.2")
    assert gpt.release_status == "GA"
    assert gpt.category == "Versatile"
    assert gpt.cached_input_usd_per_1m == 0.175
    sonnet = next(
        price for price in parsed.prices if price.model == "claude-sonnet-4.5"
    )
    assert sonnet.cache_write_usd_per_1m == 3.75


def test_parse_opencode_go_pricing_deduplicates_context_variants() -> None:
    text = """
Model	Input	Output	Cached Read	Cached Write
Claude Sonnet 4.5 (> 200K tokens)	$6.00	$22.50	$0.60	$7.50
Claude Sonnet 4.5 (≤ 200K tokens)	$3.00	$15.00	$0.30	$3.75
""".strip()

    parsed = parse_opencode_go_pricing(text, table="actual")

    assert len(parsed.prices) == 1
    sonnet = parsed.prices[0]
    assert sonnet.model == "claude-sonnet-4.5"
    assert sonnet.input_usd_per_1m == 3.0
    assert sonnet.output_usd_per_1m == 15.0
    assert sonnet.cached_input_usd_per_1m == 0.3
    assert sonnet.cache_write_usd_per_1m == 3.75


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


def test_parse_price_document_dispatches_github_copilot_provider() -> None:
    text = """
OpenAI
Model\tRelease status\tCategory\tInput\tCached input\tOutput
GPT-5.2\tGA\tVersatile\t$1.75\t$0.175\t$14.00
""".strip()
    parsed = parse_price_document(text, provider="github-copilot")

    assert parsed.provider == "github-copilot"
    assert parsed.prices[0].model == "gpt-5.2"


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


def test_merge_prices_document_replace_provider_recovers_legacy_duplicates() -> None:
    existing_text = """
config_version = 1

[[pricing.virtual]]
provider = "opencode-go"
model = "claude-sonnet-4"
aliases = ["Claude Sonnet 4 (≤ 200K tokens)"]
input_usd_per_1m = 3
cached_input_usd_per_1m = 0.3
output_usd_per_1m = 15

[[pricing.virtual]]
provider = "opencode-go"
model = "claude-sonnet-4"
aliases = ["Claude Sonnet 4 (> 200K tokens)"]
input_usd_per_1m = 6
cached_input_usd_per_1m = 0.6
output_usd_per_1m = 22.5

[[pricing.virtual]]
provider = "anthropic"
model = "claude-haiku-4.5"
input_usd_per_1m = 1
output_usd_per_1m = 5
""".strip()
    parsed = ParsedPriceDocument(
        provider="opencode-go",
        table="virtual",
        prices=(
            Price(
                provider="opencode-go",
                model="claude-sonnet-4",
                aliases=("Claude Sonnet 4",),
                input_usd_per_1m=3.0,
                cached_input_usd_per_1m=0.3,
                output_usd_per_1m=15.0,
            ),
        ),
        warnings=(),
    )

    merged = merge_prices_document(
        existing_text=existing_text,
        parsed=parsed,
        replace_provider=True,
    )

    assert merged.count('model = "claude-sonnet-4"') == 1
    assert 'provider = "anthropic"' in merged
