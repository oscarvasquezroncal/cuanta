from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from cuanta.domain.code_index import (
    IndexedFile,
    IndexHistory,
    IndexReport,
    IndexRow,
    IndexStructure,
    IndexTable,
)


class CodeIndex(Protocol):
    def files(self) -> tuple[IndexedFile, ...]: ...

    def rows(self, table: IndexTable, path: str = "") -> tuple[IndexRow, ...]: ...

    def replace_files(self, files: Sequence[IndexedFile], removed: Sequence[str]) -> None: ...

    def put_rows(self, table: IndexTable, rows: Sequence[IndexRow]) -> None: ...

    def replace_rows(self, table: IndexTable, path: str, rows: Sequence[IndexRow]) -> None: ...

    def meta(self) -> Mapping[str, str]: ...

    def set_meta(self, values: Mapping[str, str]) -> None: ...

    def close(self) -> None: ...


class IndexInventory(Protocol):
    def candidates(self) -> tuple[IndexedFile, ...]: ...

    def read(self, path: str) -> str | None: ...


class IndexExtractor(Protocol):
    def extract(self, file: IndexedFile, text: str, paths: tuple[str, ...]) -> IndexStructure: ...

    def resolve(self, path: str, module: str, paths: tuple[str, ...]) -> str: ...


class IndexGraph(Protocol):
    def records(self, files: tuple[IndexedFile, ...]) -> IndexStructure: ...

    def fingerprint(self) -> str: ...

    def request_refresh(self, files: tuple[IndexedFile, ...]) -> None: ...


class IndexKnowledge(Protocol):
    def reports(self) -> tuple[IndexReport, ...]: ...

    def history(self) -> tuple[IndexHistory, ...]: ...
