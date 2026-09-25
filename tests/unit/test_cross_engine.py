from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CROSS_ORDER, CrossEnginePipeline
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.routing import RoutePlan
from cuanta.domain.agents import parse_agent
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.progress import ProgressEvent
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy

REQUEST = MandateRequest(type="bug", what="fix add", why="add(2, 3) == -1", out_of_scope="tests")


class Recorder:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)


class ScriptedEngine:
    def __init__(self, name: str, prompts: list[str], fail_on: str = "", cost: float = 0.5) -> None:
        self._name = name
        self.prompts = prompts
        self.fail_on = fail_on
        self.cost = cost

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "x"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        return None

    def command(self, request: EngineRequest) -> list[str]:
        return [self._name]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.prompts.append(request.prompt)
        failed = bool(self.fail_on) and self.fail_on in request.prompt.split("=== REQUEST")[0]
        text = f'{{"from": "{request.model}"}}'
        result = RunResult(not failed, "success", self.cost, 1, "s", (), text)
        on_event(result)
        return EngineOutcome(0 if not failed else 1, result, 0)


def plan() -> RoutePlan:
    engines = {Role.ANALYST: "claude", Role.SENIOR: "codex", Role.TESTER: "claude"}
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role in engines else None,
            ModelEntry(engines[role], f"model-{role.value}", "m", "p") if role in engines else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(), None, None, (), routes, "heuristic")


def pipeline(
    tmp_path: Path, prompts: list[str], budget: float, fail_on: str = ""
) -> CrossEnginePipeline:
    ledger = MemoryLedger()
    counter = iter(range(100))

    def launcher(name: str) -> EngineLauncher:
        return EngineLauncher(
            ScriptedEngine(name, prompts, fail_on),
            ledger,
            FixedClock(),
            lambda: f"RUN{next(counter)}",
            lambda size: b"\x01" * size,
            "shop",
            4318,
            None,
        )

    senior = parse_agent("---\nname: python-senior\ndescription: d\n---\nYou are the SENIOR.\n")
    assert senior is not None
    return CrossEnginePipeline(
        launcher, lambda: (senior,), FileCapsuleStore(tmp_path), str(tmp_path), budget
    )


def test_each_role_runs_on_its_engine_with_the_previous_handoff(tmp_path: Path) -> None:
    prompts: list[str] = []
    recorder = Recorder()
    report = pipeline(tmp_path, prompts, 5.0).run(REQUEST, plan(), recorder)
    assert report.ok
    assert [step.role for step in report.steps] == [Role.ANALYST, Role.SENIOR, Role.TESTER]
    assert [step.engine for step in report.steps] == ["claude", "codex", "claude"]
    assert "none: you are the first role." in prompts[0]
    assert '{"from": "model-analyst"}' in prompts[1]
    assert prompts[1].startswith("You are the SENIOR.")
    assert '{"from": "model-senior"}' in prompts[2]
    assert report.spent_usd == 1.5
    assert CROSS_ORDER[-1] is Role.DOCS


def test_the_budget_stops_the_pipeline(tmp_path: Path) -> None:
    report = pipeline(tmp_path, [], 0.9).run(REQUEST, plan(), Recorder())
    assert not report.ok
    assert len(report.steps) == 2
    assert report.stopped is not None
    assert english(report.stopped) == "the cross-engine budget is spent"


def test_a_failed_role_stops_the_pipeline(tmp_path: Path) -> None:
    report = pipeline(tmp_path, [], 5.0, fail_on="SENIOR").run(REQUEST, plan(), Recorder())
    assert not report.ok
    assert [step.ok for step in report.steps] == [True, False]
    assert report.stopped is not None
    assert "senior failed" in english(report.stopped)
