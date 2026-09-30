from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.instinct import DecisionMaker
from cuanta.application.mandate import MandateService
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.timing import PhaseRecorder
from cuanta.domain.detection import Stack
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.time_anatomy import analyze_time
from cuanta.ports.ledger import EventQuery
from tests.unit.test_launch_cost import SilentEngine
from tests.unit.test_time_anatomy import ManualClock


class TimedEngine(SilentEngine):
    def __init__(self, clock: ManualClock) -> None:
        super().__init__(0.01, "sonnet", name="claude")
        self.clock = clock

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        self.clock.sleep(4)
        return super().run(request, on_event)


def test_local_phases_exclude_user_wait_and_keep_verification_outside_engine(
    tmp_path: Path,
) -> None:
    clock, ledger = ManualClock(), MemoryLedger()
    workspace = LocalWorkspace(tmp_path)
    engine = TimedEngine(clock)
    service = MandateService(
        workspace, ledger, DecisionMaker(HeuristicInstinct(), ledger, clock.now_iso), clock.now_iso
    )
    launcher = EngineLauncher(
        engine, ledger, clock, lambda: "R", lambda size: b"x" * size, "project", 4318, None
    )

    def verify(run_id: str) -> str:
        clock.sleep(3)
        return "green"

    def stack() -> Stack:
        clock.sleep(2)
        return Stack()

    flow = MandateFlow(
        service,
        lambda name: engine,
        lambda selected: launcher,
        stack,
        lambda run_id: ({}, None),
        str(tmp_path),
        "claude",
        1,
        final_suite=verify,
        refresh_index=lambda: clock.sleep(1),
        new_run_id=lambda: "R",
        timing=PhaseRecorder(clock, ledger),
    )
    prepared = flow.prepare(
        MandateRequest(
            "feature", "add a title", "clarity", tests="title exists", out_of_scope="docs"
        ),
        0,
        MandateOptions(simple=True),
    )
    clock.sleep(100)
    report = flow.run(prepared, RecordingSink())
    anatomy = analyze_time(report.run, ledger.events(EventQuery(run_id=report.run.id)))
    phases = {row.phase: row for row in anatomy.phases}
    assert report.tests == "green"
    assert anatomy.wall_seconds == 10
    assert phases["index_refresh"].seconds == 1
    assert phases["forecast_plan"].seconds == 2
    assert phases["verification"].seconds == 3
    assert phases["snapshots_guards"].seconds == 0
    assert phases["handoff"].seconds == 0
    assert phases["engine_startup"].seconds is None
