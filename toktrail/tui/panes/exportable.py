# mypy: ignore-errors
from __future__ import annotations


class ExportablePaneMixin:
    export_text: str = ""

    def get_export_text(self) -> str:
        return self.export_text
