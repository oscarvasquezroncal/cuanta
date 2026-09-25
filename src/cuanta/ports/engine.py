from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest


class Engine(Protocol):
    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def version(self) -> str: ...

    def missing_flags(self) -> tuple[str, ...]: ...

    def cancel(self) -> None: ...

    def command(self, request: EngineRequest) -> list[str]: ...

    def run(
        self, request: EngineRequest, on_event: Callable[[EngineEvent], None]
    ) -> EngineOutcome: ...
