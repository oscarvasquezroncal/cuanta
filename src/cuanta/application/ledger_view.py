from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cuanta.domain.ledger import Run
from cuanta.ports.ledger import Ledger

RUN_LIMIT = 5000


@dataclass(frozen=True, slots=True)
class RunFilter:
    kind: str = ""
    engine: str = ""
    since: str = ""


def matches(run: Run, selection: RunFilter) -> bool:
    if selection.kind and run.kind != selection.kind:
        return False
    if selection.engine and (run.engine or "none") != selection.engine:
        return False
    return not selection.since or run.started_at[: len(selection.since)] >= selection.since


def filter_runs(runs: Sequence[Run], selection: RunFilter) -> tuple[Run, ...]:
    return tuple(run for run in runs if matches(run, selection))


def facets(runs: Sequence[Run]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    kinds = sorted({run.kind for run in runs if run.kind})
    engines = sorted({run.engine or "none" for run in runs})
    return tuple(kinds), tuple(engines)


class RunsQuery:
    def __init__(
        self, ledger_factory: Callable[[], Ledger], has_ledger: Callable[[], bool]
    ) -> None:
        self._ledger_factory = ledger_factory
        self._has_ledger = has_ledger

    def run(self, limit: int = RUN_LIMIT) -> tuple[Run, ...]:
        if not self._has_ledger():
            return ()
        ledger = self._ledger_factory()
        try:
            return ledger.runs(limit=limit)
        finally:
            ledger.close()
