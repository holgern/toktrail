# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class AreaFormScreen(ModalScreen[str | None]):
    def __init__(self, initial_value: str = "") -> None:
        super().__init__()
        self._initial_value = initial_value

    def compose(self) -> ComposeResult:
        yield Label("Area path")
        yield Input(self._initial_value, id="area-path")
        with Horizontal():
            yield Button("Save", id="save")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            value = self.query_one("#area-path", Input).value.strip()
            self.dismiss(value or None)
            return
        self.dismiss(None)
