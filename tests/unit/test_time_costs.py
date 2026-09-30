from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.costs import CostsQuery
from cuanta.application.results import ResultQuery
from cuanta.application.run_reports import RunReports
from cuanta.application.spectrum import Selection, SpectrumQuery
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.real_costs import attempts
from cuanta.domain.time_costs import time_medians
from tests.unit.test_real_costs import run


def phase_event(run_id: str, seconds: float, phase: str = "verification") -> LedgerEvent:
    return LedgerEvent(
        run_id=run_id,
        kind="phase_timing",
        agent="writer",
        ts="2026-09-20T10:00:01Z",
        raw=json.dumps({"phase": phase, "duration_ms": seconds * 1000}),
    )


def test_time_medians_keep_unknowns_out_of_samples_and_distinguish_configuration() -> None:
    runs = (
        run("A", model="sonnet"),
        run("B", model="sonnet"),
        run("C", model="sonnet"),
        run("D", model="sonnet"),
        run("E", model="opus"),
    )
    metadata = {
        key: {"variant": "low", "implementation_profile": "fast"} for key in ("A", "B", "C", "E")
    }
    metadata["D"] = {"variant": "high", "implementation_profile": "balanced"}
    rows = time_medians(
        attempts(runs),
        (phase_event("A", 2.0), phase_event("B", 4.0), phase_event("D", 0.0)),
        metadata,
    )
    assert len(rows) == 3
    fast = next(row for row in rows if row.model == "sonnet" and row.variant == "low")
    assert (fast.task_type, fast.profile, fast.runs) == ("feature", "fast", 3)
    verify = next(phase for phase in fast.phases if phase.phase == "verification")
    assert verify.seconds == 3.0 and verify.samples == 2
    hooks = next(phase for phase in fast.phases if phase.phase == "hooks")
    assert hooks.seconds is None and hooks.samples == 0
    balanced = next(row for row in rows if row.profile == "balanced")
    zero = next(phase for phase in balanced.phases if phase.phase == "verification")
    assert zero.seconds == 0.0 and zero.samples == 1


def test_time_medians_include_child_events_once_and_leave_missing_labels_unknown() -> None:
    rows = time_medians(
        attempts((run("P", kind="cross"), run("C", kind="cross", parent_id="P"))),
        (
            phase_event("P", 2.0),
            phase_event("C", 3.0),
            LedgerEvent(run_id="C", model="sonnet", effort="medium"),
        ),
        {},
    )
    assert len(rows) == 1
    assert rows[0].runs == 1
    assert (rows[0].model, rows[0].variant, rows[0].profile) == ("sonnet", "medium", "")
    phase = next(phase for phase in rows[0].phases if phase.phase == "verification")
    assert phase.seconds == 5.0 and phase.samples == 1
    missing = time_medians(attempts((run("OLD", started_at="", ended_at=""),)), (), {})[0]
    assert (missing.model, missing.variant, missing.profile) == ("", "", "")
    assert missing.phases[0].phase == "wall" and missing.phases[0].seconds is None
    assert time_medians((), (), {}) == ()


def test_costs_result_and_spectrum_read_the_same_persisted_phase_measurement(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    workspace = LocalWorkspace(tmp_path)
    ledger.add_run(run("RUN", model="sonnet"))
    ledger.add_events((phase_event("RUN", 1.25),))
    RunReports(workspace).save_meta("RUN", {"effort": "low", "implementation_profile": "balanced"})
    query = CostsQuery(
        lambda: ledger,
        lambda: True,
        lambda: "2026-09-26T12:00:00Z",
        workspace=workspace,
    )
    row = query.report().time_medians[0]
    assert (row.variant, row.profile) == ("low", "balanced")
    assert next(phase.seconds for phase in row.phases if phase.phase == "verification") == 1.25
    result = ResultQuery(workspace, ledger, lambda: date(2026, 9, 26)).load("RUN")
    assert result is not None
    spectrum = SpectrumQuery(ledger).run(Selection(run="RUN"))
    assert result.time == spectrum.time
    assert (
        next(phase.seconds for phase in result.time.phases if phase.phase == "verification") == 1.25
    )
    ledger.add_run(run("OTHER"))
    aggregated = SpectrumQuery(ledger).run(Selection(since="0"))
    assert aggregated.time.wall_seconds is None
