from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.prices import load_prices
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.cross_engine import CrossEnginePipeline
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.export import export
from cuanta.application.instinct_view import week_spend
from cuanta.application.models import model_stats
from cuanta.application.results import ResultQuery, run_markdown
from cuanta.domain.costs import CostSource, sum_costs
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.ledger import Decision, LedgerEvent, Run
from cuanta.domain.pricing import estimate_cost
from cuanta.domain.spectrum import View, grouped, totals_of
from tests.unit.test_cross_engine import REQUEST, Recorder, plan
from tests.unit.test_launch_cost import SilentEngine


@pytest.mark.parametrize(
    ("values", "expected"),
    [((), 0.0), ((0.0, 1.0), 1.0), ((None,), None), ((2.0, None, 1.0), None)],
)
def test_cost_totals_require_every_measurement(
    values: tuple[float | None, ...], expected: float | None
) -> None:
    assert sum_costs(values) == expected


@pytest.mark.parametrize("cost", [None, 0.0, 0.4])
def test_exports_preserve_unknown_costs_and_real_zero(cost: float | None) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", cost_usd=cost, cost_source="unknown"))
    ledger.add_events([LedgerEvent(run_id="R", kind="result_usage", cost_usd=cost)])
    for table in ("runs", "events"):
        document = json.loads(export(ledger, "json", table))
        assert document[table][0]["cost_usd"] == cost
        row = next(csv.DictReader(io.StringIO(export(ledger, "csv", table))))
        assert row["cost_usd"] == ("n/a" if cost is None else str(cost))


def test_free_reported_usage_is_not_repriced_and_missing_usage_stays_unknown() -> None:
    table = load_prices()
    free = LedgerEvent(model="gpt-5.5", input_tokens=1_000_000, cost_usd=0.0)
    assert estimate_cost([free], table).value == 0.0
    assert estimate_cost([free], table).kind == "reported"
    absent = replace(free, input_tokens=0, cost_usd=None)
    assert estimate_cost([absent], table).value is None
    assert estimate_cost([free, absent], table).value is None
    known = replace(free, cost_usd=None)
    assert estimate_cost([known], table).kind == "estimated"
    assert estimate_cost([known, replace(known, model="private")], table).value is None


def launcher_for(engine: SilentEngine, ledger: MemoryLedger) -> EngineLauncher:
    return EngineLauncher(
        engine,
        ledger,
        FixedClock(),
        lambda: "R",
        lambda size: b"\x01" * size,
        "sample",
        4318,
        None,
        load_prices(),
    )


@pytest.mark.parametrize(
    ("cost", "model", "source"),
    [(0.0, "gpt-5.5", "reported"), (None, "gpt-5.5", "estimated"), (None, "private", "unknown")],
)
def test_launch_persists_cost_provenance(
    cost: float | None, model: str, source: CostSource
) -> None:
    ledger = MemoryLedger()
    launch = launcher_for(SilentEngine(cost, model), ledger).launch(
        LaunchSpec("mandate", "hi", ".", (), model=model),
        lambda event: None,
    )
    assert launch.run.cost_source == source
    assert ledger.get_run("R") == launch.run
    assert (launch.run.cost_usd is None) == (source == "unknown")


def test_no_usage_is_unknown_and_cap_check_publishes_only_normalized_result() -> None:
    ledger = MemoryLedger()
    empty = RunResult(True, "success", None, 0, "s")
    launch = launcher_for(SilentEngine(None, "", empty), ledger).launch(
        LaunchSpec("mandate", "hi", ".", ()),
        lambda event: None,
    )
    assert launch.run.cost_usd is None
    assert launch.run.cost_source == "unknown"
    events: list[EngineEvent] = []
    capped = launcher_for(SilentEngine(None, "gpt-5.5"), ledger).launch(
        LaunchSpec("mandate", "hi", ".", (), max_budget_usd=0.1),
        events.append,
    )
    assert not capped.outcome.ok
    assert capped.run.end_reason == "error_max_budget_usd"
    assert capped.run.status == "failed"
    assert capped.run.cost_source == "estimated"
    results = [event for event in events if isinstance(event, RunResult)]
    assert results == [capped.outcome.result]
    assert results[0].text == "done"
    assert results[0].models[0].input_tokens == 1_000_000


def test_unknown_cost_poisons_groups_and_model_and_decision_totals() -> None:
    events = [
        LedgerEvent(model="m", input_tokens=10, cost_usd=0.2),
        LedgerEvent(model="m", input_tokens=10),
    ]
    assert totals_of(events).cost_usd is None
    assert grouped(View.MODEL, events, ())[0].cost_usd is None
    rows = model_stats(
        [
            Run("a", "mandate", engine="codex", model="m", status="ok", cost_usd=0.2),
            Run("b", "mandate", engine="codex", model="m", status="failed"),
        ],
        (),
    )
    assert rows[0].cost_usd is None and rows[0].average_cost is None
    decisions = tuple(
        Decision("r", "llm", "choose", "q", "", "a", 1.0, 0, cost_usd=cost) for cost in (0.0, None)
    )
    assert week_spend(decisions, "llm", "") == (None, 2)


@pytest.mark.parametrize(("budget", "count"), [(2.0, 1), (0.0, 3)])
def test_cross_engine_unknown_cost_stops_only_budgeted_next_role(
    tmp_path: Path, budget: float, count: int
) -> None:
    calls: list[str] = []
    ledger = MemoryLedger()

    class Unpriced(SilentEngine):
        def run(
            self, request: EngineRequest, on_event: Callable[[EngineEvent], None]
        ) -> EngineOutcome:
            calls.append(request.model)
            return super().run(request, on_event)

    cross = CrossEnginePipeline(
        lambda name: launcher_for(Unpriced(None, "private", name=name), ledger),
        lambda: (),
        FileCapsuleStore(tmp_path),
        str(tmp_path),
        budget,
    )
    report = cross.run(REQUEST, plan(), Recorder())
    assert len(calls) == len(report.steps) == count
    assert report.spent_usd is None
    assert all(step.cost_source == "unknown" for step in report.steps)
    if budget:
        assert report.stopped is not None and report.stopped.key == "cross.cost_unknown"
    else:
        assert report.ok


def test_cross_engine_estimate_consumes_budget_and_preserves_label(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    cross = CrossEnginePipeline(
        lambda name: launcher_for(SilentEngine(None, "gpt-5.5", name=name), ledger),
        lambda: (),
        FileCapsuleStore(tmp_path),
        str(tmp_path),
        0.1,
    )
    report = cross.run(REQUEST, plan(), Recorder())
    assert len(report.steps) == 1 and not report.ok
    assert report.spent_usd is not None and report.spent_usd > 0.1
    assert report.steps[0].cost_source == "estimated"
    assert report.stopped is not None and report.stopped.key == "cross.budget"


def test_result_markdown_labels_estimates_and_unknown(tmp_path: Path) -> None:
    from datetime import date

    ledger = MemoryLedger()
    query = ResultQuery(LocalWorkspace(tmp_path), ledger, lambda: date(2026, 9, 26))
    cases: tuple[tuple[float | None, CostSource, str], ...] = (
        (None, "unknown", "cost n/a"),
        (0.2, "estimated", "$0.20 (estimated)"),
    )
    for cost, source, expected in cases:
        ledger.add_run(Run("R", "mandate", cost_usd=cost, cost_source=source))
        view = query.load("R")
        assert view is not None
        assert expected in run_markdown(view)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (True, None),
        ("invalid", None),
        (float("nan"), None),
        (float("inf"), None),
        (-1.0, None),
        (0, 0.0),
        ("0.0", 0.0),
        ("0.12", 0.12),
    ],
)
def test_telemetry_cost_requires_a_valid_measurement(value: object, expected: float | None) -> None:
    from cuanta.adapters.telemetry.mapping import as_cost

    assert as_cost(value) == expected


def test_a_completed_run_exactly_at_cap_is_within_the_post_run_limit() -> None:
    ledger = MemoryLedger()
    launched = launcher_for(SilentEngine(0.1, "gpt-5.5"), ledger).launch(
        LaunchSpec("mandate", "hi", ".", (), max_budget_usd=0.1),
        lambda event: None,
    )
    assert launched.outcome.ok
    assert launched.run.cost_usd == 0.1
