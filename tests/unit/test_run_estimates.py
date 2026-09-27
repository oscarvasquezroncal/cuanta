from __future__ import annotations

from datetime import date
from itertools import count
from pathlib import Path

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.prices import load_prices
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.cross_engine import CrossEnginePipeline
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.estimate import estimate, run_estimate
from cuanta.application.instinct import DecisionMaker
from cuanta.application.mandate import MandateService
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.results import ResultQuery
from cuanta.application.routing import RoutePlan
from cuanta.domain.detection import Stack
from cuanta.domain.estimates import RunEstimate
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import MandateRequest
from cuanta.ports.engine import Engine
from tests.fakes import FakeRunner, FakeStream
from tests.unit.test_engine_profiles import all_roles
from tests.unit.test_sandbox_runs import TEMPLATE, EditingEngine

FEATURE = MandateRequest(
    type="feature", what="Add a sitemap route", why="SEO", tests="builds", out_of_scope="x"
)
BUG = MandateRequest(type="bug", what="fix add", why="incorrect sum", out_of_scope="docs")
GUESS = RunEstimate("history", 0.2, 0.5, 3)


def _launcher(engine: Engine, ledger: MemoryLedger, prefix: str) -> EngineLauncher:
    ids = count(1)
    return EngineLauncher(
        engine,
        ledger,
        FixedClock(),
        lambda: f"{prefix}{next(ids)}",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
    )


def _flow(
    tmp_path: Path,
    ledger: MemoryLedger,
    calls: list[tuple[RoutePlan | None, str, str]],
) -> MandateFlow:
    root = tmp_path / "shop"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "MANDATE_TEMPLATE.md").write_text(TEMPLATE, encoding="utf-8")
    clock = FixedClock()
    decisions = DecisionMaker(HeuristicInstinct(), ledger, clock.now_iso)
    service = MandateService(LocalWorkspace(root), ledger, decisions, clock.now_iso)
    engine = EditingEngine(lambda _: None)

    def estimator(plan: RoutePlan | None, task_type: str, depth: str) -> RunEstimate:
        calls.append((plan, task_type, depth))
        return GUESS

    ids = count(1)
    return MandateFlow(
        service,
        lambda _: engine,
        lambda chosen: _launcher(chosen, ledger, "RUN"),
        Stack,
        lambda _: ({}, None),
        str(root),
        "claude",
        0.0,
        new_run_id=lambda: f"MANDATE{next(ids)}",
        estimator=estimator,
    )


def test_a_mandate_stores_the_estimate_and_cap_it_ran_with(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    calls: list[tuple[RoutePlan | None, str, str]] = []
    flow = _flow(tmp_path, ledger, calls)
    prepared = flow.prepare(FEATURE, 0, MandateOptions(simple=True, budget_usd=1.5, depth="deep"))
    report = flow.run(prepared, RecordingSink(), None, False)
    stored = ledger.get_run(report.run.id)
    assert stored is not None
    assert (
        stored.estimate_low,
        stored.estimate_high,
        stored.estimate_source,
        stored.estimate_samples,
        stored.cap_usd,
    ) == (0.2, 0.5, "history", 3, 1.5)
    assert calls == [(None, "feature", "deep")]


def test_the_team_step_estimate_wins_and_previews_skip_the_estimator(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    calls: list[tuple[RoutePlan | None, str, str]] = []
    flow = _flow(tmp_path, ledger, calls)
    shown = RunEstimate("plan", 0.1, 0.1, 0)
    prepared = flow.prepare(FEATURE, 0, MandateOptions(simple=True, estimate=shown))
    assert prepared.spec.estimate == shown
    preview = flow.prepare(FEATURE, 0, MandateOptions(simple=True), preview=True)
    assert preview.spec.estimate is None
    assert calls == []


def test_cross_runs_type_every_role_and_estimate_the_root_only(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    runner = FakeRunner(
        streams={"claude": FakeStream(['{"type":"result","subtype":"success","is_error":false}'])}
    )
    engine_launcher = _launcher(ClaudeCodeEngine(runner), ledger, "STEP")
    calls: list[tuple[RoutePlan | None, str, str]] = []

    def estimator(plan: RoutePlan | None, task_type: str, depth: str) -> RunEstimate:
        calls.append((plan, task_type, depth))
        return GUESS

    pipeline = CrossEnginePipeline(
        lambda _: engine_launcher,
        tuple,
        FileCapsuleStore(tmp_path),
        str(tmp_path),
        2.0,
        estimator=estimator,
        depth="quick",
    )
    report = pipeline.run(BUG, all_roles("claude"), RecordingSink())
    runs = sorted(ledger.runs(), key=lambda run: run.id)
    root, *roles = runs
    assert root.id == report.steps[0].run_id and root.parent_id == ""
    assert (root.estimate_source, root.estimate_low, root.cap_usd) == ("history", 0.2, 2.0)
    assert all(run.task_type == "bug" and run.depth == "quick" for run in runs)
    assert all(role.estimate_source == "" and role.parent_id == root.id for role in roles)
    assert len(calls) == 1 and calls[0][1:] == ("bug", "quick")


def test_run_estimate_matches_the_team_step_bounds() -> None:
    history = tuple(
        Run(f"R{index}", "mandate", cost_usd=cost, task_type="feature", status="ok")
        for index, cost in enumerate((0.2, 0.4, 0.6, 0.8))
    )
    plan = all_roles("claude")
    shown = estimate(plan, history, load_prices(), "feature", "", 1.0)
    assert run_estimate(plan, history, load_prices(), "feature", "") == shown.bounds
    assert shown.bounds.source == "history" and shown.bounds.samples == 4
    assert run_estimate(None, (), load_prices(), "feature", "").source == "none"


def test_a_cross_result_shows_the_pipeline_total_and_its_error(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(
        Run(
            "ROOT",
            "cross",
            "claude",
            started_at="2026-09-20T10:00:00Z",
            status="ok",
            cost_usd=0.5,
            estimate_low=0.4,
            estimate_high=0.6,
            estimate_source="history",
        )
    )
    ledger.add_run(
        Run(
            "ROLE",
            "cross",
            "codex",
            started_at="2026-09-20T10:01:00Z",
            status="ok",
            cost_usd=0.4,
            parent_id="ROOT",
            cost_source="estimated",
        )
    )
    query = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today)
    view = query.load("ROOT")
    assert view is not None
    assert view.actual_usd == 0.9 and view.actual_estimated
    assert view.estimate_error is not None and round(view.estimate_error, 2) == 0.5
    assert view.decidable
    role = query.load("ROLE")
    assert role is not None and not role.decidable


def test_legacy_zero_costs_do_not_become_history() -> None:
    legacy = Run("L", "mandate", cost_usd=0.0, task_type="feature", status="ok")
    assert run_estimate(None, (legacy,), load_prices(), "feature", "").source == "none"


def test_a_run_stuck_running_for_a_day_can_be_decided_on_the_result_screen(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("OLD", "mandate", status="running", started_at="2026-09-10T00:00:00Z"))
    fresh = ResultQuery(
        LocalWorkspace(tmp_path), ledger, date.today, lambda: "2026-09-10T02:00:00Z"
    )
    later = ResultQuery(
        LocalWorkspace(tmp_path), ledger, date.today, lambda: "2026-09-26T00:00:00Z"
    )
    early = fresh.load("OLD")
    late = later.load("OLD")
    assert early is not None and not early.decidable
    assert late is not None and late.decidable
