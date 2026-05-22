# mypy: ignore-errors
from __future__ import annotations

from textual.widgets import Static

from toktrail.tui.services import DashboardData


class DashboardPane(Static):
    def set_data(self, data: DashboardData) -> None:
        active = data.active_area or "none"
        self.update(
            "\n".join(
                [
                    "Dashboard",
                    f"Today tokens: {data.total_tokens}",
                    f"Active area: {active}",
                    f"Unpriced models: {data.unpriced_count}",
                    f"Config: {data.config_path}",
                    f"Prices: {data.prices_path}",
                    f"Subscriptions: {data.subscriptions_path}",
                ]
            )
        )
