# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static


class SubscriptionDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self, text: str, *, subscription_id: str) -> None:
        super().__init__()
        self._text = text
        self._subscription_id = subscription_id

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static(self._text, id="subscription-detail-content"),
            id="subscription-detail-scroll",
        )
