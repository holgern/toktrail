# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.tui.formatting import leaf_path
from toktrail.tui.layout import TuiDisplay
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.panes.table import move_table_to_key, restore_selected_key
from toktrail.tui.services import AreasData


class AreasPane(ExportablePaneMixin, Vertical):
    selected_area_path: str | None = None

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.tui_display: TuiDisplay = TuiDisplay(mode="full", columns=80, rows=24)

    def set_display(self, display: TuiDisplay) -> None:
        self.tui_display = display

    def compose(self) -> ComposeResult:
        table = DataTable(id="areas-table")
        table.cursor_type = "row"
        yield table
        yield Static("No area selected.", id="areas-detail")

    def set_data(self, data: AreasData) -> None:
        if self.tui_display.mode == "full":
            self._set_full_data(data)
        elif self.tui_display.mode == "compact":
            self._set_compact_data(data)
        else:
            self._set_micro_data(data)

    def _set_full_data(self, data: AreasData) -> None:
        table = self.query_one("#areas-table", DataTable)
        detail = self.query_one("#areas-detail", Static)
        table.clear(columns=True)
        table.add_columns(
            "Active", "Area", "Depth", "Msgs", "Tokens", "Actual", "Virtual"
        )

        self._latest_usage_rows = data.usage_rows
        usage_by_path = {row.path: row for row in data.usage_rows if row.path}
        previous_selected = self.selected_area_path
        for path in data.area_paths:
            usage = usage_by_path.get(path)
            depth = usage.depth if usage is not None else path.count("/")
            message_count = usage.message_count if usage is not None else 0
            token_total = usage.tokens.total if usage is not None else 0
            actual_cost = usage.costs.actual_cost_usd if usage is not None else 0.0
            virtual_cost = usage.costs.virtual_cost_usd if usage is not None else 0.0
            table.add_row(
                "*" if path == data.active_area else "",
                path,
                str(depth),
                _format_int(message_count),
                _format_int(token_total),
                _format_cost(actual_cost),
                _format_cost(virtual_cost),
                key=path,
            )

        if not data.area_paths:
            self.selected_area_path = None
            self.export_text = "Areas\n(none)"
            detail.update("No area selected.")
        else:
            self.selected_area_path = restore_selected_key(
                previous_selected, data.area_paths
            )
            move_table_to_key(table, self.selected_area_path)
            self._update_detail(self.selected_area_path, usage_by_path)
            self.export_text = self._build_export_text(data, usage_by_path)

    def _set_compact_data(self, data: AreasData) -> None:
        table = self.query_one("#areas-table", DataTable)
        detail = self.query_one("#areas-detail", Static)
        table.clear(columns=True)
        table.add_columns("*", "Area", "Tokens", "Cost")
        self._latest_usage_rows = data.usage_rows
        usage_by_path = {row.path: row for row in data.usage_rows if row.path}
        previous_selected = self.selected_area_path
        for path in data.area_paths:
            usage = usage_by_path.get(path)
            token_total = usage.tokens.total if usage is not None else 0
            actual_cost = (
                float(usage.costs.actual_cost_usd) if usage is not None else 0.0
            )
            virtual_cost = (
                float(usage.costs.virtual_cost_usd) if usage is not None else 0.0
            )
            cost_value = actual_cost if actual_cost > 0 else virtual_cost
            table.add_row(
                "*" if path == data.active_area else "",
                leaf_path(path),
                _format_int(token_total),
                _format_cost(cost_value),
                key=path,
            )
        if not data.area_paths:
            self.selected_area_path = None
            self.export_text = "Areas\n(none)"
            detail.update("No area selected.")
            return
        self.selected_area_path = restore_selected_key(
            previous_selected, data.area_paths
        )
        move_table_to_key(table, self.selected_area_path)
        self._update_detail(self.selected_area_path, usage_by_path)
        self.export_text = self._build_export_text(data, usage_by_path)

    def _set_micro_data(self, data: AreasData) -> None:
        table = self.query_one("#areas-table", DataTable)
        detail = self.query_one("#areas-detail", Static)
        table.clear(columns=True)
        table.add_columns("Area")
        self._latest_usage_rows = data.usage_rows
        usage_by_path = {row.path: row for row in data.usage_rows if row.path}
        previous_selected = self.selected_area_path
        for path in data.area_paths:
            usage = usage_by_path.get(path)
            token_total = usage.tokens.total if usage is not None else 0
            actual_cost = (
                float(usage.costs.actual_cost_usd) if usage is not None else 0.0
            )
            virtual_cost = (
                float(usage.costs.virtual_cost_usd) if usage is not None else 0.0
            )
            cost_value = actual_cost if actual_cost > 0 else virtual_cost
            marker = "*" if path == data.active_area else " "
            line = (
                f"{marker} {leaf_path(path)} {_format_int(token_total)} "
                f"{_format_cost(cost_value)}"
            )
            table.add_row(
                line,
                key=path,
            )
        if not data.area_paths:
            self.selected_area_path = None
            self.export_text = "Areas\n(none)"
            detail.update("No area selected.")
            return
        self.selected_area_path = restore_selected_key(
            previous_selected, data.area_paths
        )
        move_table_to_key(table, self.selected_area_path)
        self._update_detail(self.selected_area_path, usage_by_path)
        self.export_text = self._build_export_text(data, usage_by_path)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "areas-table":
            return
        key = str(event.row_key.value)
        self.selected_area_path = key
        usage_by_path = {
            row.path: row
            for row in getattr(self, "_latest_usage_rows", ())
            if row.path is not None
        }
        self._update_detail(key, usage_by_path)

    def _update_detail(
        self, path: str | None, usage_by_path: dict[str, object]
    ) -> None:
        detail = self.query_one("#areas-detail", Static)
        if path is None:
            detail.update("No area selected.")
            return
        usage = usage_by_path.get(path)
        if usage is None:
            detail.update(f"Area: {path}\nNo usage today.")
            return
        detail.update(
            "\n".join(
                [
                    f"Area: {path}",
                    f"Depth: {usage.depth}",
                    f"Messages: {_format_int(usage.message_count)}",
                    f"Tokens: {_format_int(usage.tokens.total)}",
                    f"Actual: {_format_cost(usage.costs.actual_cost_usd)}",
                    f"Virtual: {_format_cost(usage.costs.virtual_cost_usd)}",
                ]
            )
        )

    def _build_export_text(
        self, data: AreasData, usage_by_path: dict[str, object]
    ) -> str:
        lines = ["Areas", "active\tarea\tdepth\tmsgs\ttokens\tactual\tvirtual"]
        for path in data.area_paths:
            usage = usage_by_path.get(path)
            lines.append(
                "\t".join(
                    [
                        "*" if path == data.active_area else "",
                        path,
                        str(usage.depth if usage is not None else path.count("/")),
                        str(usage.message_count if usage is not None else 0),
                        str(usage.tokens.total if usage is not None else 0),
                        str(usage.costs.actual_cost_usd if usage is not None else 0),
                        str(usage.costs.virtual_cost_usd if usage is not None else 0),
                    ]
                )
            )
        return "\n".join(lines)
