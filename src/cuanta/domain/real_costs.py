from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from cuanta.domain.anatomy import AnatomyReport, Phase
from cuanta.domain.costs import ESTIMATED, CostTotal, median, sum_costs, total_costs
from cuanta.domain.estimates import estimate_error
from cuanta.domain.ledger import Run
from cuanta.domain.outcomes import ACCEPTED, CROSS_KIND, REJECTED, is_attempt, pipeline_running

if TYPE_CHECKING:
    from cuanta.domain.time_costs import TimeMedianRow

WINDOW_DAYS = 30
INVESTIGATION_TYPE = "investigation"
FIX_TYPE = "fix"
FEATURE_TYPE = "feature"
TYPE_ORDER = (INVESTIGATION_TYPE, FIX_TYPE, FEATURE_TYPE, "refactor", "docs")
TYPE_ALIASES = {"bug": FIX_TYPE}
UNTYPED = "untyped"
UNKNOWN_SOURCE = "unknown"
CROSS_MIX = "cross-engine"
MIX_ORDER = ("claude", "codex", CROSS_MIX)
ALL = "all"

COMPLETION_ORDER = ("complete", "complete_skipped", "partial", "failed")


@dataclass(frozen=True, slots=True)
class Attempt:
    run: Run
    cost: float | None
    estimated: bool
    seconds: float | None
    type: str
    mix: str
    run_ids: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.run.outcome == ACCEPTED

    @property
    def rejected(self) -> bool:
        return self.run.outcome == REJECTED

    @property
    def error(self) -> float | None:
        return estimate_error(self.run.estimate_low, self.run.estimate_high, self.cost)


@dataclass(frozen=True, slots=True)
class CostRow:
    key: str
    runs: int
    accepted: int
    rejected: int
    spend: CostTotal
    median_cost: float | None
    median_seconds: float | None
    per_accepted: float | None
    median_error: float | None
    errors: int
    in_range: int = 0

    @property
    def error_in_range(self) -> bool:
        return self.median_error == 0 and self.in_range * 2 > self.errors

    @property
    def estimated(self) -> bool:
        return self.spend.estimated > 0

    @property
    def pending(self) -> int:
        return self.runs - self.accepted - self.rejected

    @property
    def lower_bound(self) -> bool:
        return self.spend.lower_bound


@dataclass(frozen=True, slots=True)
class PhaseMedian:
    phase: Phase
    median_cost_usd: float | None
    samples: int


@dataclass(frozen=True, slots=True)
class PhaseCostRow:
    key: str
    runs: int
    covered: int
    medians: tuple[PhaseMedian, ...]

    @property
    def missing(self) -> int:
        return self.runs - self.covered


@dataclass(frozen=True, slots=True)
class CostReport:
    since: str
    by_type: tuple[CostRow, ...]
    by_mix: tuple[CostRow, ...]
    total: CostRow
    phase_medians: tuple[PhaseCostRow, ...] = ()
    by_completion: tuple[CostRow, ...] = ()
    time_medians: tuple[TimeMedianRow, ...] = ()

    @property
    def empty(self) -> bool:
        return self.total.runs == 0

    def row(self, key: str) -> CostRow | None:
        return next((row for row in self.by_type if row.key == key), None)


def seconds_between(start: str, end: str) -> float | None:
    if not start or not end:
        return None
    try:
        seconds = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def costs_since(now_iso: str, days: int = WINDOW_DAYS) -> str:
    try:
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        return ""
    moment = now if now.tzinfo is None else now.astimezone(UTC)
    return f"{(moment - timedelta(days=days)).date().isoformat()}T00:00:00Z"


def since_date(text: str) -> str | None:
    try:
        day = date.fromisoformat(text.strip())
    except ValueError:
        return None
    return f"{day.isoformat()}T00:00:00Z"


def type_key(task_type: str) -> str:
    return TYPE_ALIASES.get(task_type, task_type) or UNTYPED


def mix_key(run: Run, roles: Sequence[Run] = ()) -> str:
    if run.kind != CROSS_KIND:
        return run.engine or UNTYPED
    engines = {role.engine for role in (run, *roles) if role.engine}
    return engines.pop() if len(engines) == 1 else CROSS_MIX


def known_cost(run: Run) -> float | None:
    if run.cost_usd == 0 and run.cost_source == UNKNOWN_SOURCE:
        return None
    return run.cost_usd


def attempts(runs: Iterable[Run], now_iso: str = "") -> tuple[Attempt, ...]:
    listed = tuple(runs)
    children: dict[str, list[Run]] = {}
    for run in listed:
        if run.kind == CROSS_KIND and run.parent_id:
            children.setdefault(run.parent_id, []).append(run)
    found: list[Attempt] = []
    for run in listed:
        if not is_attempt(run):
            continue
        roles = (run, *children.get(run.id, ())) if run.kind == CROSS_KIND else (run,)
        if pipeline_running(run, roles[1:], now_iso):
            continue
        ends = [role.ended_at for role in roles if role.ended_at]
        found.append(
            Attempt(
                run,
                sum_costs(known_cost(role) for role in roles),
                any(role.cost_source == ESTIMATED for role in roles),
                seconds_between(run.started_at, max(ends)) if ends else None,
                type_key(run.task_type),
                mix_key(run, roles),
                tuple(dict.fromkeys(role.id for role in roles)),
            )
        )
    return tuple(found)


def cost_row(key: str, items: Sequence[Attempt]) -> CostRow:
    spend = total_costs((item.cost, item.estimated) for item in items)
    accepted = sum(item.accepted for item in items)
    errors = [error for item in items if (error := item.error) is not None]
    return CostRow(
        key=key,
        runs=len(items),
        accepted=accepted,
        rejected=sum(item.rejected for item in items),
        spend=spend,
        median_cost=median(item.cost for item in items if item.cost is not None),
        median_seconds=median(item.seconds for item in items if item.seconds is not None),
        per_accepted=spend.known_usd / accepted if accepted and spend.known else None,
        median_error=median(errors),
        errors=len(errors),
        in_range=sum(error == 0 for error in errors),
    )


def _rows(items: Sequence[Attempt], keys: Sequence[str], attribute: str) -> tuple[CostRow, ...]:
    grouped: dict[str, list[Attempt]] = {key: [] for key in keys}
    for item in items:
        grouped.setdefault(getattr(item, attribute), []).append(item)
    return tuple(cost_row(key, grouped[key]) for key in grouped)


def phase_cost_rows(
    items: Sequence[Attempt], reports: Mapping[str, AnatomyReport]
) -> tuple[PhaseCostRow, ...]:
    grouped: dict[str, list[Attempt]] = {key: [] for key in TYPE_ORDER}
    for item in items:
        grouped.setdefault(item.type, []).append(item)
    rows: list[PhaseCostRow] = []
    for key, group in grouped.items():
        covered = [
            report
            for item in group
            if (report := reports.get(item.run.id)) is not None
            and report.totals.requests > 0
            and report.totals.cost_usd is not None
        ]
        medians = tuple(
            PhaseMedian(
                phase,
                median(
                    summary.totals.cost_usd
                    for report in covered
                    for summary in report.phases
                    if summary.phase == phase and summary.totals.cost_usd is not None
                ),
                len(covered),
            )
            for phase in Phase
        )
        rows.append(PhaseCostRow(key, len(group), len(covered), medians))
    return tuple(rows)


def completion_rows(items: Sequence[Attempt], completion: Mapping[str, str]) -> tuple[CostRow, ...]:
    grouped: dict[str, list[Attempt]] = {}
    for item in items:
        state = completion.get(item.run.id, "")
        if state:
            grouped.setdefault(state, []).append(item)
    ordered = [key for key in COMPLETION_ORDER if key in grouped]
    ordered.extend(sorted(key for key in grouped if key not in COMPLETION_ORDER))
    return tuple(cost_row(key, grouped[key]) for key in ordered)


def report_attempts(
    items: Sequence[Attempt],
    since: str,
    phases: Mapping[str, AnatomyReport] | None = None,
    completion: Mapping[str, str] | None = None,
) -> CostReport:
    return CostReport(
        since=since,
        by_type=_rows(items, TYPE_ORDER, "type"),
        by_mix=_rows(items, MIX_ORDER, "mix"),
        total=cost_row(ALL, items),
        phase_medians=phase_cost_rows(items, phases) if phases is not None else (),
        by_completion=completion_rows(items, completion) if completion else (),
    )


def cost_report(runs: Iterable[Run], since: str, now_iso: str = "") -> CostReport:
    return report_attempts(attempts(runs, now_iso), since)
