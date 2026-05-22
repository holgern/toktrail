# mypy: ignore-errors
from __future__ import annotations

from textual.widgets import Static

from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.services import DashboardData


def _format_model_list(models: tuple[str, ...], *, limit: int = 2) -> str:
    if not models:
        return "-"
    visible = list(models[:limit])
    suffix = "" if len(models) <= limit else f" +{len(models) - limit}"
    return ", ".join(visible) + suffix


class DashboardPane(ExportablePaneMixin, Static):
    def set_data(self, data: DashboardData) -> None:
        active = data.active_area or "none"
        lines = [
            f"Dashboard: {data.title}",
            "Shortcuts: t today, d daily, w weekly | 1-5 panes | r refresh",
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
        self.export_text = "\n".join(lines)
        self.update(self.export_text)
