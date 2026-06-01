# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static


class SessionDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self, text: str, *, session_key: str) -> None:
        super().__init__()
        self._text = text
        self._session_key = session_key

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static(self._text, id="session-detail-content"),
            id="session-detail-scroll",
        )
