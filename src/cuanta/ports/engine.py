from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class TurnInput(Protocol):
    def accepts_turns(self) -> bool: ...

    def send_turn(self, text: str) -> bool: ...


@runtime_checkable
class RunStop(Protocol):
    def halt(self, subtype: str) -> None: ...


@runtime_checkable
class Resumable(Protocol):
    def resumable(self) -> bool: ...
