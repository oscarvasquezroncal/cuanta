from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from cuanta.domain.gitignore import IgnoreRules


@dataclass(frozen=True, slots=True)
class FileStat:
    sha256: str
    size: int
    mtime_ns: int
    executable: bool = False


@dataclass(frozen=True, slots=True)
class SandboxCopy:
    origin: Path
    root: Path
    slot: Path
    base: Mapping[str, FileStat]
    rules: IgnoreRules
    linked: tuple[str, ...] = ()
    linked_files: int = 0
    copied_files: int = 0
    copied_bytes: int = 0
    seconds: float = 0.0
    skipped_links: tuple[str, ...] = ()
    fingerprint: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    root_rules: IgnoreRules = field(default_factory=IgnoreRules)
    hidden: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    skipped_caches: tuple[str, ...] = ()
    skipped_outputs: tuple[str, ...] = ()
    outside_dependencies: tuple[str, ...] = ()
    python_path: str = ""
    unreadable: tuple[str, ...] = ()
    state: Mapping[str, str] = field(default_factory=dict)
    reused: bool = False

    def hashes(self) -> dict[str, str]:
        return {path: item.sha256 for path, item in self.base.items()}


class ProjectSandbox(Protocol):
    def create(self, origin: Path) -> SandboxCopy: ...

    def manifest(self, copy: SandboxCopy) -> dict[str, str]: ...

    def read(self, copy: SandboxCopy, relative: str) -> bytes | None: ...

    def ignored_changes(self, copy: SandboxCopy) -> tuple[str, ...]: ...

    def executable(self, copy: SandboxCopy, relative: str) -> bool: ...

    def dependencies_changed(self, copy: SandboxCopy) -> tuple[str, ...]: ...

    def state_changed(self, copy: SandboxCopy) -> tuple[str, ...]: ...

    def mode_changes(self, copy: SandboxCopy) -> tuple[str, ...]: ...

    def remove(self, copy: SandboxCopy) -> bool: ...

    def keep(self, copy: SandboxCopy) -> None: ...
