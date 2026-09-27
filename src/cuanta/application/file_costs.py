from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from cuanta.domain.file_costs import FileCostReport, file_cost_report
from cuanta.domain.real_costs import attempts
from cuanta.ports.code_index import CodeIndex
from cuanta.ports.ledger import Ledger


@dataclass(frozen=True, slots=True)
class FileCostView:
    report: FileCostReport = field(default_factory=FileCostReport)
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return not self.unavailable_reason


class FileCostsQuery:
    def __init__(
        self,
        index_factory: Callable[[], CodeIndex],
        ledger_factory: Callable[[], Ledger],
        now_iso: Callable[[], str],
        *,
        error_types: tuple[type[Exception], ...] = (OSError, ValueError),
    ) -> None:
        self._index_factory = index_factory
        self._ledger_factory = ledger_factory
        self._now_iso = now_iso
        self._error_types = error_types

    def report(self) -> FileCostView:
        try:
            index = self._index_factory()
            try:
                rows = index.rows("history")
            finally:
                index.close()
        except self._error_types:
            return FileCostView(unavailable_reason="index_unavailable")
        try:
            ledger = self._ledger_factory()
            try:
                runs = ledger.runs()
            finally:
                ledger.close()
        except self._error_types:
            return FileCostView(unavailable_reason="ledger_unavailable")
        now = self._now_iso()
        return FileCostView(file_cost_report(rows, attempts(runs, now), now))
