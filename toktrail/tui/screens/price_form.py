# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from toktrail.api.models import PriceRow


class PriceFormScreen(ModalScreen[PriceRow | None]):
    def __init__(self, seed: PriceRow) -> None:
        super().__init__()
        self._seed = seed

    def compose(self) -> ComposeResult:
        yield Label("Provider")
        yield Input(self._seed.provider, id="provider")
        yield Label("Model")
        yield Input(self._seed.model, id="model")
        yield Label("Table")
        yield Select(
            [("virtual", "virtual"), ("actual", "actual")],
            value=self._seed.table,
            id="table",
        )
        yield Label("Input USD / 1M")
        yield Input(str(self._seed.input_usd_per_1m), id="input-price")
        yield Label("Output USD / 1M")
        yield Input(str(self._seed.output_usd_per_1m), id="output-price")
        with Horizontal():
            yield Button("Save", id="save")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            self.dismiss(None)
            return
        provider = self.query_one("#provider", Input).value.strip()
        model = self.query_one("#model", Input).value.strip()
        table = self.query_one("#table", Select).value
        input_text = self.query_one("#input-price", Input).value.strip()
        output_text = self.query_one("#output-price", Input).value.strip()
        try:
            input_price = float(input_text)
            output_price = float(output_text)
        except ValueError:
            self.dismiss(None)
            return
        if not provider or not model or table not in {"actual", "virtual"}:
            self.dismiss(None)
            return
        self.dismiss(
            PriceRow(
                table=table,
                provider=provider,
                model=model,
                input_usd_per_1m=input_price,
                output_usd_per_1m=output_price,
            )
        )
