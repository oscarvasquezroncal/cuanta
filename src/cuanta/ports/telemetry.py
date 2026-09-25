from __future__ import annotations

from typing import Protocol

from cuanta.domain.telemetry import WiringPlan, WiringReport


class EngineWiring(Protocol):
    @property
    def engine(self) -> str: ...

    def enable(self, port: int, project: str) -> WiringReport: ...

    def disable(self) -> WiringReport: ...

    def status(self, port: int) -> WiringReport: ...

    def plan(self) -> WiringPlan: ...
