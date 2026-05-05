from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from toktrail.config import Price, normalize_identity, parse_pricing_config

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


@dataclass(frozen=True)
class ParsedPrice:
    provider: str
    model: str
    aliases: tuple[str, ...]
    input_usd_per_1m: float
    output_usd_per_1m: float
    cached_input_usd_per_1m: float | None = None
    cache_write_usd_per_1m: float | None = None
    cached_output_usd_per_1m: float | None = None
    reasoning_usd_per_1m: float | None = None
    category: str | None = None
    release_status: str | None = None
    source_label: str | None = None


@dataclass(frozen=True)
class ParsedPriceDocument:
    provider: str
    table: Literal["virtual", "actual"]
    prices: tuple[Price, ...]
    warnings: tuple[str, ...]


ParserFn = Callable[..., ParsedPriceDocument]


def parse_price_value(
    value: object,
    *,
    none_markers: set[str] | None = None,
) -> float | None:
    markers = none_markers or {"", "-", "\\", "null", "none", "n/a"}
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Expected numeric price value, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in markers:
        return None
    if lowered == "free":
        return 0.0
    if "limited-time free" in lowered:
        return None

    cleaned = text.replace("$", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(f"Could not parse price value {value!r}") from exc


def parse_price_document(
    text: str,
    *,
    provider: str,
    table: Literal["virtual", "actual"] = "virtual",
    tier: str | None = None,
) -> ParsedPriceDocument:
    normalized_provider = normalize_identity(provider)
    parser = _PRICE_PARSERS.get(normalized_provider)
    if parser is None:
        supported = ", ".join(sorted({"openai", "opencode-go", "zai"}))
        raise ValueError(
            "Unsupported pricing parser provider: "
            f"{provider}. Supported providers: {supported}."
        )
    return parser(text=text, table=table, tier=tier or "standard")


def parse_openai_pricing(
    text: str,
    *,
    tier: str = "standard",
    table: Literal["virtual", "actual"] = "virtual",
) -> ParsedPriceDocument:
    warnings: list[str] = []
    prices: list[Price] = []
    seen_keys: dict[tuple[str, str], Price] = {}

    for panel_tier, rows in _extract_openai_tier_rows(text):
        if panel_tier.lower() != tier.lower():
            continue
        for raw_row in rows:
            if not isinstance(raw_row, (list, tuple)) or len(raw_row) < 4:
                continue
            raw_model = str(raw_row[0]).strip()
            canonical = normalize_identity(_strip_parenthetical(raw_model) or raw_model)
            aliases = _build_aliases(raw_model)
            input_price = parse_price_value(raw_row[1])
            cached_input_price = parse_price_value(raw_row[2])
            output_price = parse_price_value(raw_row[3])
            if input_price is None or output_price is None:
                continue
            parsed = Price(
                provider="openai",
                model=canonical,
                aliases=aliases,
                input_usd_per_1m=input_price,
                cached_input_usd_per_1m=cached_input_price,
                output_usd_per_1m=output_price,
                release_status=tier,
            )
            key = (parsed.provider, parsed.model)
            existing = seen_keys.get(key)
            if existing is not None and existing != parsed:
                warnings.append(
                    "OpenAI row "
                    f"{raw_model!r} normalizes to {parsed.provider}/{parsed.model} "
                    "with different prices; skipped duplicate context-tier row."
                )
                continue
            seen_keys[key] = parsed
            prices.append(parsed)

    if not prices:
        markdown_prices, markdown_warnings = _parse_openai_markdown_prices(
            text,
            tier=tier,
        )
        prices.extend(markdown_prices)
        warnings.extend(markdown_warnings)

    if not prices:
        warnings.append("No OpenAI pricing rows were parsed from the provided input.")

    return ParsedPriceDocument(
        provider="openai",
        table=table,
        prices=tuple(prices),
        warnings=tuple(warnings),
    )


def parse_zai_pricing(
    text: str,
    *,
    table: Literal["virtual", "actual"] = "virtual",
) -> ParsedPriceDocument:
    prices: list[Price] = []
    warnings: list[str] = []
    current_category: str | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("###"):
            heading = line.lstrip("#").strip().lower()
            if "text" in heading:
                current_category = "text"
            elif "vision" in heading:
                current_category = "vision"
            else:
                current_category = None
            index += 1
            continue

        if line.startswith("|"):
            table_lines = [line]
            index += 1
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            rows = _parse_markdown_table(table_lines)
            if not rows:
                continue
            header = {name.strip().lower(): i for i, name in enumerate(rows[0])}
            if "model" not in header or "input" not in header or "output" not in header:
                continue
            for raw_row in rows[1:]:
                model_text = raw_row[header["model"]].strip()
                if not model_text:
                    continue
                input_price = parse_price_value(raw_row[header["input"]])
                output_price = parse_price_value(raw_row[header["output"]])
                if input_price is None or output_price is None:
                    warnings.append(
                        "Skipped Z.AI row without required prices: "
                        f"{model_text}"
                    )
                    continue
                cached_input = parse_price_value(
                    raw_row[header["cached input"]]
                    if (
                        "cached input" in header
                        and header["cached input"] < len(raw_row)
                    )
                    else None
                )
                canonical = normalize_identity(
                    _strip_parenthetical(model_text) or model_text
                )
                prices.append(
                    Price(
                        provider="zai",
                        model=canonical,
                        aliases=_build_aliases(model_text),
                        input_usd_per_1m=input_price,
                        cached_input_usd_per_1m=cached_input,
                        output_usd_per_1m=output_price,
                        category=current_category,
                    )
                )
            continue
        index += 1

    return ParsedPriceDocument(
        provider="zai",
        table=table,
        prices=tuple(prices),
        warnings=tuple(warnings),
    )


def parse_opencode_go_pricing(
    text: str,
    *,
    table: Literal["virtual", "actual"] = "actual",
) -> ParsedPriceDocument:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    header_idx = -1
    for i, line in enumerate(lines):
        lower = line.lower()
        if "model" in lower and "input" in lower and "output" in lower:
            header_idx = i
            break
    if header_idx < 0:
        return ParsedPriceDocument(
            provider="opencode-go",
            table=table,
            prices=(),
            warnings=("No opencode-go pricing table header found.",),
        )

    raw_header = lines[header_idx]
    if "|" in raw_header:
        table_rows = [
            line
            for line in lines[header_idx:]
            if line.strip().startswith("|")
        ]
        parsed = _parse_markdown_table(table_rows)
        if not parsed:
            return ParsedPriceDocument(
                provider="opencode-go",
                table=table,
                prices=(),
                warnings=("No parseable rows in opencode-go markdown table.",),
            )
        header = [cell.strip().lower() for cell in parsed[0]]
        data_rows = parsed[1:]
        split_rows = [header, *data_rows]
    else:
        split_rows = [
            re.split(r"\t+|\s{2,}", line.strip())
            for line in lines[header_idx:]
        ]

    header = [col.strip().lower() for col in split_rows[0]]
    lookup = {name: idx for idx, name in enumerate(header)}
    model_idx = lookup.get("model")
    input_idx = lookup.get("input")
    output_idx = lookup.get("output")
    cached_read_idx = lookup.get("cached read")
    cached_write_idx = lookup.get("cached write")
    if model_idx is None or input_idx is None or output_idx is None:
        return ParsedPriceDocument(
            provider="opencode-go",
            table=table,
            prices=(),
            warnings=("Missing required opencode-go columns.",),
        )

    prices: list[Price] = []
    for row in split_rows[1:]:
        if max(model_idx, input_idx, output_idx) >= len(row):
            continue
        model_text = row[model_idx].strip()
        if not model_text:
            continue
        input_price = parse_price_value(row[input_idx])
        output_price = parse_price_value(row[output_idx])
        if input_price is None or output_price is None:
            continue
        cached_read = (
            parse_price_value(row[cached_read_idx])
            if cached_read_idx is not None and cached_read_idx < len(row)
            else None
        )
        cached_write = (
            parse_price_value(row[cached_write_idx])
            if cached_write_idx is not None and cached_write_idx < len(row)
            else None
        )
        canonical = normalize_identity(_strip_parenthetical(model_text) or model_text)
        prices.append(
            Price(
                provider="opencode-go",
                model=canonical,
                aliases=_build_aliases(model_text),
                input_usd_per_1m=input_price,
                cached_input_usd_per_1m=cached_read,
                cache_write_usd_per_1m=cached_write,
                output_usd_per_1m=output_price,
            )
        )

    return ParsedPriceDocument(
        provider="opencode-go",
        table=table,
        prices=tuple(prices),
        warnings=(),
    )


def render_prices_toml(
    *,
    virtual_prices: Sequence[Price] = (),
    actual_prices: Sequence[Price] = (),
    metadata: Mapping[str, str] | None = None,
) -> str:
    lines = ["config_version = 1", ""]
    if metadata:
        lines.append("[metadata]")
        for key in sorted(metadata):
            lines.append(f"{key} = {_toml_quote(str(metadata[key]))}")
        lines.append("")

    for table_name, prices in (
        ("virtual", virtual_prices),
        ("actual", actual_prices),
    ):
        for price in sorted(prices, key=lambda item: (item.provider, item.model)):
            lines.append(f"[[pricing.{table_name}]]")
            lines.append(f"provider = {_toml_quote(price.provider)}")
            lines.append(f"model = {_toml_quote(price.model)}")
            if price.aliases:
                lines.append(f"aliases = {_render_string_array(price.aliases)}")
            lines.append(f"input_usd_per_1m = {_toml_float(price.input_usd_per_1m)}")
            if price.cached_input_usd_per_1m is not None:
                lines.append(
                    "cached_input_usd_per_1m = "
                    f"{_toml_float(price.cached_input_usd_per_1m)}"
                )
            if price.cache_write_usd_per_1m is not None:
                lines.append(
                    "cache_write_usd_per_1m = "
                    f"{_toml_float(price.cache_write_usd_per_1m)}"
                )
            if price.cached_output_usd_per_1m is not None:
                lines.append(
                    "cached_output_usd_per_1m = "
                    f"{_toml_float(price.cached_output_usd_per_1m)}"
                )
            lines.append(f"output_usd_per_1m = {_toml_float(price.output_usd_per_1m)}")
            if price.reasoning_usd_per_1m is not None:
                lines.append(
                    "reasoning_usd_per_1m = "
                    f"{_toml_float(price.reasoning_usd_per_1m)}"
                )
            if price.category is not None:
                lines.append(f"category = {_toml_quote(price.category)}")
            if price.release_status is not None:
                lines.append(f"release_status = {_toml_quote(price.release_status)}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def merge_prices_document(
    *,
    existing_text: str | None,
    parsed: ParsedPriceDocument,
    replace_provider: bool,
    metadata: Mapping[str, str] | None = None,
) -> str:
    if existing_text is None:
        existing = parse_pricing_config({})
    else:
        loaded = tomllib.loads(existing_text)
        existing = parse_pricing_config(loaded)

    virtual = list(existing.virtual_prices)
    actual = list(existing.actual_prices)
    target = virtual if parsed.table == "virtual" else actual

    if replace_provider:
        target[:] = [
            price
            for price in target
            if normalize_identity(price.provider) != normalize_identity(parsed.provider)
        ]

    replacements = {
        (normalize_identity(price.provider), normalize_identity(price.model)): price
        for price in parsed.prices
    }
    kept: list[Price] = []
    for price in target:
        key = (normalize_identity(price.provider), normalize_identity(price.model))
        replacement = replacements.pop(key, None)
        kept.append(replacement if replacement is not None else price)
    kept.extend(replacements.values())

    if parsed.table == "virtual":
        virtual = kept
    else:
        actual = kept

    return render_prices_toml(
        virtual_prices=tuple(virtual),
        actual_prices=tuple(actual),
        metadata=metadata,
    )


def _parse_openai_markdown_prices(
    text: str,
    *,
    tier: str,
) -> tuple[list[Price], list[str]]:
    warnings: list[str] = []
    prices: list[Price] = []
    seen_keys: dict[tuple[str, str], Price] = {}
    cached_header_seen = False
    model_headers_seen = False
    input_headers_seen = False
    output_headers_seen = False

    for table_rows in _extract_markdown_tables(text):
        if not table_rows:
            continue
        lookup = _openai_markdown_header_lookup(table_rows[0])
        model_idx = lookup.get("model")
        input_idx = lookup.get("input")
        output_idx = lookup.get("output")
        cached_idx = lookup.get("cached_input")
        reasoning_idx = lookup.get("reasoning")
        model_headers_seen = model_headers_seen or model_idx is not None
        input_headers_seen = input_headers_seen or input_idx is not None
        output_headers_seen = output_headers_seen or output_idx is not None
        cached_header_seen = cached_header_seen or cached_idx is not None
        if model_idx is None or input_idx is None or output_idx is None:
            continue
        for raw_row in table_rows[1:]:
            if max(model_idx, input_idx, output_idx) >= len(raw_row):
                continue
            model_text = raw_row[model_idx].strip()
            if not model_text:
                continue
            input_price = parse_price_value(raw_row[input_idx])
            output_price = parse_price_value(raw_row[output_idx])
            if input_price is None or output_price is None:
                warnings.append(
                    "Skipped OpenAI markdown row without required prices: "
                    f"{model_text}"
                )
                continue
            cached_input = (
                parse_price_value(raw_row[cached_idx])
                if cached_idx is not None and cached_idx < len(raw_row)
                else None
            )
            reasoning = (
                parse_price_value(raw_row[reasoning_idx])
                if reasoning_idx is not None and reasoning_idx < len(raw_row)
                else None
            )
            canonical = normalize_identity(
                _strip_parenthetical(model_text) or model_text
            )
            parsed = Price(
                provider="openai",
                model=canonical,
                aliases=_build_aliases(model_text),
                input_usd_per_1m=input_price,
                cached_input_usd_per_1m=cached_input,
                output_usd_per_1m=output_price,
                reasoning_usd_per_1m=reasoning,
                release_status=tier,
            )
            key = (parsed.provider, parsed.model)
            existing = seen_keys.get(key)
            if existing is not None and existing != parsed:
                warnings.append(
                    "OpenAI markdown row "
                    f"{model_text!r} normalizes to {parsed.provider}/{parsed.model} "
                    "with different prices; skipped duplicate row."
                )
                continue
            seen_keys[key] = parsed
            prices.append(parsed)

    if not model_headers_seen or not input_headers_seen or not output_headers_seen:
        warnings.append("OpenAI markdown table is missing required columns.")
    if (
        model_headers_seen
        and input_headers_seen
        and output_headers_seen
        and not cached_header_seen
    ):
        warnings.append(
            "OpenAI markdown table is missing cached input/read column; "
            "cached prices were omitted."
        )
    return prices, warnings


def _extract_openai_tier_rows(text: str) -> list[tuple[str, list[object]]]:
    rows_by_tier: list[tuple[str, list[object]]] = []
    cursor = 0
    marker = "TextTokenPricingTables"
    while True:
        start = text.find(marker, cursor)
        if start < 0:
            break
        window_end = text.find(marker, start + len(marker))
        if window_end < 0:
            window_end = len(text)
        block = text[start:window_end]

        tier_match = re.search(r'tier\s*=\s*"([^"]+)"', block)
        rows_marker = "rows={["
        rows_start = block.find(rows_marker)
        if tier_match and rows_start >= 0:
            bracket_start = rows_start + len("rows={")
            rows_literal = _scan_balanced_brackets(block, bracket_start)
            parsed_rows = _literal_eval_js_array(rows_literal)
            rows_by_tier.append((tier_match.group(1), parsed_rows))
        cursor = start + len(marker)
    return rows_by_tier


def _extract_markdown_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line.startswith("|"):
            index += 1
            continue
        table_lines = [line]
        index += 1
        while index < len(lines) and lines[index].strip().startswith("|"):
            table_lines.append(lines[index].strip())
            index += 1
        parsed = _parse_markdown_table(table_lines)
        if parsed:
            tables.append(parsed)
    return tables


def _openai_markdown_header_lookup(header: Sequence[str]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for index, column in enumerate(header):
        normalized = normalize_identity(column)
        if normalized in {"model", "models"}:
            lookup.setdefault("model", index)
        elif normalized in {"input", "input-price"}:
            lookup.setdefault("input", index)
        elif normalized in {"cached-input", "cached-read", "cache-read"}:
            lookup.setdefault("cached_input", index)
        elif normalized in {"output", "output-price"}:
            lookup.setdefault("output", index)
        elif normalized in {"reasoning", "reasoning-output"}:
            lookup.setdefault("reasoning", index)
    return lookup


def _scan_balanced_brackets(text: str, start_index: int) -> str:
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    begin = -1
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            quote = char
            continue
        if char == "[":
            if begin < 0:
                begin = index
            depth += 1
            continue
        if char == "]":
            depth -= 1
            if depth == 0 and begin >= 0:
                return text[begin : index + 1]
    raise ValueError("Could not find balanced rows array in OpenAI pricing text.")


def _literal_eval_js_array(raw: str) -> list[object]:
    normalized = re.sub(r"\bnull\b", "None", raw)
    parsed = ast.literal_eval(normalized)
    if not isinstance(parsed, list):
        raise ValueError("Expected a list of rows.")
    return parsed


def _parse_markdown_table(lines: Sequence[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _build_aliases(raw_model: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for candidate in (
        raw_model.strip(),
        _strip_parenthetical(raw_model).strip(),
        normalize_identity(raw_model),
        normalize_identity(_strip_parenthetical(raw_model) or raw_model),
    ):
        if not candidate:
            continue
        if candidate not in aliases:
            aliases.append(candidate)
    return tuple(aliases)


def _strip_parenthetical(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", value).strip()


def _toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_float(value: float) -> str:
    text = format(Decimal(str(value)).normalize(), "f")
    if "." not in text:
        return f"{text}.0"
    return text


def _render_string_array(values: Sequence[str]) -> str:
    quoted = ", ".join(_toml_quote(value) for value in values)
    return f"[{quoted}]"


def _parse_openai_document(
    *,
    text: str,
    table: Literal["virtual", "actual"],
    tier: str,
) -> ParsedPriceDocument:
    return parse_openai_pricing(text, tier=tier, table=table)


def _parse_zai_document(
    *,
    text: str,
    table: Literal["virtual", "actual"],
    tier: str,
) -> ParsedPriceDocument:
    del tier
    return parse_zai_pricing(text, table=table)


def _parse_opencode_go_document(
    *,
    text: str,
    table: Literal["virtual", "actual"],
    tier: str,
) -> ParsedPriceDocument:
    del tier
    return parse_opencode_go_pricing(text, table=table)


_PRICE_PARSERS: dict[str, ParserFn] = {
    "openai": _parse_openai_document,
    "zai": _parse_zai_document,
    "opencode-go": _parse_opencode_go_document,
    "opencodego": _parse_opencode_go_document,
}


__all__ = [
    "ParsedPrice",
    "ParsedPriceDocument",
    "merge_prices_document",
    "parse_openai_pricing",
    "parse_opencode_go_pricing",
    "parse_price_document",
    "parse_price_value",
    "parse_zai_pricing",
    "render_prices_toml",
]
