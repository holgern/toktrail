# mypy: ignore-errors
from __future__ import annotations

from textual.widgets import Static

from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.tui.formatting import leaf_path
from toktrail.tui.layout import TuiDisplay, resolve_tui_display
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.services import DashboardData


def _format_model_list(models: tuple[str, ...], *, limit: int = 2) -> str:
    if not models:
        return "-"
    visible = list(models[:limit])
    suffix = "" if len(models) <= limit else f" +{len(models) - limit}"
    return ", ".join(visible) + suffix


def _format_tokens_short(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _format_tool_bar(count: int, max_count: int, width: int) -> str:
    if max_count <= 0 or width <= 0:
        return ""
    filled = max(1, round(width * count / max_count))
    return chr(9608) * filled  # chr(9608) is the full block character


class DashboardPane(ExportablePaneMixin, Static):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.tui_display: TuiDisplay = resolve_tui_display("full")

    def set_display(self, display: TuiDisplay) -> None:
        self.tui_display = display

    def set_data(self, data: DashboardData) -> None:
        self.export_text = self._build_full_text(data)
        if self.tui_display.compact:
            self.update(self._build_compact_text(data))
            return
        self.update(self.export_text)

    def _build_full_text(self, data: DashboardData) -> str:
        active = data.active_area or "none"

        if data.view == "overview":
            return self._build_overview_text(data, active)
        if data.view == "tools":
            return self._build_tools_text(data, active)

        lines = [
            f"Dashboard: {data.title}",
            (
                "Shortcuts: o overview, t today, d daily,"
                " w weekly, g tools | 1-6 panes | r refresh"
            ),
            "",
            "Totals in shown buckets",
            f"  Tokens:       {_format_int(data.total_tokens)}",
            f"  Actual cost:  {_format_cost(data.actual_cost_usd)}",
            f"  Virtual cost: {_format_cost(data.virtual_cost_usd)}",
            f"  Savings:      {_format_cost(data.savings_usd)}",
            f"  Unpriced:     {_format_int(data.unpriced_count)}",
            "",
            f"Active area: {active}",
        ]
        if data.view != "today":
            lines.extend(
                [
                    "",
                    f"{data.title} usage",
                    (
                        "period        msgs      total      actual"
                        "    virtual   savings  models"
                    ),
                ]
            )
            for bucket in data.series_buckets:
                lines.append(
                    f"{bucket.label:<12}"
                    f"{_format_int(bucket.message_count):>6}"
                    f"{_format_int(bucket.tokens.total):>11}"
                    f"{_format_cost(bucket.costs.actual_cost_usd):>12}"
                    f"{_format_cost(bucket.costs.virtual_cost_usd):>10}"
                    f"{_format_cost(bucket.costs.savings_usd):>10}  "
                    f"{_format_model_list(bucket.models)}"
                )
            if not data.series_buckets:
                lines.append("  (no usage)")
        else:
            lines.extend(["", "Top providers"])
            if data.top_providers:
                for row in data.top_providers:
                    lines.append(
                        f"  {row.provider_id:<18}"
                        f" {_format_int(row.tokens.total):>10} tokens"
                        f" {_format_cost(row.costs.actual_cost_usd)}"
                    )
            else:
                lines.append("  (none)")
            lines.append("")
            lines.append("Top models")
            if data.top_models:
                for row in data.top_models:
                    model_label = f"{row.provider_id}/{row.model_id}"
                    lines.append(
                        f"  {model_label:<32} "
                        f"{_format_int(row.tokens.total):>10} tokens "
                        f"{_format_cost(row.costs.actual_cost_usd)}"
                    )
            else:
                lines.append("  (none)")
        lines.extend(
            [
                "",
                "Paths",
                f"  Config:        {data.config_path}",
                f"  Prices:        {data.prices_path}",
                f"  Subscriptions: {data.subscriptions_path}",
            ]
        )
        return "\n".join(lines)

    def _build_overview_text(self, data: DashboardData, active: str) -> str:
        lines = [
            "Overview",
            (
                "Shortcuts: o overview, t today, d daily,"
                " w weekly, g tools | 1-6 panes | r refresh"
            ),
            "",
            "OVERVIEW",
            f"Sessions          {_format_int(data.session_count):>10}",
            f"Messages          {_format_int(data.message_count):>10}",
            "",
            "COST & TOKENS",
            f"Total Cost        {_format_cost(data.actual_cost_usd):>12}",
            f"Input tokens      {_format_tokens_short(data.input_tokens):>12}",
            f"Output tokens     {_format_tokens_short(data.output_tokens):>12}",
            f"Cache read        {_format_tokens_short(data.cache_read_tokens):>12}",
            f"Cache write       {_format_tokens_short(data.cache_write_tokens):>12}",
        ]
        lines.extend(self._tool_usage_lines(data))
        lines.extend(["", f"Active area: {active}"])
        return "\n".join(lines)

    def _build_tools_text(self, data: DashboardData, active: str) -> str:
        lines = [
            "Tool usage",
            (
                "Shortcuts: o overview, t today, d daily,"
                " w weekly, g tools | 1-6 panes | r refresh"
            ),
        ]
        lines.extend(self._tool_usage_lines(data, width=30))
        lines.extend(["", f"Active area: {active}"])
        return "\n".join(lines)

    def _tool_usage_lines(self, data: DashboardData, width: int = 24) -> list[str]:
        lines: list[str] = []
        lines.append("")
        lines.append("TOOL USAGE")
        if not data.tool_rows:
            if data.tool_missing_session_count:
                lines.append(
                    f"  No persisted tool stats yet"
                    f" ({data.tool_missing_session_count} sessions missing)."
                )
            else:
                lines.append("  (none)")
            return lines
        max_count = max(row.get("count", 0) for row in data.tool_rows)
        for row in data.tool_rows[:10]:
            name = row.get("name", "?")
            count = row.get("count", 0)
            percent = row.get("percent", 0.0)
            bar = _format_tool_bar(count, max_count, width)
            lines.append(
                f"  {name:<12} {bar:<{width}}"
                f" {_format_int(count):>8} ({percent * 100:>4.1f}%)"
            )
        return lines

    def _build_compact_text(self, data: DashboardData) -> str:
        lines = [
            data.title,
            f"Tokens  {_format_int(data.total_tokens)}",
            f"Actual  {_format_cost(data.actual_cost_usd)}",
            f"Virtual {_format_cost(data.virtual_cost_usd)}",
            f"Save    {_format_cost(data.savings_usd)}",
            f"Area    {data.active_area or 'none'}",
            "",
        ]
        if data.view == "today":
            lines.append("Top providers")
            if data.top_providers:
                for row in data.top_providers:
                    lines.append(
                        f"{leaf_path(row.provider_id):<10} "
                        f"{_format_int(row.tokens.total):>8} "
                        f"{_format_cost(row.costs.actual_cost_usd)}"
                    )
            else:
                lines.append("(none)")
            return "\n".join(lines)

        lines.append("Buckets")
        if data.series_buckets:
            for bucket in data.series_buckets:
                lines.append(
                    f"{bucket.label:<10} "
                    f"{_format_int(bucket.tokens.total):>8} "
                    f"{_format_cost(bucket.costs.actual_cost_usd)}"
                )
        else:
            lines.append("(none)")
        return "\n".join(lines)
