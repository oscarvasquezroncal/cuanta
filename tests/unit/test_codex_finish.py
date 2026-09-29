from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CompletionState, CrossEnginePipeline, CrossReport
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.routing import RoutePlan
from cuanta.application.steering import (
    FINISH_TURN,
    GovernorSetup,
    codex_finish_prompt,
    codex_role_plan,
    resumable_thread,
)
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.engine import (
    BUDGET_LIMIT_SUBTYPE,
    GOVERNOR_STOP_SUBTYPE,
    EngineEvent,
    EngineOutcome,
    EngineRequest,
    ModelUsage,
    RunResult,
    SessionStarted,
    ToolCall,
)
from cuanta.domain.governor import RESUMED, SALVAGED, ReactionKind, Trigger, seconds_rate
from cuanta.domain.ledger import Run
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.progress import ProgressEvent
from cuanta.domain.role_handoff import HandoffSource, HandoffStatus
from cuanta.domain.routing import ROLES, Provider, Role, RoleRoute, RoutingPolicy

MODEL = "gpt-6-sol"
THREAD = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"
PRICES = PriceTable({MODEL: Price(2.0, 10.0, 2.5, 0.2)})
REQUEST = MandateRequest(type="feature", what="add canonical", why="seo", out_of_scope="secrets")
PLAN = ChangePlan(edit=(EditTarget("src/a.ts", 0.9),))
DONE = '{"summary": "done", "status": "done"}'
HANDOFF = '{"summary": "canonical half done", "status": "partial", "next_step": "edit b.ts"}'


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
class Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class StopEngine:
    clock: Clock
    files: dict[str, str]
    items: int = 12
    resumes: bool = True
    resume_ok: bool = True
    resume_input: int = 10_000
    halts: list[str] = field(default_factory=list)
    requests: list[EngineRequest] = field(default_factory=list)
    ran_items: int = 0

    @property
    def name(self) -> str:
        return "codex"

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "codex-cli 0.156.1"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return ["codex"]

    def halt(self, subtype: str) -> None:
        self.halts.append(subtype)

    def resumable(self) -> bool:
        return self.resumes

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.requests.append(request)
        if request.resume_session:
            usage = ModelUsage(MODEL, input_tokens=self.resume_input, output_tokens=500)
            answer = RunResult(
                self.resume_ok,
                "success" if self.resume_ok else "error",
                None,
                1,
                request.resume_session,
                (usage,),
                HANDOFF if self.resume_ok else "",
            )
            on_event(answer)
            return EngineOutcome(0 if self.resume_ok else 1, answer, 0)
        on_event(SessionStarted(THREAD, MODEL))
        calls = 0
        for number in range(1, self.items + 1):
            self.clock.now += 10.0
            calls += 1
            kind = "file_change" if number == 3 else "shell"
            if kind == "file_change":
                self.files["src/a.ts"] = "changed"
            on_event(ToolCall(kind, f"item_{number}", {}))
            if self.halts:
                break
        self.ran_items = calls
        if self.halts:
            stopped = RunResult(
                False,
                GOVERNOR_STOP_SUBTYPE,
                None,
                0,
                THREAD,
                (ModelUsage(MODEL),),
                "half way",
                terminal_reason="governor_stop",
            )
            on_event(stopped)
            return EngineOutcome(1, stopped, calls)
        usage = ModelUsage(MODEL, input_tokens=50_000, output_tokens=1_000)
        done = RunResult(True, "success", None, 1, THREAD, (usage,), DONE)
        on_event(done)
        return EngineOutcome(0, done, calls)


def senior_only() -> RoutePlan:
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role is Role.SENIOR else None,
            ModelEntry("codex", MODEL, "sol", "openai") if role is Role.SENIOR else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(), None, None, (), routes, "heuristic")


@dataclass
class Harness:
    report: CrossReport
    recorder: Recorder
    saved: dict[str, dict[str, object]]
    ledger: MemoryLedger
    engine: StopEngine


def cross_run(tmp_path: Path, engine: StopEngine, rate: float | None) -> Harness:
    ledger = MemoryLedger()
    saved: dict[str, dict[str, object]] = {}
    counter = iter(range(100))
    launcher = EngineLauncher(
        engine,
        ledger,
        FixedClock(),
        lambda: f"RUN{next(counter)}",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
        prices=PRICES,
    )

    def save(run_id: str, metrics: Mapping[str, object]) -> None:
        saved.setdefault(run_id, {}).update(metrics)

    setup = (
        GovernorSetup(engine.clock, PRICES, lambda model: rate if model == MODEL else 0.0)
        if rate is not None
        else None
    )
    pipeline = CrossEnginePipeline(
        lambda name: launcher,
        tuple,
        FileCapsuleStore(tmp_path / "capsules"),
        str(tmp_path),
        1.0,
        change_plan=lambda request: PLAN,
        snapshot=lambda: dict(engine.files),
        save_metrics=save,
        governor=setup,
    )
    recorder = Recorder()
    report = pipeline.run(REQUEST, senior_only(), recorder)
    return Harness(report, recorder, saved, ledger, engine)


def test_a_codex_role_is_stopped_at_its_share_and_resumed_for_a_finish_turn(
    tmp_path: Path,
) -> None:
    engine = StopEngine(Clock(), {"src/a.ts": "base"})
    found = cross_run(tmp_path, engine, 0.01)
    report = found.report
    assert engine.halts == [GOVERNOR_STOP_SUBTYPE]
    assert engine.ran_items == 8
    first, resume = engine.requests
    assert not first.resume_session and not first.stream_input
    assert resume.resume_session == THREAD
    assert resume.prompt == codex_finish_prompt(("src/a.ts",))
    assert resume.prompt.startswith(FINISH_TURN) and "- src/a.ts" in resume.prompt
    assert [(step.stopped, step.resumed, step.ok) for step in report.steps] == [
        (True, False, True),
        (False, True, True),
    ]
    killed, finished = report.steps
    assert killed.cost_usd == pytest.approx(0.8)
    assert killed.cost_source == "estimated"
    assert finished.cost_usd == pytest.approx(0.025)
    assert finished.budget_usd == pytest.approx(0.2)
    assert report.spent_usd == pytest.approx(0.825)
    run = found.ledger.get_run(killed.run_id)
    assert run is not None and run.cost_usd == pytest.approx(0.8)
    assert run.cost_source == "estimated" and run.end_reason == GOVERNOR_STOP_SUBTYPE
    assert [
        (taken.reaction.kind, taken.reaction.trigger, taken.sent, taken.outcome, taken.run_id)
        for taken in report.governor
    ] == [(ReactionKind.CODEX_STOP, Trigger.HEADROOM, True, RESUMED, killed.run_id)]
    assert report.governor[0].reaction.projection.estimated
    texts = found.recorder.texts()
    stop = "senior: the governor stopped Codex at an estimated $0.8000 of its $1.0000 share"
    resuming = "senior: a short finish turn resumes the stopped thread"
    assert texts.index(stop) < texts.index(resuming)
    assert (
        "senior: the resumed thread wrote its handoff; the stopped turn cost about $0.8000 "
        "(estimate), $0.2000 of its share left"
    ) in texts
    assert found.saved[killed.run_id]["governor_stop_estimate_usd"] == 0.8
    governor = found.saved[killed.run_id]["governor"]
    assert isinstance(governor, dict)
    assert [row["outcome"] for row in governor["reactions"]] == [RESUMED]
    assert report.handoffs[-1].status is HandoffStatus.PARTIAL
    assert report.handoffs[-1].files_changed == ("src/a.ts",)
    assert report.state is CompletionState.COMPLETE


def test_a_failed_resume_falls_back_to_the_salvage(tmp_path: Path) -> None:
    engine = StopEngine(Clock(), {"src/a.ts": "base"}, resume_ok=False)
    found = cross_run(tmp_path, engine, 0.01)
    report = found.report
    assert len(engine.requests) == 2
    assert [(step.salvaged, step.stopped, step.resumed, step.ok) for step in report.steps] == [
        (True, False, False, False),
        (False, False, True, False),
    ]
    assert report.steps[0].cost_usd == pytest.approx(0.8)
    assert report.spent_usd == pytest.approx(0.825)
    assert [taken.outcome for taken in report.governor] == [SALVAGED]
    handoff = report.handoffs[-1]
    assert handoff.source is HandoffSource.SALVAGE
    assert handoff.reason == GOVERNOR_STOP_SUBTYPE
    assert handoff.files_changed == ("src/a.ts",)
    texts = found.recorder.texts()
    assert (
        "senior: the finish turn after the stop failed; salvage builds its handoff from the "
        "changed files"
    ) in texts
    assert report.state is CompletionState.PARTIAL


def test_a_resume_that_costs_more_than_the_share_left_keeps_its_handoff(tmp_path: Path) -> None:
    engine = StopEngine(Clock(), {"src/a.ts": "base"}, resume_input=150_000)
    found = cross_run(tmp_path, engine, 0.01)
    report = found.report
    assert len(engine.requests) == 2
    assert engine.requests[1].max_budget_usd == pytest.approx(0.2)
    assert [(step.salvaged, step.stopped, step.resumed, step.ok) for step in report.steps] == [
        (False, True, False, True),
        (False, False, True, True),
    ]
    killed, finished = report.steps
    assert finished.cost_usd == pytest.approx(0.305)
    assert finished.budget_usd == pytest.approx(0.2)
    assert finished.overrun_usd == pytest.approx(0.105)
    assert finished.handoff
    run = found.ledger.get_run(finished.run_id)
    assert run is not None and run.end_reason == BUDGET_LIMIT_SUBTYPE
    assert [taken.outcome for taken in report.governor] == [RESUMED]
    handoff = report.handoffs[-1]
    assert handoff.source is not HandoffSource.SALVAGE
    assert handoff.status is HandoffStatus.PARTIAL
    assert handoff.summary == "canonical half done"
    assert handoff.files_changed == ("src/a.ts",)
    texts = found.recorder.texts()
    assert not any("the finish turn after the stop failed" in text for text in texts)
    assert (
        "senior on codex spent $0.3050 against its $0.2000 share; the $0.1050 overrun comes out "
        "of the remaining budget"
    ) in texts
    assert killed.cost_usd == pytest.approx(0.8)
    assert report.spent_usd == pytest.approx(1.105)


def test_a_resume_with_no_share_left_shows_its_whole_cost_as_overrun(tmp_path: Path) -> None:
    engine = StopEngine(Clock(), {"src/a.ts": "base"})
    found = cross_run(tmp_path, engine, 0.2)
    report = found.report
    killed, finished = report.steps
    assert killed.cost_usd is not None and killed.cost_usd >= 1.0
    assert engine.requests[1].max_budget_usd == 0.0
    assert (finished.resumed, finished.ok, finished.salvaged) == (True, True, False)
    assert finished.budget_usd == 0.0
    assert finished.overrun_usd == pytest.approx(0.025)
    assert [taken.outcome for taken in report.governor] == [RESUMED]
    assert report.handoffs[-1].source is not HandoffSource.SALVAGE
    assert (
        "senior on codex spent $0.0250 against its $0.0000 share; the $0.0250 overrun comes out "
        "of the remaining budget"
    ) in found.recorder.texts()


def test_without_the_resume_capability_the_stopped_role_is_salvaged(tmp_path: Path) -> None:
    engine = StopEngine(Clock(), {"src/a.ts": "base"}, resumes=False)
    found = cross_run(tmp_path, engine, 0.01)
    report = found.report
    assert len(engine.requests) == 1
    assert [(step.salvaged, step.cost_usd) for step in report.steps] == [(True, 0.8)]
    assert report.steps[0].cost_source == "estimated"
    assert [taken.outcome for taken in report.governor] == [SALVAGED]
    texts = found.recorder.texts()
    assert (
        "senior: the stopped thread cannot be resumed; salvage builds its handoff from the "
        "changed files"
    ) in texts
    assert not any("resumes" in text for text in texts)


def test_a_role_inside_its_share_or_without_a_governor_runs_as_before(tmp_path: Path) -> None:
    governed = StopEngine(Clock(), {"src/a.ts": "base"}, items=5)
    legacy = StopEngine(Clock(), {"src/a.ts": "base"}, items=5)
    unrated = StopEngine(Clock(), {"src/a.ts": "base"}, items=12)
    before = cross_run(tmp_path / "a", legacy, None)
    after = cross_run(tmp_path / "b", governed, 0.01)
    silent = cross_run(tmp_path / "c", unrated, 0.0)
    assert governed.halts == [] and legacy.halts == [] and unrated.halts == []
    assert after.report.governor == () and before.report.governor == ()
    assert silent.report.governor == ()
    assert [replace(request, cwd="") for request in governed.requests] == [
        replace(request, cwd="") for request in legacy.requests
    ]
    assert after.recorder.texts() == before.recorder.texts()
    assert [(step.cost_usd, step.stopped) for step in after.report.steps] == [
        (before.report.steps[0].cost_usd, False)
    ]


def test_the_seconds_rate_comes_from_finished_runs_of_the_same_model() -> None:
    def run(model: str, cost: float | None, seconds: int, status: str = "ok") -> Run:
        return Run(
            f"R{model}{seconds}",
            "cross",
            "codex",
            model,
            started_at="2026-09-28T10:00:00",
            ended_at=f"2026-09-28T10:{seconds // 60:02d}:{seconds % 60:02d}",
            status=status,
            cost_usd=cost,
        )

    runs = [run(MODEL, 0.3, 300), run(MODEL, 0.4, 200), run(MODEL, 0.6, 600)]
    assert seconds_rate(runs[:2], MODEL) == 0.0
    assert seconds_rate(runs, MODEL) == pytest.approx(0.001)
    noisy = [
        *runs,
        run(MODEL, 5.0, 10, status="failed"),
        run(MODEL, None, 20),
        run("gpt-6-luna", 9.0, 30),
    ]
    assert seconds_rate(noisy, MODEL) == pytest.approx(0.001)
    setup = GovernorSetup(lambda: 0.0, PRICES, lambda model: seconds_rate(runs, model))
    plan = codex_role_plan(setup, Role.SENIOR, MODEL, 0.48, None, PLAN)
    assert plan.provider is Provider.CODEX and plan.usd_per_second == pytest.approx(0.001)
    assert plan.price == PRICES.lookup(MODEL)


def test_only_plain_thread_ids_are_resumed() -> None:
    assert resumable_thread(THREAD)
    assert not resumable_thread("")
    assert not resumable_thread("--last")
    assert not resumable_thread("a b")


def test_a_launcher_without_a_stoppable_engine_reports_the_stop_as_not_sent() -> None:
    class Plain:
        name = "plain"

        def available(self) -> bool:
            return True

        def version(self) -> str:
            return ""

        def missing_flags(self) -> tuple[str, ...]:
            return ()

        def cancel(self) -> None:
            return None

        def command(self, request: EngineRequest) -> list[str]:
            return []

        def run(
            self, request: EngineRequest, on_event: Callable[[EngineEvent], None]
        ) -> EngineOutcome:
            return EngineOutcome(0, None, 0)

    launcher = EngineLauncher(
        Plain(),
        MemoryLedger(),
        FixedClock(),
        lambda: "R",
        lambda size: b"\x01" * size,
        "p",
        1,
        None,
    )
    assert not launcher.halt(lambda: 0.5)
    assert not launcher.resumable()
