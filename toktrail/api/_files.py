from __future__ import annotations

import os
import shutil
from pathlib import Path


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    create_backup: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    backup_path = path.with_name(f"{path.name}.bak")
    tmp_path.write_text(text, encoding=encoding)
    if create_backup and path.exists():
        shutil.copy2(path, backup_path)
    os.replace(tmp_path, path)


__all__ = ["atomic_write_text"]
