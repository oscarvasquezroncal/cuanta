from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.costs import CostsQuery
from cuanta.application.outcomes import RunOutcomes
from cuanta.application.trials import TrialStore
from cuanta.domain.errors import DomainFailure
from cuanta.domain.ledger import Run
from cuanta.domain.outcomes import ACCEPTED, REJECTED


class Setup:
    def __init__(self, tmp_path: Path) -> None:
        self.ledger = MemoryLedger()
        self.clock = FixedClock()
        self.store = TrialStore(LocalWorkspace(tmp_path), self.ledger, self.clock.now_iso)
        self.outcomes = RunOutcomes(self.ledger, self.store, self.clock.now_iso)


def _setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_accept_and_reject_record_the_outcome_once(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    setup.ledger.add_run(Run(id="A", kind="mandate", status="failed"))
    setup.ledger.add_run(Run(id="B", kind="mandate", status="ok"))
    accepted = setup.outcomes.accept("A")
    assert (accepted.run_id, accepted.outcome) == ("A", ACCEPTED)
    rejected = setup.outcomes.reject("B", "missed the point")
    assert rejected.outcome == REJECTED
    stored = setup.ledger.get_run("B")
    assert stored is not None and stored.outcome_reason == "missed the point"
    with pytest.raises(DomainFailure, match="already accepted"):
        setup.outcomes.reject("A")


def test_a_cross_role_resolves_to_its_pipeline(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    setup.ledger.add_run(Run(id="ROOT", kind="cross", status="ok"))
    setup.ledger.add_run(Run(id="ROLE", kind="cross", status="ok", parent_id="ROOT"))
    change = setup.outcomes.accept("ROLE")
    assert change.run_id == "ROOT"
    root = setup.ledger.get_run("ROOT")
    role = setup.ledger.get_run("ROLE")
    assert root is not None and root.outcome == ACCEPTED
    assert role is not None and role.outcome == ""


def test_runs_that_are_not_attempts_or_still_running_are_refused(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    setup.ledger.add_run(Run(id="L", kind="loop", status="ok"))
    setup.ledger.add_run(Run(id="R", kind="mandate", status="running"))
    with pytest.raises(DomainFailure, match="only mandates"):
        setup.outcomes.accept("L")
    with pytest.raises(DomainFailure, match="still running"):
        setup.outcomes.reject("R")
    with pytest.raises(DomainFailure, match="no run"):
        setup.outcomes.accept("MISSING")


def test_rejecting_a_sandbox_run_goes_through_discard(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    setup.ledger.add_run(Run(id="S", kind="mandate", status="ok", mode="sandbox"))
    setup.ledger.add_run(Run(id="T", kind="mandate", status="ok", mode="sandbox"))
    setup.outcomes.reject("S", "wrong file")
    stored = setup.ledger.get_run("S")
    assert stored is not None and (stored.outcome, stored.outcome_reason) == (
        REJECTED,
        "wrong file",
    )
    (tmp_path / ".cuanta" / "trials" / "T").mkdir(parents=True)
    (tmp_path / ".cuanta" / "trials" / "T" / "applied.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DomainFailure, match="already applied"):
        setup.outcomes.reject("T")
    accepted = setup.outcomes.accept("T")
    assert accepted.outcome == ACCEPTED


def test_costs_query_reads_the_window_and_skips_projects_without_a_ledger(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(
        Run(id="OLD", kind="mandate", status="ok", started_at="2026-07-01T00:00:00Z", cost_usd=5)
    )
    ledger.add_run(
        Run(
            id="NEW",
            kind="mandate",
            status="ok",
            started_at="2026-09-20T00:00:00Z",
            cost_usd=1.0,
            task_type="bug",
            outcome=ACCEPTED,
        )
    )
    query = CostsQuery(lambda: ledger, lambda: True, lambda: "2026-09-26T00:00:00Z")
    report = query.report()
    assert report.since == "2026-08-27T00:00:00Z"
    assert report.total.runs == 1
    fix = report.row("fix")
    assert fix is not None and fix.per_accepted == 1.0
    assert query.report("2026-06-01T00:00:00Z").total.runs == 2
    missing = CostsQuery(lambda: ledger, lambda: False, lambda: "2026-09-26T00:00:00Z")
    assert missing.report().empty


def test_a_pipeline_with_a_running_role_cannot_be_decided(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    setup.ledger.add_run(
        Run(id="ROOT", kind="cross", status="ok", started_at="2025-12-31T23:00:00Z")
    )
    setup.ledger.add_run(
        Run(
            id="ROLE",
            kind="cross",
            status="running",
            parent_id="ROOT",
            started_at="2025-12-31T23:10:00Z",
        )
    )
    with pytest.raises(DomainFailure, match="still running"):
        setup.outcomes.accept("ROOT")


def test_a_run_stuck_running_for_a_day_can_be_rejected(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    setup.ledger.add_run(
        Run(id="OLD", kind="mandate", status="running", started_at="2025-12-30T00:00:00Z")
    )
    setup.ledger.add_run(
        Run(id="NEW", kind="mandate", status="running", started_at="2025-12-31T23:00:00Z")
    )
    assert setup.outcomes.reject("OLD", "process died").outcome == REJECTED
    with pytest.raises(DomainFailure, match="still running"):
        setup.outcomes.reject("NEW")


def test_discarding_a_cross_role_points_at_its_pipeline(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    setup.ledger.add_run(Run(id="ROOT", kind="cross", status="ok", mode="sandbox"))
    setup.ledger.add_run(
        Run(id="ROLE", kind="cross", status="ok", mode="sandbox", parent_id="ROOT")
    )
    with pytest.raises(DomainFailure, match="role of the cross-engine run ROOT"):
        setup.store.discard("ROLE")
    role = setup.ledger.get_run("ROLE")
    assert role is not None and role.outcome == ""


def test_home_reads_real_costs_for_the_window(tmp_path: Path) -> None:
    from typing import cast

    from cuanta.application.cache_state import PrefixQuery
    from cuanta.application.doctor import Doctor, DoctorReport
    from cuanta.application.home import HomeQuery
    from cuanta.domain.cache import UNKNOWN_PREFIX
    from tests.tui.fakes import CHECKS, DETECTION

    class StubDoctor:
        def run(self) -> DoctorReport:
            return DoctorReport(DETECTION, CHECKS)

    class StubPrefix:
        def run(self, engine: str) -> object:
            return UNKNOWN_PREFIX

    ledger = MemoryLedger()
    ledger.add_run(
        Run(id="IN", kind="mandate", status="ok", started_at="2025-12-20T00:00:00Z", cost_usd=0.4)
    )
    ledger.add_run(
        Run(id="OUT", kind="mandate", status="ok", started_at="2025-10-01T00:00:00Z", cost_usd=9)
    )
    query = HomeQuery(
        cast(Doctor, StubDoctor()),
        lambda: ledger,
        lambda: True,
        lambda: "0.4.0",
        lambda: __import__("datetime").date(2026, 1, 1),
        cast(PrefixQuery, StubPrefix()),
        "claude",
        lambda: "2026-01-01T00:00:00Z",
    )
    costs = query.run().costs
    assert costs is not None
    assert costs.since == "2025-12-02T00:00:00Z"
    assert costs.total.runs == 1 and costs.total.spend.known_usd == 0.4
