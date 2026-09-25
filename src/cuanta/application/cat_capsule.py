from __future__ import annotations

from dataclasses import dataclass

from cuanta.domain.capsules import Level, LineRange, failure_window, slice_lines
from cuanta.domain.errors import DomainFailure, EnvironmentFailure
from cuanta.domain.ledger import Capsule
from cuanta.ports.capsules import CapsuleStore
from cuanta.ports.ledger import Ledger


@dataclass(frozen=True, slots=True)
class CapsuleView:
    capsule: Capsule
    level: Level
    lines: tuple[tuple[int, str], ...]
    total_lines: int


class CatCapsule:
    def __init__(self, ledger: Ledger, store: CapsuleStore) -> None:
        self._ledger = ledger
        self._store = store

    def run(self, reference: str, level: Level, selection: LineRange | None) -> CapsuleView:
        capsule = self._ledger.get_capsule(reference)
        if capsule is None:
            raise DomainFailure(
                f"no capsule matches {reference}", "list capsules with cuanta test --json"
            )
        if level is Level.L0 and selection is None:
            return CapsuleView(capsule, level, (), capsule.lines)
        if level is Level.L1 and selection is None:
            summary = capsule.summary.splitlines()
            return CapsuleView(capsule, level, tuple(enumerate(summary, 1)), capsule.lines)
        content = self._store.read(capsule.sha256)
        if content is None:
            raise EnvironmentFailure(f"capsule file missing: {capsule.path}", "re-run cuanta test")
        lines = content.splitlines()
        if selection is not None:
            chosen = slice_lines(lines, selection)
        elif level is Level.L2:
            chosen = failure_window(lines)
        else:
            chosen = list(enumerate(lines, 1))
        return CapsuleView(capsule, level, tuple(chosen), len(lines))
