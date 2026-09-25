from __future__ import annotations

from dataclasses import dataclass

from cuanta.domain.errors import DomainFailure
from cuanta.domain.new_files import DiffRow, original_of, side_by_side
from cuanta.ports.workspace import Workspace


@dataclass(frozen=True, slots=True)
class NewFilePair:
    new_path: str
    original_path: str
    rows: tuple[DiffRow, ...]


class NewFileReview:
    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def _texts(self, new_path: str) -> tuple[str, str, str]:
        try:
            original = original_of(new_path)
        except ValueError as error:
            raise DomainFailure(str(error), "pick a file that ends in .new.md") from error
        new_text = self._workspace.read_text(new_path)
        if new_text is None:
            raise DomainFailure(f"{new_path} no longer exists", "it was resolved already")
        return original, self._workspace.read_text(original) or "", new_text

    def load(self, new_path: str) -> NewFilePair:
        original, mine, new = self._texts(new_path)
        return NewFilePair(new_path, original, tuple(side_by_side(mine, new)))

    def keep_mine(self, new_path: str) -> str:
        original, _, _ = self._texts(new_path)
        self._workspace.remove(new_path)
        return original

    def use_new(self, new_path: str) -> str:
        original, _, new = self._texts(new_path)
        self._workspace.write_text(original, new)
        self._workspace.remove(new_path)
        return original
