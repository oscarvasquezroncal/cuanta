from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ListenerStatus:
    running: bool
    port: int = 0
    pid: int = 0
    received: int = 0
    written: int = 0
    uptime_s: float = 0.0
    owned: bool = False


class ListenerControl(Protocol):
    def status(self) -> ListenerStatus: ...

    def free_port(self, preferred: int) -> int: ...

    def start_background(self, port: int) -> ListenerStatus: ...

    def stop(self) -> bool: ...

    def serve(self, port: int, on_ready: Callable[[ListenerStatus], None]) -> None: ...

    def scoped(self, port: int) -> AbstractContextManager[ListenerStatus]: ...
