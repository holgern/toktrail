# mypy: ignore-errors
from __future__ import annotations

from textual.widgets import Static

from toktrail.tui.services import ConfigData


class ConfigPane(Static):
    def set_data(self, data: ConfigData) -> None:
        summary = data.summary
        self.update(
            "\n".join(
                [
                    "Config",
                    f"config: {summary['config_path']}",
                    f"prices: {summary['manual_prices_path']}",
                    f"subscriptions: {summary['subscriptions_path']}",
                    f"actual prices: {summary['actual_price_count']}",
                    f"virtual prices: {summary['virtual_price_count']}",
                ]
            )
        )
