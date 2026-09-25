from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cuanta.domain.testing import TestOutcome


@dataclass(frozen=True, slots=True)
class RunnerResult:
    outcome: TestOutcome
    output: str
    command: str


class TestRunner(Protocol):
    @property
    def name(self) -> str: ...

    def default_command(self) -> tuple[str, ...]: ...

    def run(
        self,
        base_command: Sequence[str],
        root: Path,
        scratch: Path,
        env: Mapping[str, str] | None = None,
    ) -> RunnerResult: ...
