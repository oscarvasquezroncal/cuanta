from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ProcessRunner(Protocol):
    def which(self, name: str) -> str | None: ...

    def run(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed: ...

    def stream(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        unset: Sequence[str] = (),
    ) -> StreamHandle: ...


class StreamHandle(Protocol):
    def lines(self) -> Iterator[str]: ...

    def wait(self) -> int: ...

    def stderr_text(self) -> str: ...

    def terminate(self) -> None: ...

    def close(self) -> None: ...


class Clock(Protocol):
    def now_iso(self) -> str: ...

    def now_ms(self) -> int: ...

    def monotonic(self) -> float: ...
