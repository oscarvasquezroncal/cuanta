from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.cross_engine import CrossEnginePipeline
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.forecast import Forecaster, PlannedForecast, PlanSizes
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.routing import RoutePlan
from cuanta.bootstrap import Container
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.config import Config
from cuanta.domain.engine import EngineEvent, EngineRequest
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.progress import Note, Status
from cuanta.domain.routing import ROLES, Provider, Role, RoleRoute, RoutingPolicy
from tests.fakes import FakeRunner
from tests.unit.test_cross_engine import REQUEST, Recorder, ScriptedEngine
from tests.unit.test_forecast import COLD_CLOCK, PRICES, sizes, team

if TYPE_CHECKING:
    from cuanta.application.engine_run import LaunchSpec
    from cuanta.application.mandate import MandateService
    from cuanta.application.mandate_flow import Prepared
    from cuanta.application.route_apply import Applied
    from cuanta.domain.engine import EngineOutcome
    from cuanta.ports.progress import ProgressSink

CODEX_MODELS = {Role.ANALYST: "gpt-6-sol", Role.SENIOR: "gpt-6-sol", Role.TESTER: "gpt-6-luna"}


def codex_team() -> RoutePlan:
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role in CODEX_MODELS else None,
            ModelEntry("codex", CODEX_MODELS[role], "m", "openai")
            if role in CODEX_MODELS
            else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(engines=("codex",)), None, None, (), routes, "heuristic")


def forecaster(ledger: MemoryLedger) -> Forecaster:
    return Forecaster(
        ledger,
        PRICES,
        lambda: (),
        sizes,
        lambda engine: COLD_CLOCK,
        lambda: "2026-09-28T10:00:00Z",
    )


class BrokenForecaster(Forecaster):
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
        raise ValueError("forecast 01X has an unreadable plan")


class CheckingEngine(ScriptedEngine):
    def __init__(self, name: str, prompts: list[str], seen: list[tuple[str, ...]]) -> None:
        super().__init__(name, prompts)
        self.seen = seen
        self.ledger: MemoryLedger | None = None

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        assert self.ledger is not None
        self.seen.append(tuple(item.forecast.run_id for item in self.ledger.forecasts()))
        return super().run(request, on_event)


def cross(
    tmp_path: Path,
    ledger: MemoryLedger,
    seen: list[tuple[str, ...]],
    forecasting: bool,
    chosen: Forecaster | None = None,
) -> CrossEnginePipeline:
    counter = iter(range(100))

    def launcher(name: str) -> EngineLauncher:
        engine = CheckingEngine(name, [], seen)
        engine.ledger = ledger
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

    return CrossEnginePipeline(
        launcher,
        lambda: (),
        FileCapsuleStore(tmp_path),
        str(tmp_path),
        5.0,
        forecaster=(chosen or forecaster(ledger)) if forecasting else None,
        new_run_id=(lambda: "ROOT") if forecasting else None,
    )


def test_a_gpt_team_stores_its_forecast_under_the_root_run_before_the_first_launch(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    seen: list[tuple[str, ...]] = []
    recorder = Recorder()
    report = cross(tmp_path, ledger, seen, True).run(REQUEST, codex_team(), recorder)
    assert report.ok
    assert seen == [("ROOT",), ("ROOT",), ("ROOT",)]
    assert report.steps[0].run_id == "ROOT"
    [item] = ledger.forecasts()
    assert (item.forecast.provider, item.forecast.shape, item.forecast.cap_usd) == (
        "codex",
        "pipeline",
        5.0,
    )
    assert item.actual_usd == pytest.approx(report.spent_usd)
    notes = [event.text for event in recorder.events if isinstance(event, Note)]
    assert any(text.startswith("Forecast $") for text in notes)


def test_a_failed_team_forecast_is_reported_and_the_team_still_launches(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    seen: list[tuple[str, ...]] = []
    recorder = Recorder()
    broken = BrokenForecaster(ledger, PRICES, lambda: (), sizes, lambda engine: COLD_CLOCK, str)
    report = cross(tmp_path, ledger, seen, True, broken).run(REQUEST, codex_team(), recorder)
    assert report.ok
    assert report.steps[0].run_id == "ROOT"
    assert ledger.forecasts() == ()
    warned = [event.text for event in recorder.events if isinstance(event, Note)]
    assert (
        "Forecast unavailable, the launch goes ahead without one: "
        "forecast 01X has an unreadable plan"
    ) in warned


def test_without_a_forecaster_the_pipeline_launches_exactly_as_before(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    seen: list[tuple[str, ...]] = []
    report = cross(tmp_path, ledger, seen, False).run(REQUEST, codex_team(), Recorder())
    assert report.ok
    assert seen == [(), (), ()]
    assert [step.run_id for step in report.steps] == ["RUN0", "RUN1", "RUN2"]
    assert ledger.forecasts() == ()


class StubEngine:
    def available(self) -> bool:
        return True

    def missing_flags(self) -> tuple[str, ...]:
        return ()


class StubService:
    def __init__(self, ledger: MemoryLedger) -> None:
        self.ledger = ledger
        self.seen: list[tuple[str, ...]] = []

    def run(self, *args: object) -> object:
        self.seen.append(tuple(item.forecast.run_id for item in self.ledger.forecasts()))
        return SimpleNamespace(run=SimpleNamespace(id="R1", cost_usd=0.2), ok=True, tests="green")

    def close_decisions(self, run_id: str, outcome: str) -> None:
        return None

    def save_meta(self, report: object) -> None:
        return None


def native(ledger: MemoryLedger, service: StubService, forecasting: bool) -> MandateFlow:
    flow = MandateFlow.__new__(MandateFlow)
    flow._active = None
    flow._service = cast("MandateService", service)
    flow._summarize = lambda run_id: ({}, None)
    flow._routing = None
    flow._learn_run = None
    flow._forecaster = forecaster(ledger) if forecasting else None
    flow._governor = None
    return flow


def prepared(ledger: MemoryLedger, run_id: str) -> Prepared:
    planned: PlannedForecast = forecaster(ledger).plan(
        "bug", team(), Provider.CLAUDE, "normal", "pipeline", 50.0
    )
    return cast(
        "Prepared",
        SimpleNamespace(
            launcher=SimpleNamespace(engine=StubEngine()),
            engine_name="claude",
            composed=SimpleNamespace(request=REQUEST),
            spec=SimpleNamespace(run_id=run_id),
            applied=None,
            forecast=planned,
            forecast_error=None,
        ),
    )


@pytest.mark.parametrize(("forecasting", "run_id"), [(True, "R1"), (False, "R1"), (True, "")])
def test_a_native_mandate_stores_its_forecast_before_launch_only_with_a_forecaster_and_run_id(
    forecasting: bool, run_id: str
) -> None:
    ledger = MemoryLedger()
    service = StubService(ledger)
    recorder = Recorder()
    flow = native(ledger, service, forecasting)
    flow.run(prepared(ledger, run_id), cast("ProgressSink", recorder), verdict=False)
    stored = forecasting and bool(run_id)
    assert service.seen == [("R1",) if stored else ()]
    notes = [event for event in recorder.events if isinstance(event, Note)]
    assert bool(notes) is stored
    assert all(note.status is Status.INFO for note in notes)


def launch_spec(shape: str) -> LaunchSpec:
    return cast("LaunchSpec", SimpleNamespace(shape=shape, max_budget_usd=5.0, max_turns=12))


def test_the_native_forecast_prices_the_launch_model_shape_and_turn_rail() -> None:
    ledger = MemoryLedger()
    flow = native(ledger, StubService(ledger), True)
    applied = cast("Applied", SimpleNamespace(plan=team()))
    pinned = MandateOptions(model="claude-opus-5-5", depth="normal")
    single, failure = flow._forecast("bug", applied, "claude", pinned, launch_spec("single"), None)
    assert failure is None and single is not None
    assert single.inputs.max_turns == 12 and not single.inputs.native
    assert {item.model for item in single.envelope.roles} == {"claude-opus-5-5"}
    routed = MandateOptions(depth="normal")
    pipeline, _ = flow._forecast("bug", applied, "claude", routed, launch_spec("pipeline"), None)
    assert pipeline is not None and pipeline.inputs.native
    assert pipeline.envelope.roles[0].role is Role.ORCHESTRATOR


def test_a_failed_native_forecast_is_reported_and_the_launch_goes_ahead() -> None:
    ledger = MemoryLedger()
    service = StubService(ledger)
    flow = native(ledger, service, True)
    flow._forecaster = BrokenForecaster(
        ledger, PRICES, lambda: (), sizes, lambda engine: COLD_CLOCK, str
    )
    applied = cast("Applied", SimpleNamespace(plan=team()))
    options = MandateOptions(depth="normal")
    planned, failure = flow._forecast(
        "bug", applied, "claude", options, launch_spec("single"), None
    )
    assert planned is None and failure is not None
    ready = cast(
        "Prepared",
        SimpleNamespace(
            launcher=SimpleNamespace(engine=StubEngine()),
            engine_name="claude",
            composed=SimpleNamespace(request=REQUEST),
            spec=SimpleNamespace(run_id="R1"),
            applied=None,
            forecast=None,
            forecast_error=failure,
        ),
    )
    recorder = Recorder()
    flow.run(ready, cast("ProgressSink", recorder), verdict=False)
    assert service.seen == [()]
    notes = [event for event in recorder.events if isinstance(event, Note)]
    assert [(note.status, note.text) for note in notes] == [
        (
            Status.WARN,
            "Forecast unavailable, the launch goes ahead without one: "
            "forecast 01X has an unreadable plan",
        )
    ]


@pytest.mark.parametrize(
    ("database", "sidecar"),
    [(b"stub", "index.db-journal"), (b"not a database", "")],
)
def test_an_unreadable_code_index_leaves_the_plan_sizes_unknown(
    tmp_path: Path, database: bytes, sidecar: str
) -> None:
    state = tmp_path / ".cuanta"
    state.mkdir()
    (state / "index.db").write_bytes(database)
    if sidecar:
        (state / sidecar).write_bytes(b"")
    container = Container(tmp_path, Config(), runner=FakeRunner())
    plan = ChangePlan(edit=(EditTarget("src/calc.py", 0.9),), read=("src/util.py",))
    try:
        assert container.plan_sizes(plan) == PlanSizes((0,), (0,))
    finally:
        container.close()
