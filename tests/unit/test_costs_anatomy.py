from __future__ import annotations

from dataclasses import replace

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.costs import CostsQuery
from cuanta.domain.anatomy import Phase
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.real_costs import attempts
from cuanta.ports.ledger import EventQuery
from tests.unit.test_real_costs import SINCE, run


class CountingLedger(MemoryLedger):
    def __init__(self) -> None:
        super().__init__()
        self.queries: list[EventQuery] = []

    def events(self, query: EventQuery) -> tuple[LedgerEvent, ...]:
        self.queries.append(query)
        return super().events(query)


def event(
    run_id: str,
    second: int,
    cost: float | None = None,
    kind: str = "api_request",
    **changes: str | int,
) -> LedgerEvent:
    return LedgerEvent(
        run_id=run_id,
        source="claude_code",
        session_id=run_id,
        agent="main",
        kind=kind,
        ts=f"2026-09-20T10:00:{second:02d}Z",
        cost_usd=cost,
        input_tokens=int(changes.get("input_tokens", 100)),
        output_tokens=int(changes.get("output_tokens", 20)),
        tool_name=str(changes.get("tool_name", "")),
    )


def query(ledger: CountingLedger) -> CostsQuery:
    return CostsQuery(lambda: ledger, lambda: True, lambda: "2026-09-26T12:00:00Z")


def test_phase_medians_keep_billed_costs_and_zero_only_covered_absent_phases() -> None:
    ledger = CountingLedger()
    ledger.add_run(run("A", "bug", cost=1.0, outcome="accepted", turns=3))
    ledger.add_run(run("NO_EVENTS", "bug", cost=2.0))
    ledger.add_events(
        (
            event("A", 0, 0.1),
            event("A", 5, kind="tool_result", tool_name="Read"),
            event("A", 10, 0.2),
            event("A", 20, 0.3, input_tokens=50, output_tokens=400),
        )
    )
    report = query(ledger).report()
    fix = next(row for row in report.phase_medians if row.key == "fix")
    medians = {item.phase: item.median_cost_usd for item in fix.medians}
    assert (fix.runs, fix.covered, fix.missing) == (2, 1, 1)
    assert medians == {
        Phase.START: 0.1,
        Phase.EXPLORATION: 0.2,
        Phase.WRITING: 0.3,
        Phase.HANDOFF: 0.0,
    }
    assert all(item.samples == 1 for item in fix.medians)
    assert report.total.spend.known_usd == 3.0 and report.total.per_accepted == 3.0
    assert ledger.queries == [EventQuery(since=SINCE)]


@pytest.mark.parametrize("coverage", ["missing_cost", "incomplete_turns", "missing_events"])
def test_unknown_request_coverage_never_becomes_zero_phase_cost(coverage: str) -> None:
    ledger = CountingLedger()
    ledger.add_run(run("A", turns=2 if coverage == "incomplete_turns" else 0))
    if coverage != "missing_events":
        ledger.add_events((event("A", 0, None if coverage == "missing_cost" else 0.1),))
    row = next(row for row in query(ledger).report().phase_medians if row.key == "feature")
    assert row.covered == 0 and row.missing == 1
    assert all(item.median_cost_usd is None and item.samples == 0 for item in row.medians)


def test_pipeline_child_events_count_once_and_preserve_event_prices() -> None:
    ledger = CountingLedger()
    parent = run("P", kind="cross", cost=0.5, turns=1)
    child = run("C", kind="cross", parent_id="P", cost=0.25, turns=1)
    ledger.add_run(parent)
    ledger.add_run(child)
    ledger.add_events((event("P", 0, 0.15), event("C", 0, 0.2)))
    report = query(ledger).report()
    row = next(row for row in report.phase_medians if row.key == "feature")
    assert row.runs == row.covered == 1
    start = next(item for item in row.medians if item.phase == Phase.START)
    assert start.median_cost_usd == pytest.approx(0.35)
    assert report.total.spend.known_usd == 0.75
    assert len(ledger.queries) == 1
    assert attempts((parent, child))[0].run_ids == ("P", "C")


def test_primary_usage_priority_drops_fallback_without_losing_other_role_fallback() -> None:
    ledger = CountingLedger()
    ledger.add_run(run("P", kind="cross", cost=0.5))
    ledger.add_run(run("C", kind="cross", parent_id="P", cost=0.25))
    ledger.add_events(
        (
            event("P", 0, 0.15),
            event("P", 1, 10.0, kind="result_usage"),
            event("C", 0, 0.2, kind="result_usage"),
        )
    )
    row = next(row for row in query(ledger).report().phase_medians if row.key == "feature")
    start = next(item for item in row.medians if item.phase == Phase.START)
    assert row.covered == 1 and start.median_cost_usd == pytest.approx(0.35)


def test_missing_pipeline_role_requests_exclude_the_whole_attempt() -> None:
    ledger = CountingLedger()
    ledger.add_run(run("P", kind="cross", cost=0.5))
    ledger.add_run(run("C", kind="cross", parent_id="P", cost=0.25))
    ledger.add_events((event("P", 0, 0.15),))
    row = next(row for row in query(ledger).report().phase_medians if row.key == "feature")
    assert row.runs == row.missing == 1 and row.covered == 0


def test_primary_unknown_event_cost_cannot_use_fallback_as_a_substitute() -> None:
    ledger = CountingLedger()
    ledger.add_run(run("A"))
    ledger.add_events((event("A", 0, None), event("A", 1, 1.0, kind="result_usage")))
    row = next(row for row in query(ledger).report().phase_medians if row.key == "feature")
    assert row.covered == 0


def test_positive_turn_coverage_can_exclude_fallback_even_when_billed_cost_is_known() -> None:
    ledger = CountingLedger()
    ledger.add_run(run("A", turns=3))
    ledger.add_events((event("A", 0, 1.0, kind="result_usage"),))
    report = query(ledger).report()
    row = next(row for row in report.phase_medians if row.key == "feature")
    assert row.covered == 0 and report.total.spend.value == 1.0


def test_phase_medians_group_task_types_and_use_every_economic_attempt() -> None:
    ledger = CountingLedger()
    ledger.add_run(run("A", "bug", outcome="accepted"))
    ledger.add_run(run("B", "bug", outcome="rejected"))
    ledger.add_run(run("C", "feature"))
    ledger.add_events((event("A", 0, 0.1), event("B", 0, 0.3), event("C", 0, 0.8)))
    rows = {row.key: row for row in query(ledger).report().phase_medians}
    fix = next(item for item in rows["fix"].medians if item.phase == Phase.START)
    feature = next(item for item in rows["feature"].medians if item.phase == Phase.START)
    assert fix.median_cost_usd == pytest.approx(0.2) and feature.median_cost_usd == 0.8
    assert rows["fix"].runs == rows["fix"].covered == 2


def test_pipeline_attempt_event_ids_are_unique_without_changing_economic_total() -> None:
    parent = run("P", kind="cross", cost=0.5)
    child = run("C", kind="cross", parent_id="P", cost=0.25)
    item = attempts((parent, child, replace(child)))[0]
    assert item.run_ids == ("P", "C")
    assert item.cost == 1.0
