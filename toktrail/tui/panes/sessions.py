# mypy: ignore-errors
from __future__ import annotations

from textual.widgets import Static

from toktrail.tui.services import SessionsData


class SessionsPane(Static):
    selected_session_key: str | None = None

    def set_data(self, data: SessionsData) -> None:
        self.selected_session_key = data.session_keys[0] if data.session_keys else None
        rows = ["Sessions"]
        if not data.session_keys:
            rows.append("(none)")
        else:
            rows.extend(data.session_keys)
        self.update("\n".join(rows))
