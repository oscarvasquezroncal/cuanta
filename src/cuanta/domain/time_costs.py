from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.costs import median
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.real_costs import Attempt
from cuanta.domain.time_anatomy import PHASES, PhaseTime, TimeReport, analyze_time


@dataclass(frozen=True, slots=True)
class TimeMedianRow:
    task_type: str
    model: str
    variant: str
    profile: str
    runs: int
    phases: tuple[PhaseTime, ...]


def _label(values: Sequence[str]) -> str:
    return ", ".join(sorted({value for value in values if value}))


def time_medians(
    items: Sequence[Attempt],
    events: Sequence[LedgerEvent],
    metadata: Mapping[str, Mapping[str, object]],
) -> tuple[TimeMedianRow, ...]:
    by_run: dict[str, list[LedgerEvent]] = defaultdict(list)
    for event in events:
        by_run[event.run_id].append(event)
    grouped: dict[tuple[str, str, str, str], list[TimeReport]] = defaultdict(list)
    counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for item in items:
        selected = [event for run_id in item.run_ids for event in by_run[run_id]]
        meta = metadata.get(item.run.id, {})
        model = _label([event.model for event in selected]) or item.run.model
        variant = str(meta.get("variant") or meta.get("effort") or "")
        variant = variant or _label([event.effort for event in selected])
        profile = str(meta.get("implementation_profile") or meta.get("profile") or "")
        key = item.type, model, variant, profile
        counts[key] += 1
        if selected:
            grouped[key].append(analyze_time(item.run, selected))
    rows: list[TimeMedianRow] = []
    for key, count in sorted(counts.items()):
        reports = grouped[key]
        phase_names = tuple(
            dict.fromkeys(
                (*PHASES, *(phase.phase for report in reports for phase in report.phases))
            )
        )
        phases: list[PhaseTime] = []
        for name in ("wall", *phase_names):
            values = [
                value
                for report in reports
                if (
                    value := report.wall_seconds
                    if name == "wall"
                    else next(
                        (phase.seconds for phase in report.phases if phase.phase == name), None
                    )
                )
                is not None
            ]
            phases.append(PhaseTime(name, median(values), len(values)))
        rows.append(TimeMedianRow(*key, count, tuple(phases)))
    return tuple(rows)
