# mypy: ignore-errors
from __future__ import annotations

from textual.widgets import Static

from toktrail.tui.services import AreasData


class AreasPane(Static):
    selected_area_path: str | None = None

    def set_data(self, data: AreasData) -> None:
        self.selected_area_path = data.area_paths[0] if data.area_paths else None
        rows = ["Areas"]
        if not data.area_paths:
            rows.append("(none)")
        else:
            for path in data.area_paths:
                suffix = " *" if path == data.active_area else ""
                rows.append(f"{path}{suffix}")
        self.update("\n".join(rows))
