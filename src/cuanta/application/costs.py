from __future__ import annotations

from collections.abc import Callable

from cuanta.domain.real_costs import CostReport, cost_report, costs_since
from cuanta.ports.ledger import Ledger


class CostsQuery:
    def __init__(
        self,
        ledger_factory: Callable[[], Ledger],
        has_ledger: Callable[[], bool],
        now_iso: Callable[[], str],
    ) -> None:
        self._ledger_factory = ledger_factory
        self._has_ledger = has_ledger
        self._now_iso = now_iso

    def report(self, since: str = "") -> CostReport:
        now = self._now_iso()
        start = since or costs_since(now)
        if not self._has_ledger():
            return cost_report((), start)
        ledger = self._ledger_factory()
        try:
            return cost_report(ledger.runs(since=start), start, now)
        finally:
            ledger.close()
