# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.formatting import format_epoch_ms_compact
from toktrail.tui.formatting import (
    compact_model,
    compact_time,
    leaf_path,
    session_cost_label,
)
from toktrail.tui.layout import TuiDisplay
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.panes.table import move_table_to_key, restore_selected_key
from toktrail.tui.services import SessionsData


class SessionsPane(ExportablePaneMixin, Vertical):
    selected_session_key: str | None = None

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.tui_display: TuiDisplay = TuiDisplay(mode="full", columns=80, rows=24)

    def set_display(self, display: TuiDisplay) -> None:
        self.tui_display = display

    def compose(self) -> ComposeResult:
        table = DataTable(id="sessions-table")
        table.cursor_type = "row"
        yield table
        yield Static("No session selected.", id="sessions-detail")

    def set_data(self, data: SessionsData) -> None:
        if self.tui_display.mode == "full":
            self._set_full_data(data)
        elif self.tui_display.mode == "compact":
            self._set_compact_data(data)
        else:
            self._set_micro_data(data)

    def _set_full_data(self, data: SessionsData) -> None:
        table = self.query_one("#sessions-table", DataTable)
        detail = self.query_one("#sessions-detail", Static)
        table.clear(columns=True)
        table.add_columns(
            "Time",
            "Machine",
            "Area",
            "Harness",
            "Session",
            "Msgs",
            "Tokens",
            "Actual",
            "Virtual",
            "Path",
        )
        previous_selected = self.selected_session_key
        self._rows_by_key = {}
        for row in data.sessions:
            key = row.key
            self._rows_by_key[key] = row
            table.add_row(
                format_epoch_ms_compact(row.last_ms),
                row.machine_label,
                row.area_path or "-",
                row.harness,
                row.session_title or row.source_session_id,
                _format_int(row.message_count),
                _format_int(row.tokens.total),
                _format_cost(row.costs.actual_cost_usd),
                _format_cost(row.costs.virtual_cost_usd),
                row.cwd or row.source_dir or "-",
                key=key,
            )

        if not data.sessions:
            self.selected_session_key = None
            self.export_text = "Sessions\n(none)"
            detail.update("No session selected.")
        else:
            self.selected_session_key = restore_selected_key(
                previous_selected, self._rows_by_key.keys()
            )
            move_table_to_key(table, self.selected_session_key)
            self._update_detail(self.selected_session_key)
            self.export_text = self._build_export_text(data)

    def _set_compact_data(self, data: SessionsData) -> None:
        table = self.query_one("#sessions-table", DataTable)
        detail = self.query_one("#sessions-detail", Static)
        table.clear(columns=True)
        table.add_columns("Time", "Area", "Model", "Tokens", "Cost")
        previous_selected = self.selected_session_key
        self._rows_by_key = {}
        for row in data.sessions:
            key = row.key
            self._rows_by_key[key] = row
            table.add_row(
                compact_time(format_epoch_ms_compact(row.last_ms)),
                leaf_path(row.area_path),
                compact_model(row.models, limit=1),
                _format_int(row.tokens.total),
                session_cost_label(
                    float(row.costs.actual_cost_usd), float(row.costs.virtual_cost_usd)
                ),
                key=key,
            )
        if not data.sessions:
            self.selected_session_key = None
            self.export_text = "Sessions\n(none)"
            detail.update("No session selected.")
            return
        self.selected_session_key = restore_selected_key(
            previous_selected, self._rows_by_key.keys()
        )
        move_table_to_key(table, self.selected_session_key)
        self._update_detail(self.selected_session_key)
        self.export_text = self._build_export_text(data)

    def _set_micro_data(self, data: SessionsData) -> None:
        table = self.query_one("#sessions-table", DataTable)
        detail = self.query_one("#sessions-detail", Static)
        table.clear(columns=True)
        table.add_columns("Session")
        previous_selected = self.selected_session_key
        self._rows_by_key = {}
        for row in data.sessions:
            key = row.key
            self._rows_by_key[key] = row
            table.add_row(
                (
                    f"{compact_time(format_epoch_ms_compact(row.last_ms))} "
                    f"{row.harness} {leaf_path(row.area_path)} "
                    f"{_format_int(row.tokens.total)} "
                    f"{compact_model(row.models, limit=1)}"
                ),
                key=key,
            )
        if not data.sessions:
            self.selected_session_key = None
            self.export_text = "Sessions\n(none)"
            detail.update("No session selected.")
            return
        self.selected_session_key = restore_selected_key(
            previous_selected, self._rows_by_key.keys()
        )
        move_table_to_key(table, self.selected_session_key)
        self._update_detail(self.selected_session_key)
        self.export_text = self._build_export_text(data)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "sessions-table":
            return
        key = str(event.row_key.value)
        self.selected_session_key = key
        self._update_detail(key)

    def _update_detail(self, key: str | None) -> None:
        detail = self.query_one("#sessions-detail", Static)
        if key is None:
            detail.update("No session selected.")
            return
        row = self._rows_by_key.get(key)
        if row is None:
            detail.update("No session selected.")
            return
        detail.update(
            "\n".join(
                [
                    f"Key: {row.key}",
                    f"Harness: {row.harness}",
                    f"Area: {row.area_path or '-'}",
                    f"CWD: {row.cwd or '-'}",
                    f"Source dir: {row.source_dir or '-'}",
                    f"Models: {', '.join(row.models) if row.models else '-'}",
                ]
            )
        )

    def _build_export_text(self, data: SessionsData) -> str:
        lines = [
            "Sessions",
            "time\tmachine\tarea\tharness\tsession\tmsgs\ttokens\tactual\tvirtual\tpath",
        ]
        for row in data.sessions:
            lines.append(
                "\t".join(
                    [
                        format_epoch_ms_compact(row.last_ms),
                        row.machine_label,
                        row.area_path or "-",
                        row.harness,
                        row.session_title or row.source_session_id,
                        str(row.message_count),
                        str(row.tokens.total),
                        str(row.costs.actual_cost_usd),
                        str(row.costs.virtual_cost_usd),
                        row.cwd or row.source_dir or "-",
                    ]
                )
            )
        return "\n".join(lines)
