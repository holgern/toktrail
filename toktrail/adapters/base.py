from __future__ import annotations

from pathlib import Path
from typing import Protocol

from toktrail.models import UsageEvent


class HarnessAdapter(Protocol):
    name: str

    def parse(self, source_path: Path) -> list[UsageEvent]:
        ...
