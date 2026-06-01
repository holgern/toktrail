# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from toktrail.tui.layout import ResolvedTuiMode


def _build_help_text(mode: ResolvedTuiMode) -> str:
    if mode == "micro":
        return _micro_text()
    if mode == "compact":
        return _compact_text()
    return _full_text()


def _full_text() -> str:
    return "\n".join(
        [
            "# Keybindings",
            "",
            "## Navigation",
            "  left/right   browse day-by-day (sessions, areas)",
            "  up/down, j/k move row selection in tables",
            "  1-6          switch pane (dash, sess, area, price, subs, cfg)",
            "",
            "## Dashboard views",
            "  t            today",
            "  d            daily",
            "  w            weekly",
            "",
            "## Sessions pane",
            "  enter        open selected session detail",
            "  esc          go back from session detail",
            "  i            toggle inline detail",
            "  s            assign selected session to area",
            "  l            assign latest session to area",
            "",
            "## Subscriptions pane",
            "  enter        open selected subscription detail",
            "  esc          go back from subscription detail",
            "",
            "## Areas pane",
            "  a            create area",
            "  u            set active area",
            "  c            clear active area",
            "",
            "## Prices pane",
            "  e            edit selected price",
            "  p            toggle prices view",
            "",
            "## General",
            "  r            refresh data",
            "  y            copy current view to clipboard",
            "  Y            export current view",
            "  ? / h        this help",
            "  q            quit",
            "",
            "Press Escape to close.",
        ]
    )


def _compact_text() -> str:
    return "\n".join(
        [
            "# Keybindings",
            "",
            "left/right  day-by-day",
            "up/down,j/k select row",
            "1-6         switch pane",
            "t/d/w       today/daily/weekly",
            "a/u/c       area create/use/clear",
            "s/l         assign session",
            "i           toggle inline detail",
            "enter       open",
            "esc         back from detail",
            "e           edit price",
            "r           refresh",
            "y/Y         copy/export",
            "?           help",
            "q           quit",
            "",
            "Escape to close.",
        ]
    )


def _micro_text() -> str:
    return "\n".join(
        [
            "# Keys",
            "left/right day",
            "up/down row",
            "1-6 pane",
            "t/d/w dash",
            "a/u/c area",
            "s/l assign",
            "enter open",
            "esc back",
            "j/k row",
            "r refresh",
            "? help  q quit",
            "Esc close",
        ]
    )


class HelpScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Close help"),
    ]

    def __init__(self, mode: ResolvedTuiMode = "full") -> None:
        super().__init__()
        self._mode = mode

    def compose(self) -> ComposeResult:
        yield VerticalScroll(Static(_build_help_text(self._mode), id="help-content"))
