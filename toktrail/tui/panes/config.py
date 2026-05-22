# mypy: ignore-errors
from __future__ import annotations

from textual.widgets import Static

from toktrail.tui.formatting import abbreviate_home
from toktrail.tui.layout import TuiDisplay, resolve_tui_display
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.services import ConfigData


class ConfigPane(ExportablePaneMixin, Static):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.tui_display: TuiDisplay = resolve_tui_display("full")

    def set_display(self, display: TuiDisplay) -> None:
        self.tui_display = display

    def set_data(self, data: ConfigData) -> None:
        summary = data.summary
        self.export_text = "\n".join(
            [
                "Config",
                f"config: {summary['config_path']}",
                f"prices: {summary['manual_prices_path']}",
                f"subscriptions: {summary['subscriptions_path']}",
                f"actual prices: {summary['actual_price_count']}",
                f"virtual prices: {summary['virtual_price_count']}",
            ]
        )
        if self.tui_display.compact:
            compact = "\n".join(
                [
                    "Config",
                    f"config: {abbreviate_home(str(summary['config_path']))}",
                    f"prices: {abbreviate_home(str(summary['manual_prices_path']))}",
                    f"actual prices: {summary['actual_price_count']}",
                    f"virtual prices: {summary['virtual_price_count']}",
                ]
            )
            self.update(compact)
            return
        self.update(self.export_text)
