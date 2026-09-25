from __future__ import annotations

from typing import Protocol

from cuanta.domain.bench import BenchTask


class BenchSandbox(Protocol):
    def prepare(self, task: BenchTask, with_kit: bool, label: str) -> str: ...

    def accept(self, task: BenchTask, root: str) -> tuple[bool, str]: ...

    def discard(self, root: str) -> None: ...
