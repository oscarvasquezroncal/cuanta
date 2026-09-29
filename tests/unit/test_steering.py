from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CompletionState, CrossEnginePipeline, CrossReport
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.forecast import Forecaster, PlannedForecast
from cuanta.application.governor import Governor
from cuanta.application.mandate import Composed, MandateReport
from cuanta.application.mandate_flow import MandateFlow, Prepared
from cuanta.application.routing import RoutePlan
from cuanta.application.steering import (
    CHECKPOINT_TURN,
    FINISH_TURN,
    ROLE_FINISH,
    TEAM_FINISH,
    GovernorSetup,
    claude_role_plan,
    restart_left,
    resume_prompt,
    session_plan,
    session_steering,
)
from cuanta.domain.cache import PrefixState
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.depth import Depth, profile
from cuanta.domain.engine import (
    EngineEvent,
    EngineOutcome,
    EngineRequest,
    ModelUsage,
    RunResult,
    StepUsage,
)
from cuanta.domain.envelope import EnvelopeInputs, RoleInput, RoleModel, envelope
from cuanta.domain.governor import SKIPPED, ReactionKind, RolePlan, Trigger
from cuanta.domain.governor_report import HOOKS
from cuanta.domain.instinct import Choice
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.progress import ProgressEvent
from cuanta.domain.role_budgets import USD_MARGIN_FALLBACK, native_cap
from cuanta.domain.routing import ROLES, Provider, Role, RoleRoute, RoutingPolicy
from cuanta.ports.engine import TurnInput

if TYPE_CHECKING:
    from cuanta.application.mandate import MandateService

SONNET = Price(3.0, 15.0, 3.75, 0.30)
PRICES = PriceTable({"sonnet": SONNET})
REQUEST = MandateRequest(type="feature", what="add canonical", why="seo", out_of_scope="secrets")
PLAN = ChangePlan(edit=(EditTarget("src/a.ts", 0.9),))
DONE = '{"summary": "done", "status": "done"}'
CHECKPOINT = '{"summary": "halfway", "status": "partial", "next_step": "edit src/b.ts"}'
STOPPED = '{"summary": "stopped early", "status": "partial"}'


class Recorder:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)

    def texts(self) -> list[str]:
        return [
            english(message)
            for event in self.events
            if (message := getattr(event, "message", None)) is not None
        ]


@dataclass
class Leg:
    contexts: tuple[int, ...]
    cost: float = 0.1
    subtype: str = "success"
    late: bool = False


class TurnEngine:
    def __init__(self, legs: list[Leg], accepts: bool = True, delivers: bool = True) -> None:
        self._legs = legs
        self._accepts = accepts
        self._delivers = delivers
        self._running = False
        self._step = 0
        self.requests: list[EngineRequest] = []
        self.turns: list[tuple[int, int, str]] = []

    @property
    def name(self) -> str:
        return "claude"

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "x"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return ["claude"]

    def accepts_turns(self) -> bool:
        return self._accepts

    def send_turn(self, text: str) -> bool:
        if not self._running or not self._delivers:
            return False
        self.turns.append((len(self.requests), self._step, text))
        return True

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.requests.append(request)
        leg = self._legs.pop(0)
        leg_number = len(self.requests)
        self._running = True
        for index, context in enumerate(leg.contexts, 1):
            self._step = index
            usage = ModelUsage("sonnet", cache_read_tokens=context)
            on_event(StepUsage(usage, message_id=f"{leg_number}-{index}"))
            if self._heard(leg_number, CHECKPOINT_TURN):
                break
        self._running = False
        text = (
            DONE
            if leg.late
            else CHECKPOINT
            if self._heard(leg_number, CHECKPOINT_TURN)
            else STOPPED
            if self._heard(leg_number, ROLE_FINISH)
            else DONE
        )
        ok = leg.subtype == "success"
        result = RunResult(ok, leg.subtype, leg.cost, len(leg.contexts), "s", (), text)
        on_event(result)
        return EngineOutcome(0 if ok else 1, result, len(leg.contexts), late_results=int(leg.late))

    def _heard(self, leg: int, text: str) -> bool:
        return any(number == leg and sent == text for number, _, sent in self.turns)


class FixedForecaster(Forecaster):
    def __init__(self, planned: PlannedForecast) -> None:
        self.planned = planned

    def plan(
        self,
        task_type: str,
        plan: RoutePlan,
        provider: Provider,
        depth: str,
        shape: str,
        cap: float,
        change_plan: ChangePlan | None = None,
        native: bool = False,
        model: str = "",
        max_turns: int = 0,
    ) -> PlannedForecast:
        return self.planned

    def record(
        self, run_id: str, planned: PlannedForecast, request: MandateRequest
    ) -> PlannedForecast:
        return planned


def senior_forecast(cap: float) -> PlannedForecast:
    inputs = EnvelopeInputs(
        task_type="feature",
        depth=profile(Depth.NORMAL, "feature"),
        shape="pipeline",
        provider=Provider.CLAUDE,
        roles=(RoleInput(Role.SENIOR, RoleModel("sonnet", SONNET), fixed_tokens=20_000),),
        cap_usd=cap,
        edit_tokens=(2_000,),
        read_tokens=(1_000,),
    )
    return PlannedForecast(inputs, envelope(inputs), PrefixState.COLD)


def senior_only() -> RoutePlan:
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role is Role.SENIOR else None,
            ModelEntry("claude", "sonnet", "m", "p") if role is Role.SENIOR else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(), None, None, (), routes, "heuristic")


def launcher_for(engine: TurnEngine, ledger: MemoryLedger) -> EngineLauncher:
    counter = iter(range(100))
    return EngineLauncher(
        engine,
        ledger,
        FixedClock(),
        lambda: f"RUN{next(counter)}",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
    )


def setup() -> GovernorSetup:
    return GovernorSetup(lambda: 100.0, PRICES)


def cross_run(
    tmp_path: Path,
    engine: TurnEngine,
    budget: float,
    governor: GovernorSetup | None,
    forecast: PlannedForecast | None = None,
    discipline: bool = False,
) -> tuple[CrossReport, Recorder, dict[str, dict[str, object]]]:
    ledger = MemoryLedger()
    saved: dict[str, dict[str, object]] = {}
    launcher = launcher_for(engine, ledger)
    ids = iter(range(100))

    def save(run_id: str, metrics: Mapping[str, object]) -> None:
        saved.setdefault(run_id, {}).update(metrics)

    pipeline = CrossEnginePipeline(
        lambda name: launcher,
        tuple,
        FileCapsuleStore(tmp_path / "capsules"),
        str(tmp_path),
        budget,
        change_plan=lambda request: PLAN,
        save_metrics=save,
        forecaster=FixedForecaster(forecast) if forecast is not None else None,
        new_run_id=(lambda: f"ROOT{next(ids)}") if forecast is not None else None,
        governor=governor,
        read_discipline=(lambda engine: True) if discipline else None,
    )
    recorder = Recorder()
    return pipeline.run(REQUEST, senior_only(), recorder), recorder, saved


def test_a_governed_role_launches_on_stream_json_and_gets_one_finish_at_the_right_step(
    tmp_path: Path,
) -> None:
    engine = TurnEngine([Leg((250_000,) * 14, cost=0.9)])
    report, recorder, _ = cross_run(tmp_path, engine, 1.0, setup())
    assert isinstance(engine, TurnInput)
    assert [request.stream_input for request in engine.requests] == [True]
    assert engine.requests[0].prompt.startswith("You are the senior")
    assert engine.turns == [(1, 11, ROLE_FINISH)]
    assert ROLE_FINISH.startswith(FINISH_TURN)
    assert [
        (taken.reaction.kind, taken.reaction.trigger, taken.sent, taken.run_id)
        for taken in report.governor
    ] == [(ReactionKind.FINISH_NOW, Trigger.HEADROOM, True, "RUN0")]
    limit = native_cap(1.0, USD_MARGIN_FALLBACK)
    assert report.governor[0].reaction.projection.limit_usd == pytest.approx(limit)
    assert (
        f"senior: the governor sent the finish turn (two more steps would reach its limit) "
        f"at $0.8250 of ${limit:.4f}"
    ) in recorder.texts()
    assert report.steps[0].ok and report.state is CompletionState.COMPLETE


def test_an_undelivered_finish_falls_back_to_the_native_cap_and_the_salvage(
    tmp_path: Path,
) -> None:
    engine = TurnEngine(
        [Leg((250_000,) * 13, cost=0.93, subtype="error_max_budget_usd")], delivers=False
    )
    report, recorder, _ = cross_run(tmp_path, engine, 1.0, setup())
    assert engine.turns == []
    assert [(taken.reaction.kind, taken.sent) for taken in report.governor] == [
        (ReactionKind.FINISH_NOW, False)
    ]
    assert (
        "senior: the finish turn could not be sent; the native cap and salvage stop it instead"
        in recorder.texts()
    )
    assert report.steps[0].salvaged
    assert report.state is CompletionState.PARTIAL


def test_without_turn_input_or_a_governor_the_launch_is_the_legacy_one(tmp_path: Path) -> None:
    legacy = TurnEngine([Leg((250_000,) * 13, cost=0.93, subtype="error_max_budget_usd")])
    silent = TurnEngine(
        [Leg((250_000,) * 13, cost=0.93, subtype="error_max_budget_usd")], accepts=False
    )
    before, before_notes, _ = cross_run(tmp_path / "a", legacy, 1.0, None)
    after, after_notes, _ = cross_run(tmp_path / "b", silent, 1.0, setup())
    assert [request.stream_input for request in silent.requests] == [False]
    assert silent.turns == [] and legacy.turns == []
    assert after.governor == () and before.governor == ()
    assert [replace(request, cwd="") for request in silent.requests] == [
        replace(request, cwd="") for request in legacy.requests
    ]
    assert after_notes.texts() == before_notes.texts()
    assert after.steps[0].salvaged and before.steps[0].salvaged


def test_a_role_rotates_once_when_a_fresh_session_is_cheaper(tmp_path: Path) -> None:
    engine = TurnEngine([Leg((150_000,), cost=0.06), Leg((150_000, 150_000, 150_000), cost=0.2)])
    report, recorder, saved = cross_run(
        tmp_path, engine, 2.0, setup(), senior_forecast(2.0), discipline=True
    )
    assert [text for _, _, text in engine.turns] == [CHECKPOINT_TURN]
    assert [request.stream_input for request in engine.requests] == [True, True]
    first, second = engine.requests
    assert second.prompt == resume_prompt(first.prompt, CHECKPOINT)
    left = restart_left(2.0, 0.06)
    assert left == pytest.approx(1.94)
    assert second.max_budget_usd == pytest.approx(native_cap(left, USD_MARGIN_FALLBACK))
    assert [(step.role, step.rotated, step.cost_usd) for step in report.steps] == [
        (Role.SENIOR, True, 0.06),
        (Role.SENIOR, False, 0.2),
    ]
    assert [step.read_discipline for step in report.steps] == [HOOKS, HOOKS]
    assert report.spent_usd == pytest.approx(0.26)
    rotations = [taken for taken in report.governor if taken.reaction.kind is ReactionKind.ROTATE]
    assert len(rotations) == 1 and rotations[0].sent
    saving = rotations[0].reaction.saving_usd
    assert saving > 0.01
    rotated_run = report.steps[0].run_id
    assert saved[rotated_run]["rotation_saving_usd"] == round(saving, 6)
    assert saved[rotated_run]["role_share_usd"] == 2.0
    assert any(
        text.startswith(f"senior restarts in a fresh session with its checkpoint note: ${left:.4f}")
        for text in recorder.texts()
    )
    assert report.handoffs[-1].status.value == "done"
    assert report.state is CompletionState.COMPLETE


def test_a_role_with_a_small_context_keeps_its_session(tmp_path: Path) -> None:
    engine = TurnEngine([Leg((20_000, 22_000, 24_000), cost=0.05)])
    report, _, saved = cross_run(tmp_path, engine, 2.0, setup(), senior_forecast(2.0))
    assert engine.turns == []
    assert len(engine.requests) == 1 and engine.requests[0].stream_input
    assert report.governor == ()
    assert all("rotation_saving_usd" not in metrics for metrics in saved.values())
    assert [step.rotated for step in report.steps] == [False]


def test_no_restart_when_the_checkpoint_leaves_too_little_share(tmp_path: Path) -> None:
    engine = TurnEngine([Leg((150_000,), cost=1.8)])
    report, recorder, _ = cross_run(tmp_path, engine, 2.0, setup(), senior_forecast(2.0))
    assert [text for _, _, text in engine.turns] == [CHECKPOINT_TURN]
    assert len(engine.requests) == 1
    assert restart_left(2.0, 1.8) == 0.0
    assert "senior: no fresh session after the checkpoint; its note is the handoff" in (
        recorder.texts()
    )
    assert [step.rotated for step in report.steps] == [False]
    assert report.handoffs[-1].status.value == "partial"


def test_a_checkpoint_answered_after_a_successful_result_does_not_restart_the_role(
    tmp_path: Path,
) -> None:
    engine = TurnEngine([Leg((150_000,), cost=0.06, late=True), Leg((20_000,), cost=0.05)])
    report, recorder, _ = cross_run(tmp_path, engine, 2.0, setup(), senior_forecast(2.0))
    assert [text for _, _, text in engine.turns] == [CHECKPOINT_TURN]
    assert len(engine.requests) == 1
    assert [(step.rotated, step.ok, step.salvaged) for step in report.steps] == [
        (False, True, False)
    ]
    rotations = [taken for taken in report.governor if taken.reaction.kind is ReactionKind.ROTATE]
    assert [(taken.sent, taken.outcome) for taken in rotations] == [(True, SKIPPED)]
    assert "senior: no fresh session after the checkpoint; its note is the handoff" in (
        recorder.texts()
    )
    assert report.handoffs[-1].status.value == "done"
    assert report.state is CompletionState.COMPLETE


def test_role_and_session_plans_come_from_the_forecast_and_the_cap() -> None:
    forecast = senior_forecast(2.0)
    plan = claude_role_plan(setup(), Role.SENIOR, "sonnet", 1.0, 0.92, forecast, PLAN)
    assert plan.rotatable and plan.fixed_tokens == 20_000 and plan.price == SONNET
    assert plan.planned_requests == forecast.envelope.roles[0].requests
    assert plan.edit_paths == ("src/a.ts",) and plan.limit_usd == pytest.approx(0.92)
    bare = claude_role_plan(setup(), Role.TESTER, "sonnet", 1.0, 0.92, forecast, PLAN)
    assert not bare.rotatable and bare.planned_requests == 0 and bare.edit_paths == ()
    team = session_plan(GovernorSetup(lambda: 0.0), "sonnet", 3.0, forecast, PLAN)
    assert team.role is Role.ORCHESTRATOR and not team.rotatable
    assert team.planned_requests == forecast.envelope.requests
    assert team.limit_usd == 3.0 and team.price is None and team.read_paths == ()


def team_spec(tmp_path: Path, agents_file: str = "agents.json", cap: float = 1.0) -> LaunchSpec:
    return LaunchSpec(
        kind="mandate",
        prompt="build the team",
        cwd=str(tmp_path),
        allowed_tools=("Agent",),
        model="sonnet",
        max_budget_usd=cap,
        agents_file=agents_file,
        run_id="RUN7",
    )


def test_a_native_team_session_gets_one_finish_against_the_mandate_cap(tmp_path: Path) -> None:
    engine = TurnEngine([Leg((250_000,) * 14, cost=1.0)])
    launcher = launcher_for(engine, MemoryLedger())
    recorder = Recorder()
    spec = team_spec(tmp_path)
    steering = session_steering(setup(), launcher, spec, None, recorder)
    assert steering is not None
    launcher.launch(replace(spec, steer=True), steering)
    assert engine.turns == [(1, 12, TEAM_FINISH)]
    assert [(taken.reaction.trigger, taken.run_id) for taken in steering.taken] == [
        (Trigger.SHARE, "RUN7")
    ]
    assert recorder.texts() == [
        "team session: the governor sent the finish turn (its share is nearly spent) at $0.9000 "
        "of the $1.0000 cap; the main agent reads it when the running subagent returns"
    ]
    assert engine.requests[0].stream_input


def test_a_session_is_not_governed_without_a_team_a_cap_or_turn_input(tmp_path: Path) -> None:
    launcher = launcher_for(TurnEngine([]), MemoryLedger())
    silent = launcher_for(TurnEngine([], accepts=False), MemoryLedger())
    recorder = Recorder()
    assert session_steering(None, launcher, team_spec(tmp_path), None, recorder) is None
    assert session_steering(setup(), silent, team_spec(tmp_path), None, recorder) is None
    single = team_spec(tmp_path, agents_file="")
    assert session_steering(setup(), launcher, single, None, recorder) is None
    uncapped = team_spec(tmp_path, cap=0.0)
    assert session_steering(setup(), launcher, uncapped, None, recorder) is None


class StubService:
    def __init__(self) -> None:
        self.specs: list[LaunchSpec] = []

    def run(
        self,
        composed: Composed,
        launcher: EngineLauncher,
        spec: LaunchSpec,
        progress: object,
        summarize: object,
        observer: Callable[[EngineEvent], None] | None = None,
    ) -> MandateReport:
        self.specs.append(spec)
        launch = launcher.launch(spec, observer or (lambda event: None))
        return MandateReport(
            launch.run, launch.outcome.ok, (), "not run", {}, None, (), 0, Choice("normal", 0.5), ""
        )

    def close_decisions(self, run_id: str, outcome: str) -> None:
        return None

    def save_meta(self, report: MandateReport) -> None:
        return None


def team_flow(governor: GovernorSetup | None, service: StubService) -> MandateFlow:
    flow = MandateFlow.__new__(MandateFlow)
    flow._active = None
    flow._service = cast("MandateService", service)
    flow._summarize = lambda run_id: ({}, None)
    flow._routing = None
    flow._forecaster = None
    flow._learn_run = None
    flow._final_suite = None
    flow._governor = governor
    return flow


@pytest.mark.parametrize("governed", [True, False])
def test_the_mandate_flow_governs_a_native_team_only_with_a_governor(
    tmp_path: Path, governed: bool
) -> None:
    engine = TurnEngine([Leg((250_000,) * 14, cost=1.0)])
    launcher = launcher_for(engine, MemoryLedger())
    service = StubService()
    composed = Composed("build the team", Choice("normal", 0.5), 1, REQUEST, ("claude",))
    prepared = Prepared(composed, "claude", launcher, team_spec(tmp_path))
    seen: list[EngineEvent] = []
    flow = team_flow(setup() if governed else None, service)
    report = flow.run(prepared, Recorder(), seen.append, verdict=False)
    assert [spec.steer for spec in service.specs] == [governed]
    assert engine.requests[0].stream_input is governed
    assert len(seen) == 15
    assert engine.turns == ([(1, 12, TEAM_FINISH)] if governed else [])
    assert [taken.sent for taken in report.governor] == ([True] if governed else [])


def test_finish_and_checkpoint_turns_ask_for_the_handoff() -> None:
    assert FINISH_TURN == (
        "termina ahora: aplica lo que está completo, escribe el handoff y lista lo que falta"
    )
    assert "JSON handoff" in ROLE_FINISH and "status to partial" in ROLE_FINISH
    assert TEAM_FINISH.startswith(FINISH_TURN) and "subagents" in TEAM_FINISH
    assert "JSON handoff" in CHECKPOINT_TURN and "next_step" in CHECKPOINT_TURN
    resumed = resume_prompt("ROLE PROMPT", CHECKPOINT)
    assert resumed.startswith("ROLE PROMPT\n\n=== CHECKPOINT FROM YOUR EARLIER SESSION ===")
    assert json.loads(resumed.split("===\n", 2)[-1].split("\n\n", 1)[0])["status"] == "partial"


def test_a_restart_reopens_the_lane_and_voids_a_finish_the_old_session_never_got() -> None:
    plan = RolePlan(Role.SENIOR, Provider.CLAUDE, "sonnet", 1.0, 0, price=SONNET, hard_cap_usd=0.92)
    governor = Governor([plan], lambda: 0.0)
    kinds = [
        reaction.kind
        for index in range(1, 12)
        for reaction in governor.observe(
            Role.SENIOR,
            StepUsage(ModelUsage("sonnet", cache_read_tokens=250_000), message_id=f"a{index}"),
        )
    ]
    assert kinds == [ReactionKind.FINISH_NOW]
    governor.observe(Role.SENIOR, RunResult(True, "success", 0.83, 11, "s"))
    assert governor.check(Role.SENIOR) == ()
    governor.restart(Role.SENIOR)
    again = governor.observe(
        Role.SENIOR, StepUsage(ModelUsage("sonnet", cache_read_tokens=250_000), message_id="b1")
    )
    assert [(reaction.kind, reaction.trigger) for reaction in again] == [
        (ReactionKind.FINISH_NOW, Trigger.SHARE)
    ]
    assert governor.progress(Role.SENIOR).requests == 12
