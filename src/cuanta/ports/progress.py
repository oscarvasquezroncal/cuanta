from __future__ import annotations

from typing import Protocol

from cuanta.domain.progress import ProgressEvent


class ProgressSink(Protocol):
    def publish(self, event: ProgressEvent) -> None: ...
