from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CompletionState, CrossEnginePipeline, CrossReport
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.routing import RoutePlan
from cuanta.application.steering import GovernorSetup
from cuanta.cli.commands.mandate import cross_blocks, cross_payload
from cuanta.cli.document import Line, Table
from cuanta.domain.engine import (
    GOVERNOR_STOP_SUBTYPE,
    WALL_LIMIT_SUBTYPE,
    EngineEvent,
    EngineOutcome,
    EngineRequest,
    ModelUsage,
    RunResult,
)
from cuanta.domain.governor import SALVAGED, SKIPPED, ReactionKind
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.progress import Status, StepFinished, StepStarted
from cuanta.domain.role_handoff import VerifyResult
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy
from cuanta.domain.stop_reason import stop_message
from tests.unit import test_codex_finish as codex
from tests.unit import test_steering as steering
from tests.unit.test_cross_handoffs import FIX, Act, Actor, Harness, analyst_json, seed

WALL_S = 1800.0
SENIOR = {Role.SENIOR: "codex"}
TEAM = {Role.SENIOR: "codex", Role.TESTER: "codex"}
EDITED = '{"summary": "edited", "status": "done"}'
FAILING = (VerifyResult("npm run build", 1, 1.0, errors=("src/layout.ts:1 error TS1: broken",)),)
PASSING = (VerifyResult("npm run build", 0, 1.0),)
WALL_LINE = "stopped by the time limit of 30 min"


@dataclass
class Ticker:
    now: float = 0.0
    step: float = 1.0

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


@dataclass
class Overdue(Harness):
    ticker: Ticker = field(default_factory=Ticker)

    def verifier(
        self, commands: Sequence[str], stopped: Callable[[], bool]
    ) -> tuple[VerifyResult, ...]:
        self.ticker.now = self.wall_s + 1.0
        return super().verifier(commands, stopped)


def senior_edit(
    cost: float = 0.2, subtype: str = "success", partial: bool = False
) -> dict[str, list[Act]]:
    return {"senior": [Act(EDITED, cost, {"src/layout.ts": "x\n"}, subtype, partial=partial)]}


def total_line(report: CrossReport, budget: float) -> str:
    blocks = cross_blocks(report, budget, "team")
    line = blocks[1]
    assert isinstance(line, Line)
    return line.text


def cost_cells(report: CrossReport) -> list[str]:
    table = cross_blocks(report, 0.0, "team")[0]
    assert isinstance(table, Table)
    return [row[-1] for row in table.rows if not row[0].startswith("verify")]


def test_a_repair_due_after_the_team_deadline_stops_at_the_wall_before_launching(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    ticker = Ticker()
    harness = Overdue(
        tmp_path,
        senior_edit(),
        budget=0.0,
        verify_results=[FAILING],
        wall_s=WALL_S,
        monotonic=ticker,
        ticker=ticker,
    )
    report, _ = harness.run(SENIOR, FIX)
    assert len(harness.prompt_of("senior")) == 1
    assert [step.repair for step in report.steps] == [False]
    assert report.state is CompletionState.PARTIAL
    assert report.stopped is not None and english(report.stopped) == WALL_LINE
    assert report.handoffs[-1].role == Role.SENIOR.value
    run = harness.ledger.get_run(report.steps[0].run_id)
    assert run is not None and run.max_wall_s == WALL_S


def test_a_role_whose_launch_comes_after_the_deadline_is_never_started(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path, senior_edit(), budget=0.0, wall_s=WALL_S, monotonic=Ticker(step=1_000.0)
    )
    report, _ = harness.run(SENIOR, FIX)
    assert harness.prompt_of("senior") == []
    assert report.steps == () and report.state is CompletionState.FAILED
    assert report.stopped is not None and english(report.stopped) == WALL_LINE


@dataclass
class Scripted:
    values: list[float]

    def __call__(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_a_deadline_that_passes_right_before_the_launch_never_starts_the_role(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    clock = Scripted([0.0, 0.0, 0.0, WALL_S])
    harness = Harness(tmp_path, senior_edit(), budget=0.0, wall_s=WALL_S, monotonic=clock)
    report, _ = harness.run(SENIOR, FIX)
    assert harness.prompt_of("senior") == []
    assert report.steps == () and report.state is CompletionState.FAILED
    assert report.stopped is not None and english(report.stopped) == WALL_LINE


@pytest.mark.parametrize("calls", [[0.0, 0.0, WALL_S], [0.0, 0.0, 0.0, WALL_S]])
def test_a_role_stopped_by_the_wall_before_its_launch_finishes_its_progress_step(
    tmp_path: Path, calls: list[float]
) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path, senior_edit(), budget=0.0, wall_s=WALL_S, monotonic=Scripted(list(calls))
    )
    report, recorder = harness.run(SENIOR, FIX)
    started = [event.key for event in recorder.events if isinstance(event, StepStarted)]
    finished = [event for event in recorder.events if isinstance(event, StepFinished)]
    assert harness.prompt_of("senior") == []
    assert started == ["cross-senior"]
    assert [(event.key, event.status) for event in finished] == [("cross-senior", Status.SKIP)]
    assert finished[0].message is not None and english(finished[0].message) == WALL_LINE
    assert report.stopped is not None and english(report.stopped) == WALL_LINE


class Slow(Actor):
    def __init__(self, root: Path, script: dict[str, list[Act]], ticker: Ticker) -> None:
        super().__init__("codex", root, script)
        self.ticker = ticker

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        outcome = super().run(request, on_event)
        if len(self.requests) == 2:
            self.ticker.now = WALL_S + 1.0
        return outcome


@pytest.mark.parametrize("engines", [SENIOR, TEAM])
@pytest.mark.parametrize("cut", ["wall", "deadline"])
def test_a_repair_cut_by_the_wall_ends_with_the_time_limit_before_a_second_check(
    tmp_path: Path, engines: Mapping[Role, str], cut: str
) -> None:
    seed(tmp_path)
    ticker = Ticker()
    walled = cut == "wall"
    repair = Act(
        EDITED,
        0.1,
        {"src/layout.ts": "y\n"},
        WALL_LIMIT_SUBTYPE if walled else "success",
        partial=walled,
    )
    script = {"senior": [Act(EDITED, 0.2, {"src/layout.ts": "x\n"}), repair]}
    harness = Harness(
        tmp_path,
        script,
        budget=0.0,
        verify_results=[FAILING, PASSING],
        wall_s=WALL_S,
        monotonic=ticker,
    )
    if not walled:
        harness.actors["codex"] = Slow(tmp_path / "work", script, ticker)
    report, _ = harness.run(engines, FIX)
    assert [step.repair for step in report.steps] == [False, True]
    assert len(report.verifications) == 1
    assert harness.prompt_of("tester") == []
    assert report.state is CompletionState.PARTIAL
    assert report.stopped is not None and english(report.stopped) == WALL_LINE
    assert report.handoffs[-1].role == Role.SENIOR.value


def codex_pipeline(tmp_path: Path, engine: codex.StopEngine, wall_s: float) -> CrossEnginePipeline:
    counter = iter(range(100))
    launcher = EngineLauncher(
        engine,
        MemoryLedger(),
        FixedClock(),
        lambda: f"RUN{next(counter)}",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
        prices=codex.PRICES,
    )
    return CrossEnginePipeline(
        lambda name: launcher,
        tuple,
        FileCapsuleStore(tmp_path / "capsules"),
        str(tmp_path),
        1.0,
        change_plan=lambda request: codex.PLAN,
        snapshot=lambda: dict(engine.files),
        governor=GovernorSetup(
            engine.clock, codex.PRICES, lambda model: 0.01 if model == codex.MODEL else 0.0
        ),
        wall_s=wall_s,
        monotonic=engine.clock,
    )


def codex_team(*roles: Role) -> RoutePlan:
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role in roles else None,
            ModelEntry("codex", codex.MODEL, "sol", "openai") if role in roles else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(), None, None, (), routes, "heuristic")


def test_a_codex_resume_due_after_the_team_deadline_is_not_launched(tmp_path: Path) -> None:
    engine = codex.StopEngine(codex.Clock(), {"src/a.ts": "base"})
    pipeline = codex_pipeline(tmp_path, engine, 60.0)
    report = pipeline.run(codex.REQUEST, codex.senior_only(), codex.Recorder())
    assert engine.halts == [GOVERNOR_STOP_SUBTYPE]
    assert len(engine.requests) == 1
    assert [taken.outcome for taken in report.governor] == [SALVAGED]
    assert report.state is CompletionState.PARTIAL
    assert report.stopped is not None
    assert english(report.stopped) == "stopped by the time limit of 1 min"


class WalledResume(codex.StopEngine):
    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        if not request.resume_session:
            return super().run(request, on_event)
        self.requests.append(request)
        self.clock.now += 10_000.0
        cut = RunResult(
            False,
            WALL_LIMIT_SUBTYPE,
            None,
            0,
            request.resume_session,
            (ModelUsage(codex.MODEL, input_tokens=1_000),),
            "",
            terminal_reason="max_wall",
            partial=True,
        )
        on_event(cut)
        return EngineOutcome(1, cut, 0)


@pytest.mark.parametrize("roles", [(Role.SENIOR,), (Role.SENIOR, Role.TESTER)])
def test_a_codex_resume_cut_by_the_wall_ends_with_the_time_limit(
    tmp_path: Path, roles: tuple[Role, ...]
) -> None:
    engine = WalledResume(codex.Clock(), {"src/a.ts": "base"})
    report = codex_pipeline(tmp_path, engine, 600.0).run(
        codex.REQUEST, codex_team(*roles), codex.Recorder()
    )
    assert engine.halts == [GOVERNOR_STOP_SUBTYPE]
    assert len(engine.requests) == 2
    assert [taken.outcome for taken in report.governor] == [SALVAGED]
    assert [(step.resumed, step.partial) for step in report.steps] == [
        (False, False),
        (True, True),
    ]
    assert report.state is CompletionState.PARTIAL
    assert report.stopped is not None
    assert english(report.stopped) == "stopped by the time limit of 10 min"


class Overrunning(steering.TurnEngine):
    def __init__(self, legs: list[steering.Leg], ticker: Ticker) -> None:
        super().__init__(legs)
        self.ticker = ticker

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        outcome = super().run(request, on_event)
        self.ticker.now = WALL_S + 1.0
        return outcome


def test_a_rotation_due_after_the_team_deadline_is_not_launched(tmp_path: Path) -> None:
    ticker = Ticker()
    engine = Overrunning(
        [steering.Leg((150_000,), cost=0.06), steering.Leg((150_000,) * 3, cost=0.2)], ticker
    )
    saved: dict[str, Mapping[str, object]] = {}
    ids = iter(range(100))

    def save(run_id: str, metrics: Mapping[str, object]) -> None:
        saved[run_id] = metrics

    launcher = steering.launcher_for(engine, MemoryLedger())
    pipeline = CrossEnginePipeline(
        lambda name: launcher,
        tuple,
        FileCapsuleStore(tmp_path / "capsules"),
        str(tmp_path),
        2.0,
        change_plan=lambda request: steering.PLAN,
        save_metrics=save,
        forecaster=steering.FixedForecaster(steering.senior_forecast(2.0)),
        new_run_id=lambda: f"ROOT{next(ids)}",
        governor=steering.setup(),
        wall_s=WALL_S,
        monotonic=ticker,
    )
    report = pipeline.run(steering.REQUEST, steering.senior_only(), steering.Recorder())
    assert len(engine.requests) == 1
    rotations = [taken for taken in report.governor if taken.reaction.kind is ReactionKind.ROTATE]
    assert [(taken.sent, taken.outcome) for taken in rotations] == [(True, SKIPPED)]
    assert [step.rotated for step in report.steps] == [False]
    assert report.state is CompletionState.PARTIAL
    assert report.stopped is not None and english(report.stopped) == WALL_LINE


def test_an_unbudgeted_repair_records_no_budget(tmp_path: Path) -> None:
    seed(tmp_path)
    harness = Harness(tmp_path, senior_edit(), budget=0.0, verify_results=[FAILING, PASSING])
    report, _ = harness.run(SENIOR, FIX)
    assert [step.repair for step in report.steps] == [False, True]
    assert report.steps[1].budget_usd == 0.0
    steps = cross_payload(report)["steps"]
    assert isinstance(steps, list) and [row["budget_usd"] for row in steps] == [0.0, 0.0]


def test_a_role_cut_by_the_wall_shows_its_partial_cost_and_the_partial_total(
    tmp_path: Path,
) -> None:
    seed(tmp_path)
    harness = Harness(
        tmp_path,
        senior_edit(1.8, subtype=WALL_LIMIT_SUBTYPE, partial=True),
        budget=5.0,
        wall_s=WALL_S,
        monotonic=Ticker(),
    )
    report, _ = harness.run(SENIOR, FIX)
    assert report.state is CompletionState.PARTIAL
    assert report.stopped is not None and english(report.stopped) == WALL_LINE
    assert [step.partial for step in report.steps] == [True]
    assert report.spent_usd == pytest.approx(1.8) and report.partial
    assert cost_cells(report) == ["$1.80 (partial)"]
    assert total_line(report, 5.0) == "spent $1.80 (partial) of $5.00"
    payload = cross_payload(report)
    assert payload["spent_usd"] == pytest.approx(1.8) and payload["partial"] is True
    steps = payload["steps"]
    assert isinstance(steps, list) and steps[0]["partial"] is True
    assert harness.saved[report.steps[0].run_id]["stopped"] == {
        "key": "stop.wall_limit",
        "params": {"minutes": "30"},
    }
    run = harness.ledger.get_run(report.steps[0].run_id)
    assert run is not None and run.max_wall_s == WALL_S
    assert english(stop_message(run)) == WALL_LINE


@pytest.mark.parametrize(("budget", "roles"), [(5.0, 1), (0.0, 4)])
def test_a_partial_role_stops_a_budgeted_team_but_its_cost_stays_in_the_total(
    tmp_path: Path, budget: float, roles: int
) -> None:
    seed(tmp_path)
    script = {"analyst": [Act(analyst_json(), 0.3, partial=True)]}
    harness = Harness(tmp_path, script, budget=budget)
    report, _ = harness.run()
    assert len(report.steps) == roles
    assert [step.partial for step in report.steps] == [True] + [False] * (roles - 1)
    assert report.partial
    assert report.spent_usd == pytest.approx(0.3 + 0.1 * (roles - 1))
    if budget:
        assert report.stopped is not None and report.stopped.key == "cross.cost_unknown"
        assert total_line(report, budget) == "spent $0.3000 (partial) of $5.00"
    else:
        assert report.ok
        assert total_line(report, budget) == "spent $0.6000 (partial)"
