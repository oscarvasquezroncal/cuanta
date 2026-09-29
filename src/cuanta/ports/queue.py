from __future__ import annotations

from collections.abc import Collection
from typing import Protocol

from cuanta.domain.queue import QueueEntry


class QueueStore(Protocol):
    def entries(self) -> tuple[QueueEntry, ...]: ...

    def append(self, args: tuple[str, ...], added_at: str) -> QueueEntry: ...

    def remove(self, ids: Collection[str]) -> tuple[str, ...]: ...

    def claim(self) -> bool: ...

    def release(self) -> None: ...

    def lock_file(self) -> str: ...
