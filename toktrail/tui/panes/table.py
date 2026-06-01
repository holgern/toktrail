from __future__ import annotations

from collections.abc import Collection
from typing import TypeVar

from textual.widgets import DataTable

RowKey = TypeVar("RowKey", bound=str)


def restore_selected_key(
    previous: RowKey | None,
    keys: Collection[RowKey],
) -> RowKey | None:
    if not keys:
        return None
    if previous is not None and previous in keys:
        return previous
    return next(iter(keys))


def move_table_to_key(table: DataTable, key: str | None) -> None:
    if key is None:
        return
    table.move_cursor(row=table.get_row_index(key), column=0)


def move_table_by(table: DataTable, delta: int) -> None:
    if table.row_count <= 0:
        return
    current = getattr(table, "cursor_row", 0)
    target = max(0, min(table.row_count - 1, current + delta))
    table.move_cursor(row=target, column=0)
