from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence

from cuanta.domain.anatomy import AnatomyReport, analyze_anatomy
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.real_costs import (
    Attempt,
    CostReport,
    attempts,
    cost_report,
    costs_since,
    report_attempts,
)
from cuanta.domain.spectrum import resolve_agents, usage_events
from cuanta.ports.ledger import EventQuery, Ledger


def _phase_reports(
    items: Sequence[Attempt], runs: Mapping[str, Run], events: Sequence[LedgerEvent]
) -> dict[str, AnatomyReport]:
    if not events:
        return {}
    needed = {run_id for item in items for run_id in item.run_ids}
    by_run: dict[str, list[LedgerEvent]] = defaultdict(list)
    for event in events:
        if event.run_id in needed:
            by_run[event.run_id].append(event)
    reports: dict[str, AnatomyReport] = {}
    for item in items:
        grouped = tuple(event for run_id in item.run_ids for event in by_run[run_id])
        if not grouped:
            continue
        resolved = resolve_agents(grouped)
        role_events: dict[str, list[LedgerEvent]] = defaultdict(list)
        for event in resolved:
            role_events[event.run_id].append(event)
        selected: list[LedgerEvent] = []
        covered = True
        for run_id in item.run_ids:
            usage = usage_events(role_events[run_id])
            role = runs[run_id]
            if (
                not usage
                or any(event.cost_usd is None for event in usage)
                or (role.turns > 0 and len(usage) < role.turns)
            ):
                covered = False
                break
            selected.extend(usage)
        if covered:
            reports[item.run.id] = analyze_anatomy(resolved, selected)
    return reports


class CostsQuery:
    def __init__(
        self,
        ledger_factory: Callable[[], Ledger],
        has_ledger: Callable[[], bool],
        now_iso: Callable[[], str],
        completion: Callable[[str], str] | None = None,
    ) -> None:
        self._completion = completion
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
            runs = ledger.runs(since=start)
            items = attempts(runs, now)
            phases = _phase_reports(
                items, {run.id: run for run in runs}, ledger.events(EventQuery(since=start))
            )
            states = (
                {item.run.id: self._completion(item.run.id) for item in items}
                if self._completion is not None
                else None
            )
            return report_attempts(items, start, phases, states)
        finally:
            ledger.close()
