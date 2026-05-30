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
