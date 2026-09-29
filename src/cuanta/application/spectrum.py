from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from cuanta.application.index_reporting import run_index_metrics
from cuanta.application.results import stored_shape
from cuanta.domain.anatomy import analyze_anatomy
from cuanta.domain.cost_trend import CostTrend
from cuanta.domain.errors import DomainFailure
from cuanta.domain.ledger import LedgerEvent, RouteAudit, Run
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.outcomes import CROSS_KIND
from cuanta.domain.overhead import SessionOverhead, session_overhead
from cuanta.domain.pricing import PriceTable
from cuanta.domain.read_efficiency import read_efficiency
from cuanta.domain.spectrum import (
    Row,
    SpectrumReport,
    View,
    Window,
    analyze,
    changed_paths,
    file_utilization,
    grouped,
    plan_weeks,
    plan_windows,
    resolve_agents,
    tool_events,
    usage_events,
)
from cuanta.domain.testing import GatewayStatus
from cuanta.ports.ledger import EventQuery, Ledger

HU_PATTERN = re.compile(r"^HU-\d+$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Selection:
    run: str = ""
    session: str = ""
    since: str = ""


@dataclass(frozen=True, slots=True)
class SpectrumResult:
    report: SpectrumReport
    runs: tuple[Run, ...]
    events: tuple[LedgerEvent, ...]
    audits: tuple[RouteAudit, ...] = ()
    trend: CostTrend = field(default_factory=CostTrend)

    def rows(self, view: View) -> list[Row]:
        resolved = resolve_agents(self.events)
        return list(grouped(view, usage_events(resolved), tool_events(resolved)))

    @property
    def overhead(self) -> SessionOverhead:
        return session_overhead(self.events, 0)

    def windows(self) -> tuple[list[Window], list[Window]]:
        usage = usage_events(resolve_agents(self.events))
        return plan_windows(usage), plan_weeks(usage)


ALL_SESSIONS = "0"


class SpectrumQuery:
    def __init__(
        self,
        ledger: Ledger,
        prices: PriceTable | None = None,
        metadata: Callable[[str], Mapping[str, object] | None] | None = None,
        reports: Callable[[str], str | None] | None = None,
        roots: Callable[[Run], Sequence[str]] | None = None,
    ) -> None:
        self._ledger = ledger
        self._prices = prices
        self._metadata = metadata
        self._reports = reports
        self._roots = roots

    def _runs(self, selection: Selection) -> tuple[Run, ...]:
        if selection.run and HU_PATTERN.match(selection.run):
            runs = self._ledger.runs(hu_ref=selection.run.upper())
            if not runs:
                raise DomainFailure(
                    f"no runs tagged {selection.run.upper()}", "tag runs with cuanta pounce --hu"
                )
            return runs
        if selection.run:
            run = self._ledger.get_run(selection.run)
            if run is None:
                matches = [
                    item
                    for item in self._ledger.runs()
                    if item.id.startswith(selection.run.upper())
                ]
                if len(matches) != 1:
                    raise DomainFailure(
                        f"no run matches {selection.run}", "cuanta ledger export --json lists runs"
                    )
                run = matches[0]
            return (run,)
        if selection.session or selection.since:
            return ()
        latest = self._ledger.runs(limit=1)
        if not latest:
            raise DomainFailure(
                "no runs recorded yet · nap",
                "cuanta init, cuanta pounce, or cuanta spectrum --import",
            )
        return latest

    def run(self, selection: Selection) -> SpectrumResult:
        runs = self._runs(selection)
        events: list[LedgerEvent] = []
        if runs:
            for run in runs:
                events.extend(self._ledger.events(EventQuery(run_id=run.id)))
        else:
            events.extend(
                self._ledger.events(EventQuery(session_id=selection.session, since=selection.since))
            )
        changed: frozenset[str] = frozenset()
        resolved = False
        snapshotted = False
        for run in runs:
            start = {item.path: item.sha256 for item in self._ledger.snapshots(run.id, "start")}
            end = {item.path: item.sha256 for item in self._ledger.snapshots(run.id, "end")}
            if start or end:
                snapshotted = True
                changed = changed | changed_paths(start, end)
            resolved = resolved or self._resolved(run.id)
        title = self._label(selection, runs)
        task_type = runs[0].task_type if len(runs) == 1 else ""
        if len(runs) == 1 and self._metadata is not None:
            task_type = str((self._metadata(runs[0].id) or {}).get("task_type") or task_type)
        report = analyze(
            english(title),
            events,
            changed,
            resolved,
            snapshotted,
            self._prices,
            title,
            task_type,
        )
        index_events, index_runs = self._metric_inputs(events, runs)
        report = replace(
            report,
            index=run_index_metrics(index_events, index_runs, self._metadata),
            anatomy=analyze_anatomy(resolve_agents(index_events)),
        )
        if self._reports is not None:
            texts = {run.id: self._reports(run.id) for run in index_runs}
            identities = set(texts)
            primary = [run for run in index_runs if run.parent_id not in identities]
            known = bool(primary) and all(texts[run.id] is not None for run in primary)
            text = "\n".join(texts[run.id] or "" for run in primary) if known else None
            edited = set(changed)
            fallback = False
            for run in index_runs:
                meta = self._metadata(run.id) or {} if self._metadata else {}
                paths = meta.get("changed_files")
                if isinstance(paths, list | tuple):
                    edited.update(path for path in paths if isinstance(path, str))
                single, shape_known = stored_shape(
                    meta, run, str(meta.get("task_type") or run.task_type)
                )
                if run in primary and texts[run.id] is None and (not single or not shape_known):
                    fallback = True
            efficiency = read_efficiency(
                index_events,
                text,
                tuple(edited),
                task_type,
                project_roots=tuple(root for run in index_runs for root in self._roots(run))
                if self._roots
                else (),
            )
            report = replace(
                report,
                read_efficiency=efficiency,
                utilization=report.utilization
                if fallback
                else file_utilization(efficiency, report.totals.total),
            )
        audits = self._ledger.route_audits(runs[0].id) if len(runs) == 1 else ()
        return SpectrumResult(report, runs, tuple(events), audits)

    def _metric_inputs(
        self, events: list[LedgerEvent], runs: tuple[Run, ...]
    ) -> tuple[list[LedgerEvent], tuple[Run, ...]]:
        index_runs = {run.id: run for run in runs}
        for event in events:
            if event.run_id and event.run_id not in index_runs:
                run = self._ledger.get_run(event.run_id)
                if run is not None:
                    index_runs[run.id] = run
        index_events = list(events)
        for run in tuple(index_runs.values()):
            if run.kind != CROSS_KIND or run.parent_id:
                continue
            for child in self._ledger.runs(kind=CROSS_KIND, since=run.started_at):
                if child.parent_id == run.id and child.id not in index_runs:
                    index_runs[child.id] = child
                    index_events.extend(self._ledger.events(EventQuery(run_id=child.id)))
        return index_events, tuple(index_runs.values())

    def _resolved(self, run_id: str) -> bool:
        test_runs = self._ledger.test_runs(run_id=run_id)
        if len(test_runs) < 2:
            return False
        latest, earlier = test_runs[0], test_runs[1:]
        latest_ids = {item.signature_id for item in self._ledger.signatures(latest.id)}
        for record in earlier:
            before = {item.signature_id for item in self._ledger.signatures(record.id)}
            if before - latest_ids:
                return True
        return latest.status == GatewayStatus.GREEN.value and any(
            record.status != GatewayStatus.GREEN.value for record in earlier
        )

    def _label(self, selection: Selection, runs: tuple[Run, ...]) -> Message:
        if selection.run and HU_PATTERN.match(selection.run):
            return msg("selection.story", story=selection.run.upper(), count=len(runs))
        if len(runs) == 1:
            run = runs[0]
            if run.hu_ref:
                return msg("selection.run_story", run=run.id, kind=run.kind, story=run.hu_ref)
            return msg("selection.run", run=run.id, kind=run.kind)
        if selection.session:
            return msg("selection.session", session=selection.session)
        if selection.since in {"", ALL_SESSIONS}:
            return msg("selection.all")
        return msg("selection.since", since=selection.since)
