# mypy: ignore-errors
from __future__ import annotations

from textual.widgets import Static

from toktrail.api.models import PriceRow, UnconfiguredModelRow
from toktrail.tui.services import PricesData


class PricesPane(Static):
    selected_unconfigured: UnconfiguredModelRow | None = None

    def set_data(self, data: PricesData) -> None:
        self.selected_unconfigured = data.unconfigured[0] if data.unconfigured else None
        rows = ["Prices", f"Configured rows: {len(data.rows)}", "Unconfigured:"]
        if not data.unconfigured:
            rows.append("(none)")
        else:
            for row in data.unconfigured:
                rows.append(f"{row.provider_id}/{row.model_id}")
        self.update("\n".join(rows))

    def seed_manual_price_row(self) -> PriceRow | None:
        if self.selected_unconfigured is None:
            return None
        selected = self.selected_unconfigured
        return PriceRow(
            table="virtual",
            provider=selected.provider_id,
            model=selected.model_id,
            input_usd_per_1m=0.0,
            output_usd_per_1m=0.0,
        )
