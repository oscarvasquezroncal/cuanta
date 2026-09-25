from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GraphResult:
    ok: bool
    detail: str = ""


class GraphTool(Protocol):
    def available(self) -> bool: ...

    def install(self) -> GraphResult: ...

    def update(self, root: Path) -> GraphResult: ...
