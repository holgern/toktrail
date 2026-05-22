# mypy: ignore-errors
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from toktrail.api.models import PriceRow, UnconfiguredModelRow
from toktrail.cli_parts.formatting import _format_int, _format_price
from toktrail.tui.panes.exportable import ExportablePaneMixin
from toktrail.tui.services import PricesData


class PricesPane(ExportablePaneMixin, Vertical):
    selected_unconfigured: UnconfiguredModelRow | None = None

    def compose(self) -> ComposeResult:
        configured = DataTable(id="prices-configured-table")
        configured.cursor_type = "row"
        configured.add_columns(
            "Kind",
            "Table",
            "Provider",
            "Model",
            "Input/1M",
            "Output/1M",
        )
        unconfigured = DataTable(id="prices-unconfigured-table")
        unconfigured.cursor_type = "row"
        unconfigured.add_columns(
            "Required",
            "Harness",
            "Provider",
            "Model",
            "Msgs",
            "Tokens",
        )
        yield configured
        yield unconfigured
        yield Static("No unconfigured model selected.", id="prices-detail")

    def set_data(self, data: PricesData) -> None:
        configured = self.query_one("#prices-configured-table", DataTable)
        unconfigured = self.query_one("#prices-unconfigured-table", DataTable)
        detail = self.query_one("#prices-detail", Static)
        configured.clear(columns=False)
        unconfigured.clear(columns=False)
        for row in data.rows:
            configured.add_row(
                row.source_kind,
                row.table,
                row.provider,
                row.model,
                _format_price(row.input_usd_per_1m),
                _format_price(row.output_usd_per_1m),
            )

        previous_key = self._unconfigured_row_key(self.selected_unconfigured)
        self._rows_by_key: dict[str, UnconfiguredModelRow] = {}
        for row in data.unconfigured:
            key = self._unconfigured_row_key(row)
            self._rows_by_key[key] = row
            unconfigured.add_row(
                ",".join(row.required),
                row.harness,
                row.provider_id,
                row.model_id,
                _format_int(row.message_count),
                _format_int(row.tokens.total),
                key=key,
            )

        if not data.unconfigured:
            self.selected_unconfigured = None
            detail.update("No unconfigured model selected.")
            self.export_text = self._build_export_text(data)
        else:
            if previous_key and previous_key in self._rows_by_key:
                selected_key = previous_key
            else:
                selected_key = self._unconfigured_row_key(data.unconfigured[0])
            self.selected_unconfigured = self._rows_by_key[selected_key]
            row_index = unconfigured.get_row_index(selected_key)
            unconfigured.move_cursor(row=row_index, column=0)
            self._update_detail(self.selected_unconfigured)
            self.export_text = self._build_export_text(data)

    def seed_manual_price_row(self) -> PriceRow | None:
        if self.selected_unconfigured is None:
            return None
        selected = self.selected_unconfigured
        return PriceRow(
            table="virtual",
            provider=selected.provider_id,
            model=selected.model_id,
            input_usd_per_1m=0.0,
            output_usd_per_1m=0.0,
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "prices-unconfigured-table":
            return
        key = str(event.row_key.value)
        selected = self._rows_by_key.get(key)
        if selected is None:
            return
        self.selected_unconfigured = selected
        self._update_detail(selected)

    def _update_detail(self, row: UnconfiguredModelRow | None) -> None:
        detail = self.query_one("#prices-detail", Static)
        if row is None:
            detail.update("No unconfigured model selected.")
            return
        detail.update(
            "\n".join(
                [
                    f"Model: {row.provider_id}/{row.model_id}",
                    f"Harness: {row.harness}",
                    f"Required: {', '.join(row.required)}",
                    f"Messages: {_format_int(row.message_count)}",
                    f"Tokens: {_format_int(row.tokens.total)}",
                ]
            )
        )

    def _unconfigured_row_key(self, row: UnconfiguredModelRow | None) -> str | None:
        if row is None:
            return None
        return f"{row.provider_id}:{row.model_id}:{row.thinking_level or '-'}"

    def _build_export_text(self, data: PricesData) -> str:
        lines = [
            f"Configured rows: {len(data.rows)}",
            "kind\ttable\tprovider\tmodel\tinput_per_1m\toutput_per_1m",
        ]
        for row in data.rows:
            lines.append(
                "\t".join(
                    [
                        row.source_kind,
                        row.table,
                        row.provider,
                        row.model,
                        str(row.input_usd_per_1m),
                        str(row.output_usd_per_1m),
                    ]
                )
            )
        lines.append("")
        lines.append("Unconfigured")
        lines.append("required\tharness\tprovider\tmodel\tmsgs\ttokens")
        for row in data.unconfigured:
            lines.append(
                "\t".join(
                    [
                        ",".join(row.required),
                        row.harness,
                        row.provider_id,
                        row.model_id,
                        str(row.message_count),
                        str(row.tokens.total),
                    ]
                )
            )
        return "\n".join(lines)
