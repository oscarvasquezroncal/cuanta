from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ScanResult:
    file_count: int
    nested_claude_md: tuple[str, ...] = ()
    test_files: Mapping[str, int] = field(default_factory=dict)
    entry_candidates: tuple[str, ...] = ()
    files: tuple[str, ...] = ()


class Workspace(Protocol):
    @property
    def root(self) -> Path: ...

    def read_text(self, relative: str) -> str | None: ...

    def exists(self, relative: str) -> bool: ...

    def is_dir(self, relative: str) -> bool: ...

    def list_names(self, relative: str) -> tuple[str, ...]: ...

    def write_text(self, relative: str, content: str) -> None: ...

    def scan(self, extra_exclusions: frozenset[str], collect_files: bool = False) -> ScanResult: ...

    def sha256(self, relative: str) -> str | None: ...

    def size_bytes(self, relative: str) -> int: ...

    def files_under(self, relative: str) -> tuple[str, ...]: ...

    def remove(self, relative: str) -> None: ...


class HomeReader(Protocol):
    def read_text(self, relative: str) -> str | None: ...

    def path(self, relative: str) -> Path: ...

    def size_bytes(self, relative: str) -> int | None: ...
