# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from toktrail.cli_parts.formatting import _format_cost, _format_int
from toktrail.formatting import format_epoch_ms_compact
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.services import SessionsData


class SessionsPane(ExportablePaneMixin, Vertical):
    selected_session_key: str | None = None

    def compose(self) -> ComposeResult:
        table = DataTable(id="sessions-table")
        table.cursor_type = "row"
        yield table
        yield Static("No session selected.", id="sessions-detail")

    def set_data(self, data: SessionsData) -> None:
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
        self._rows_by_key: dict[str, object] = {}
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
            if previous_selected and previous_selected in self._rows_by_key:
                self.selected_session_key = previous_selected
            else:
                self.selected_session_key = data.sessions[0].key
            selected_row_index = table.get_row_index(self.selected_session_key)
            table.move_cursor(row=selected_row_index, column=0)
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
