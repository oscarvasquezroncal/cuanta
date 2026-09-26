from __future__ import annotations

import shutil
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from cuanta.ports.graph import GraphResult
from cuanta.ports.system import Completed

FIXTURE_REPOS = Path(__file__).parent / "fixtures" / "repos"


def copy_repo(name: str, destination: Path) -> Path:
    target = destination / name
    shutil.copytree(FIXTURE_REPOS / name, target)
    return target


@dataclass
class FakeStream:
    output: list[str]
    code: int = 0
    error: str = ""

    def lines(self) -> Iterator[str]:
        yield from self.output

    def wait(self) -> int:
        return self.code

    def stderr_text(self) -> str:
        return self.error

    def terminate(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    closed: bool = False


@dataclass
class FakeRunner:
    binaries: dict[str, str] = field(default_factory=dict)
    responses: dict[str, Completed] = field(default_factory=dict)
    streams: dict[str, FakeStream] = field(default_factory=dict)
    queued: dict[str, list[FakeStream]] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    cwds: list[Path | None] = field(default_factory=list)
    envs: list[Mapping[str, str] | None] = field(default_factory=list)
    stdins: list[str | None] = field(default_factory=list)
    unsets: list[tuple[str, ...]] = field(default_factory=list)

    def which(self, name: str) -> str | None:
        return self.binaries.get(name)

    def _key(self, args: Sequence[str]) -> Completed:
        joined = " ".join(args)
        for prefix in sorted(self.responses, key=len, reverse=True):
            if joined.startswith(prefix):
                return self.responses[prefix]
        return Completed(0, "", "")

    def run(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed:
        self.calls.append(tuple(args))
        self.cwds.append(cwd)
        self.envs.append(env)
        return self._key(args)

    def stream(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        unset: Sequence[str] = (),
    ) -> FakeStream:
        self.calls.append(tuple(args))
        self.cwds.append(cwd)
        self.envs.append(env)
        self.stdins.append(stdin_text)
        self.unsets.append(tuple(unset))
        joined = " ".join(args)
        for prefix in sorted(self.queued, key=len, reverse=True):
            if joined.startswith(prefix) and self.queued[prefix]:
                return self.queued[prefix].pop(0)
        for prefix in sorted(self.streams, key=len, reverse=True):
            if joined.startswith(prefix):
                return self.streams[prefix]
        return FakeStream([])


@dataclass
class FakeGraph:
    present: bool = False
    install_ok: bool = True
    update_ok: bool = True
    calls: list[str] = field(default_factory=list)

    def available(self) -> bool:
        return self.present

    def install(self) -> GraphResult:
        self.calls.append("install")
        if self.install_ok:
            self.present = True
        return GraphResult(self.install_ok, "network down" if not self.install_ok else "")

    def update(self, root: Path) -> GraphResult:
        self.calls.append("update")
        return GraphResult(self.update_ok, "" if self.update_ok else "parse error")
