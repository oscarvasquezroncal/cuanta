from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from cuanta.application.run_reports import RunReports
from cuanta.application.trials import TrialStore
from cuanta.domain.anatomy import AnatomyReport, analyze_anatomy
from cuanta.domain.cost_trend import DEFAULT_TREND_RUNS, CostTrend, cost_trend
from cuanta.domain.governor_report import blocked_calls, parse_governor
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.real_costs import (
    Attempt,
    CostReport,
    attempts,
    cost_report,
    costs_since,
    report_attempts,
)
from cuanta.domain.run_metrics import RunMetrics, run_metrics
from cuanta.domain.run_mode import run_mode
from cuanta.domain.scout_report import DOCS_KEY, SCOUT_KEY, parse_scout
from cuanta.domain.spectrum import resolve_agents, usage_events
from cuanta.domain.time_costs import time_medians
from cuanta.ports.ledger import EventQuery, Ledger
from cuanta.ports.workspace import Workspace


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
        workspace: Workspace | None = None,
    ) -> None:
        self._completion = completion
        self._workspace = workspace
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
            events = ledger.events(EventQuery(since=start))
            phases = _phase_reports(items, {run.id: run for run in runs}, events)
            states = (
                {item.run.id: self._completion(item.run.id) for item in items}
                if self._completion is not None
                else None
            )
            metadata = (
                {item.run.id: RunReports(self._workspace).meta(item.run.id) or {} for item in items}
                if self._workspace is not None
                else {}
            )
            return replace(
                report_attempts(items, start, phases, states),
                time_medians=time_medians(items, events, metadata),
            )
        finally:
            ledger.close()

    def trend(self, limit: int = DEFAULT_TREND_RUNS) -> CostTrend:
        if not self._has_ledger():
            return CostTrend(limit)
        ledger = self._ledger_factory()
        try:
            return cost_trend(attempts(ledger.runs(), self._now_iso()), limit)
        finally:
            ledger.close()

    def metrics(self, since: str = "") -> tuple[RunMetrics, ...]:
        if self._workspace is None:
            raise ValueError("run metrics read run metadata and need the state workspace")
        now = self._now_iso()
        start = since or costs_since(now)
        if not self._has_ledger():
            return ()
        reports = RunReports(self._workspace)
        ledger = self._ledger_factory()
        try:
            runs = ledger.runs(since=start)
            by_id = {run.id: run for run in runs}
            forecasts = {item.forecast.run_id: item.forecast for item in ledger.forecasts()}
            trials = TrialStore(self._workspace, ledger, self._now_iso)
            found: list[RunMetrics] = []
            ordered = sorted(
                attempts(runs, now),
                key=lambda item: (item.run.started_at, item.run.id),
                reverse=True,
            )
            for item in ordered:
                events = [
                    event
                    for run_id in item.run_ids
                    for event in ledger.events(EventQuery(run_id=run_id))
                ]
                meta = reports.meta(item.run.id) or {}
                trial = trials.load(item.run.id)
                found.append(
                    run_metrics(
                        item,
                        tuple(by_id[run_id] for run_id in item.run_ids[1:] if run_id in by_id),
                        resolve_agents(events),
                        forecasts.get(item.run.id),
                        parse_governor(meta.get("governor"), blocked_calls(events)),
                        parse_scout(meta.get(SCOUT_KEY), meta.get(DOCS_KEY)),
                        None if trial is None else trial.added + trial.removed,
                        run_mode(meta),
                    )
                )
            return tuple(found)
        finally:
            ledger.close()
