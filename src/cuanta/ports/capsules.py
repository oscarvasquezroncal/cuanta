from __future__ import annotations

from typing import Protocol


class CapsuleStore(Protocol):
    def put(self, content: str) -> tuple[str, str, int]: ...

    def read(self, digest: str) -> str | None: ...
