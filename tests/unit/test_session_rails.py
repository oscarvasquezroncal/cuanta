from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.implementer import ImplementationSession
from cuanta.application.mandate_flow import MandateOptions, stepped_prepared
from cuanta.application.run_reports import RunReports
from cuanta.domain.config import Config
from cuanta.domain.engine import EngineOutcome
from cuanta.domain.mandate import MandateRequest
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream
from tests.real_run import REAL_MODEL, result_line, scout_read, scout_spawn
from tests.unit.test_engine_profiles import flow as profile_flow
from tests.unit.test_implementer import (
    COMMANDS,
    NEW_ERROR,
    CheckSequence,
    checks,
    record_turn,
    result,
)

TURNS = {"claude --help": Completed(0, "--input-format", "")}
FEATURE = MandateRequest(
    type="feature",
    what="add a status badge",
    why="the page hides its state",
    tests="the badge shows the state",
    out_of_scope="docs",
)


@dataclass
class LongScout(FakeStream):
    clock: FixedClock = field(default_factory=FixedClock)
    released: threading.Event = field(default_factory=threading.Event)
    pause_s: float = 0.2

    def lines(self) -> Iterator[str]:
        yield scout_spawn()
        yield scout_read("msg_scout_1")
        self.released.wait(self.pause_s)
        self.clock.sleep(901.0)
        if not self.terminated:
            yield from self.output

    def terminate(self) -> None:
        self.terminated = True
        self.released.set()


@dataclass
class SlowSteps(FakeStream):
    clock: FixedClock = field(default_factory=FixedClock)
    plan: str = ""

    def lines(self) -> Iterator[str]:
        self.clock.sleep(901.0)
        yield self.plan
        for line in self.output:
            if self.ended or self.terminated:
                return
            self.clock.sleep(901.0)
            yield line


@dataclass
class StoppedByUser(FakeStream):
    holder: list[ClaudeCodeEngine] = field(default_factory=list)

    def lines(self) -> Iterator[str]:
        yield scout_spawn()
        self.holder[0].cancel()
        yield result_line("ignored")


def turn_text(sent: str) -> str:
    content = json.loads(sent)["message"]["content"]
    return str(content[0]["text"])


def launcher_for(
    engine: ClaudeCodeEngine,
    clock: FixedClock,
    session: ImplementationSession,
    reports: RunReports | None = None,
) -> EngineLauncher:
    return EngineLauncher(
        engine,
        MemoryLedger(),
        clock,
        lambda: "R",
        lambda size: b"x" * size,
        "project",
        4318,
        None,
        reports=reports,
        implementer=lambda _: session,
    )


def test_a_native_scout_longer_than_the_repair_window_is_not_halted(tmp_path: Path) -> None:
    clock = FixedClock()
    stream = LongScout(
        [result_line("scout mapped the anchors; senior edited", cost=0.4, turns=12)], clock=clock
    )
    engine = ClaudeCodeEngine(FakeRunner(streams={"claude": stream}, responses=TURNS))
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks()), clock.monotonic, timeout_s=0.01
    )
    launch = launcher_for(engine, clock, session).launch(
        LaunchSpec("mandate", "feature", str(tmp_path), (), model=REAL_MODEL),
        lambda _: None,
    )
    assert not stream.terminated
    assert launch.outcome.ok and launch.outcome.result is not None
    assert launch.outcome.result.text == "scout mapped the anchors; senior edited"
    assert launch.implementation is not None and launch.implementation.reason == ""
    assert [step.state for step in launch.implementation.steps] == ["green"]
    assert launch.run.status == "ok" and launch.run.turns == 12


def test_a_fast_writer_whose_steps_outlast_the_repair_window_is_not_halted(
    tmp_path: Path,
) -> None:
    from cuanta.bootstrap import Container

    clock = FixedClock()
    plan = result_line('{"implementation_steps":["add the badge","wire the badge"]}', cost=0.1)
    stream = SlowSteps(
        [result_line("badge added", cost=0.3, turns=4), result_line("badge wired", 0.6, 9)],
        clock=clock,
        plan=plan,
    )
    runner = FakeRunner(streams={"claude -p": stream}, responses=TURNS)
    engine = ClaudeCodeEngine(runner)
    options = MandateOptions(profile="fast", model=REAL_MODEL)
    prepared = stepped_prepared(profile_flow(tmp_path, engine).prepare(FEATURE, 0, options), True)
    spec = replace(prepared.spec, verify_commands=("npm run check",))
    assert (spec.profile, spec.implementation_steps, spec.pure) == ("fast", True, True)
    container = Container(tmp_path, Config(), runner=runner, clock=clock, home=tmp_path / "home")
    try:
        launcher = container.launcher(engine, container.ledger(), telemetry=False)
        launch = launcher.launch(spec, lambda _: None)
    finally:
        container.close()
    assert not stream.terminated and stream.ended
    assert [turn_text(sent).splitlines()[0] for sent in stream.sent] == [
        "Cuanta authorizes step 1/2: add the badge",
        "Cuanta authorizes step 2/2: wire the badge",
    ]
    implementation = launch.implementation
    assert implementation is not None and implementation.passed
    assert implementation.reason == "" and implementation.repairs == 0
    assert [step.state for step in implementation.steps] == ["green", "green"]
    assert implementation.elapsed_s > implementation.time_limit_s == 900.0
    assert launch.outcome.ok and launch.run.status == "ok" and launch.run.turns == 9


def test_the_default_repair_window_never_halts_a_long_native_scout(tmp_path: Path) -> None:
    from cuanta.bootstrap import Container

    clock = FixedClock()
    stream = LongScout([result_line("done", cost=0.4, turns=12)], clock=clock, pause_s=0.0)
    runner = FakeRunner(streams={"claude -p": stream}, responses=TURNS)
    container = Container(tmp_path, Config(), runner=runner, clock=clock, home=tmp_path / "home")
    try:
        launcher = container.launcher(ClaudeCodeEngine(runner), container.ledger(), telemetry=False)
        launch = launcher.launch(
            LaunchSpec(
                "mandate",
                "feature",
                str(tmp_path),
                (),
                model=REAL_MODEL,
                verify_commands=("npm run check",),
            ),
            lambda _: None,
        )
        meta = RunReports(container.state_workspace()).meta(launch.run.id) or {}
    finally:
        container.close()
    assert launch.outcome.ok
    assert launch.implementation is not None and launch.implementation.passed
    assert launch.implementation.time_limit_s == 900.0
    implementation = meta.get("implementation")
    assert isinstance(implementation, dict) and implementation["reason"] == ""


def test_the_repair_window_starts_at_the_first_repair_turn() -> None:
    now = [0.0]
    sent: list[str] = []
    session = ImplementationSession(
        COMMANDS,
        checks(),
        CheckSequence(checks(NEW_ERROR), checks(NEW_ERROR)),
        lambda: now[0],
        timeout_s=900.0,
    )
    now[0] = 5_000.0
    session.on_result(result(), lambda text: record_turn(sent, text))
    assert len(sent) == 1 and NEW_ERROR in sent[0]
    assert session.report.reason == ""
    assert [step.state for step in session.report.steps] == ["repairing"]
    now[0] = 5_901.0
    session.on_result(result(0.3, turns=2), lambda text: record_turn(sent, text))
    assert len(sent) == 1 and session.report.repairs == 1
    assert session.report.reason == "repair_time_limit"
    assert [step.state for step in session.report.steps] == ["failed"]
    settled = session.settle(EngineOutcome(0, result(0.3), 2))
    assert settled.result is not None and settled.result.terminal_reason == "repair_time_limit"


def test_the_repair_window_counts_only_the_time_inside_repair_rounds() -> None:
    now = [0.0]
    sent: list[str] = []
    verify = CheckSequence(checks(NEW_ERROR), checks(), checks(NEW_ERROR))
    session = ImplementationSession(
        COMMANDS, checks(), verify, lambda: now[0], timeout_s=900.0, stepped=True
    )

    def send(text: str) -> bool:
        return record_turn(sent, text)

    session.on_result(result(text='{"implementation_steps":["one","two"]}'), send)
    now[0] = 100.0
    session.on_result(result(), send)
    now[0] = 700.0
    session.on_result(result(), send)
    now[0] = 5_000.0
    session.on_result(result(), send)
    assert len(sent) == 4 and "step 2/2" in sent[2] and NEW_ERROR in sent[3]
    assert session.report.reason == "" and session.report.repairs == 2


@pytest.mark.parametrize("ending", ["user_stop", "engine_exit"])
def test_a_session_that_ends_without_a_result_settles_its_steps(
    tmp_path: Path, ending: str
) -> None:
    holder: list[ClaudeCodeEngine] = []
    stream: FakeStream = (
        StoppedByUser([], holder=holder)
        if ending == "user_stop"
        else FakeStream([scout_spawn()], 1, "boom")
    )
    engine = ClaudeCodeEngine(FakeRunner(streams={"claude": stream}, responses=TURNS))
    holder.append(engine)
    clock = FixedClock()
    reports = RunReports(LocalWorkspace(tmp_path))
    session = ImplementationSession(COMMANDS, checks(), CheckSequence(), clock.monotonic)
    launch = launcher_for(engine, clock, session, reports).launch(
        LaunchSpec("mandate", "feature", str(tmp_path), (), model=REAL_MODEL), lambda _: None
    )
    assert launch.run.status == "failed"
    assert launch.implementation is not None
    assert [step.state for step in launch.implementation.steps] == ["stopped"]
    meta = reports.meta("R") or {}
    implementation = meta.get("implementation")
    assert isinstance(implementation, dict)
    assert implementation["steps"] == [{"title": "Implementation", "state": "stopped"}]
    if ending == "user_stop":
        assert launch.implementation.reason == "stopped"
        assert launch.run.end_reason == "cancelled"
    else:
        assert launch.implementation.reason == "engine_exited"
        assert launch.implementation.exit_code == 1
