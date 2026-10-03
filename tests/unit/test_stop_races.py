from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.codex import CodexEngine
from cuanta.adapters.engines.opencode import OpenCodeEngine
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.engine_run import EngineLauncher, Launch, LaunchSpec
from cuanta.application.implementer import ImplementationSession, Verify
from cuanta.cli.fmt import usd
from cuanta.domain.engine import (
    CANCELLED_SUBTYPE,
    WALL_LIMIT_SUBTYPE,
    EngineEvent,
    EngineOutcome,
    EngineRequest,
    RunResult,
)
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import english
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.role_handoff import VerifyResult
from cuanta.domain.stop_reason import stop_message
from cuanta.ports.engine import Engine
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream
from tests.real_run import REAL_MODEL, assistant_line, init_line, result_line
from tests.unit.test_implementer import COMMANDS, NEW_ERROR, CheckSequence, checks

TURNS = {"claude --help": Completed(0, "--input-format", "")}
WALL_S = 1800.0
CODEX_MODEL = "gpt-test"
PRICES = PriceTable({CODEX_MODEL: Price(2.0, 10.0, 2.5, 0.2)})


@dataclass
class DeadPipe(FakeStream):
    def send(self, text: str) -> bool:
        return False if self.terminated else super().send(text)

    def wait(self) -> int:
        return 1 if self.terminated else self.code


@dataclass
class Scripted(DeadPipe):
    steps: list[str | Callable[[], None]] = field(default_factory=list)

    def lines(self) -> Iterator[str]:
        for step in self.steps:
            if isinstance(step, str):
                yield step
            else:
                step()


@dataclass
class Lingering:
    stopped: threading.Event = field(default_factory=threading.Event)

    @property
    def name(self) -> str:
        return "lingering"

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "1"

    def missing_flags(self) -> tuple[str, ...]:
        return ()

    def cancel(self) -> None:
        self.stopped.set()

    def command(self, request: EngineRequest) -> list[str]:
        return ["lingering"]

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        answer = RunResult(True, "success", 0.1, 1, "S", text="done")
        on_event(answer)
        self.stopped.wait(0.5)
        cut = self.stopped.is_set()
        return EngineOutcome(1 if cut else 0, answer, 0, cancelled=cut)


@dataclass
class Governed(Lingering):
    launchers: list[EngineLauncher] = field(default_factory=list)

    def accepts_turns(self) -> bool:
        return True

    def send_turn(self, text: str) -> bool:
        return True

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        assert self.launchers[0].send_turn("finish now")
        return super().run(request, on_event)


def launcher_for(
    engine: Engine,
    session: ImplementationSession | None = None,
    ledger: MemoryLedger | None = None,
) -> EngineLauncher:
    return EngineLauncher(
        engine,
        ledger or MemoryLedger(),
        FixedClock(),
        lambda: "R",
        lambda size: b"x" * size,
        "project",
        4318,
        None,
        prices=PRICES,
        implementer=(lambda _: session) if session is not None else None,
    )


def claude(stream: FakeStream) -> ClaudeCodeEngine:
    return ClaudeCodeEngine(FakeRunner(streams={"claude": stream}, responses=TURNS))


def session_for(verify: Verify) -> ImplementationSession:
    return ImplementationSession(COMMANDS, checks(), verify, FixedClock().monotonic)


def native(launcher: EngineLauncher, cwd: Path, model: str = REAL_MODEL) -> Launch:
    return launcher.launch(
        LaunchSpec("mandate", "feature", str(cwd), (), model=model, max_wall_s=WALL_S),
        lambda _: None,
    )


def stop_line(launch: Launch) -> str:
    report = launch.implementation
    return english(stop_message(launch.run, report.payload() if report is not None else None))


def first_result() -> list[str | Callable[[], None]]:
    return [init_line(), assistant_line("m1"), result_line("all done", cost=0.3, turns=2)]


@pytest.mark.parametrize("verified", [True, False])
def test_a_wall_halt_after_the_final_result_keeps_the_finished_run(
    tmp_path: Path, verified: bool
) -> None:
    holder: list[ClaudeCodeEngine] = []
    stream = Scripted([], steps=[*first_result(), lambda: holder[0].halt(WALL_LIMIT_SUBTYPE)])
    engine = claude(stream)
    holder.append(engine)
    session = session_for(CheckSequence(checks())) if verified else None
    launch = native(launcher_for(engine, session), tmp_path)
    assert not stream.terminated
    assert (launch.run.status, launch.run.end_reason) == ("ok", "success")
    assert launch.run.cost_usd == 0.3 and not launch.run.partial
    assert stop_line(launch) == "the agent finished"
    if verified:
        assert launch.implementation is not None and launch.implementation.passed


def test_a_wall_halt_during_a_green_verification_keeps_the_verified_run(tmp_path: Path) -> None:
    holder: list[ClaudeCodeEngine] = []

    def verify(commands: Sequence[str], stopped: Callable[[], bool]) -> tuple[VerifyResult, ...]:
        holder[0].halt(WALL_LIMIT_SUBTYPE)
        return checks()

    stream = Scripted([], steps=first_result())
    engine = claude(stream)
    holder.append(engine)
    launch = native(launcher_for(engine, session_for(verify)), tmp_path)
    assert not stream.terminated
    assert launch.implementation is not None and launch.implementation.passed
    assert (launch.run.status, launch.run.end_reason) == ("ok", "success")
    assert stop_line(launch) == "the agent finished"


def test_the_launcher_disarms_the_wall_once_the_final_result_needs_no_turn(
    tmp_path: Path,
) -> None:
    engine = Lingering()
    launch = launcher_for(engine).launch(
        LaunchSpec("mandate", "go", str(tmp_path), (), max_wall_s=0.2), lambda _: None
    )
    assert not engine.stopped.is_set()
    assert (launch.run.status, launch.run.end_reason) == ("ok", "success")


def governor_turn(launchers: list[EngineLauncher]) -> Callable[[], None]:
    def send() -> None:
        assert launchers[0].send_turn("finish now")

    return send


@pytest.mark.parametrize("verified", [True, False])
def test_a_governor_turn_answered_before_the_result_still_settles_the_run(
    tmp_path: Path, verified: bool
) -> None:
    holder: list[ClaudeCodeEngine] = []
    launchers: list[EngineLauncher] = []

    def verify(commands: Sequence[str], stopped: Callable[[], bool]) -> tuple[VerifyResult, ...]:
        holder[0].halt(WALL_LIMIT_SUBTYPE)
        return checks()

    steps: list[str | Callable[[], None]] = [
        init_line(),
        assistant_line("m1"),
        governor_turn(launchers),
        result_line("all done", cost=0.3, turns=2),
    ]
    if not verified:
        steps.append(lambda: holder[0].halt(WALL_LIMIT_SUBTYPE))
    stream = Scripted([], steps=steps)
    engine = claude(stream)
    holder.append(engine)
    launchers.append(launcher_for(engine, session_for(verify) if verified else None))
    launch = launchers[0].launch(
        LaunchSpec(
            "mandate",
            "feature",
            str(tmp_path),
            (),
            model=REAL_MODEL,
            max_wall_s=WALL_S,
            steer=True,
        ),
        lambda _: None,
    )
    assert len(stream.sent) == 1
    assert not stream.terminated
    assert (launch.run.status, launch.run.end_reason) == ("ok", "success")
    assert not launch.run.partial
    assert stop_line(launch) == "the agent finished"
    if verified:
        assert launch.implementation is not None and launch.implementation.passed


def test_the_launcher_disarms_the_wall_after_a_result_that_answered_a_governor_turn(
    tmp_path: Path,
) -> None:
    engine = Governed()
    launcher = launcher_for(engine)
    engine.launchers.append(launcher)
    launch = launcher.launch(
        LaunchSpec("mandate", "go", str(tmp_path), (), max_wall_s=0.2), lambda _: None
    )
    assert not engine.stopped.is_set()
    assert (launch.run.status, launch.run.end_reason) == ("ok", "success")


def test_the_launcher_keeps_the_wall_armed_while_a_repair_turn_is_pending(tmp_path: Path) -> None:
    engine = Governed()
    launcher = launcher_for(engine, session_for(CheckSequence(checks(NEW_ERROR))))
    engine.launchers.append(launcher)
    launch = launcher.launch(
        LaunchSpec("mandate", "go", str(tmp_path), (), max_wall_s=0.2), lambda _: None
    )
    assert engine.stopped.is_set()
    assert launch.run.status == "failed"


def test_a_repair_turn_sent_after_a_governor_turn_keeps_the_run_open_for_the_wall(
    tmp_path: Path,
) -> None:
    holder: list[ClaudeCodeEngine] = []
    launchers: list[EngineLauncher] = []

    def verify(commands: Sequence[str], stopped: Callable[[], bool]) -> tuple[VerifyResult, ...]:
        holder[0].halt(WALL_LIMIT_SUBTYPE)
        return checks(NEW_ERROR)

    stream = Scripted(
        [],
        steps=[
            init_line(),
            assistant_line("m1"),
            governor_turn(launchers),
            result_line("all done", cost=0.3, turns=2),
        ],
    )
    engine = claude(stream)
    holder.append(engine)
    launchers.append(launcher_for(engine, session_for(verify)))
    launch = native(launchers[0], tmp_path)
    assert len(stream.sent) == 2 and NEW_ERROR in stream.sent[1]
    assert stream.terminated
    assert launch.implementation is not None
    assert launch.implementation.reason == "wall_limit"
    assert (launch.run.status, launch.run.end_reason) == ("failed", WALL_LIMIT_SUBTYPE)
    assert stop_line(launch) == "stopped by the time limit of 30 min"


def test_a_wall_halt_during_a_failing_verification_stops_the_repair_with_the_wall_reason(
    tmp_path: Path,
) -> None:
    holder: list[ClaudeCodeEngine] = []

    def verify(commands: Sequence[str], stopped: Callable[[], bool]) -> tuple[VerifyResult, ...]:
        holder[0].halt(WALL_LIMIT_SUBTYPE)
        return checks(NEW_ERROR)

    stream = Scripted([], steps=first_result())
    engine = claude(stream)
    holder.append(engine)
    launch = native(launcher_for(engine, session_for(verify)), tmp_path)
    assert stream.terminated
    assert launch.implementation is not None
    assert launch.implementation.reason == "wall_limit"
    assert (launch.run.status, launch.run.end_reason) == ("failed", WALL_LIMIT_SUBTYPE)
    assert launch.run.partial
    assert stop_line(launch) == "stopped by the time limit of 30 min"


def test_a_halt_that_cut_the_engine_before_its_result_still_stops_the_run(
    tmp_path: Path,
) -> None:
    holder: list[ClaudeCodeEngine] = []
    stream = Scripted(
        [],
        steps=[
            init_line(),
            assistant_line("m1"),
            lambda: holder[0].halt(WALL_LIMIT_SUBTYPE),
            result_line("late", cost=0.3, turns=2),
        ],
    )
    engine = claude(stream)
    holder.append(engine)
    launch = native(launcher_for(engine), tmp_path)
    assert stream.terminated
    assert (launch.run.status, launch.run.end_reason) == ("failed", WALL_LIMIT_SUBTYPE)


@dataclass
class Silent(FakeStream):
    gone: threading.Event = field(default_factory=threading.Event)

    def lines(self) -> Iterator[str]:
        self.gone.wait(0.5)
        yield from ()

    def terminate(self) -> None:
        self.terminated = True
        self.gone.set()


@dataclass
class Spawning(FakeRunner):
    holder: list[ClaudeCodeEngine] = field(default_factory=list)

    def stream(
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        unset: Sequence[str] = (),
        keep_stdin: bool = False,
    ) -> FakeStream:
        self.holder[0].halt(WALL_LIMIT_SUBTYPE)
        return super().stream(args, cwd, env, stdin_text, unset, keep_stdin)


def test_a_wall_halt_that_lands_while_the_engine_starts_stops_it_at_once(tmp_path: Path) -> None:
    stream = Silent([])
    runner = Spawning(streams={"claude": stream})
    engine = ClaudeCodeEngine(runner)
    runner.holder.append(engine)
    launch = native(launcher_for(engine), tmp_path)
    assert stream.terminated
    assert (launch.run.status, launch.run.end_reason) == ("failed", WALL_LIMIT_SUBTYPE)


def test_a_user_stop_during_a_failing_verification_is_the_user_stop(tmp_path: Path) -> None:
    holder: list[ClaudeCodeEngine] = []

    def verify(commands: Sequence[str], stopped: Callable[[], bool]) -> tuple[VerifyResult, ...]:
        holder[0].cancel()
        return checks(NEW_ERROR)

    stream = Scripted([], steps=first_result())
    engine = claude(stream)
    holder.append(engine)
    launch = native(launcher_for(engine, session_for(verify)), tmp_path)
    assert launch.implementation is not None and launch.implementation.reason == "stopped"
    assert (launch.run.status, launch.run.end_reason) == ("failed", CANCELLED_SUBTYPE)
    assert stop_line(launch) == "stopped by the user"


def test_a_user_stop_during_a_repair_turn_keeps_the_result_and_is_the_user_stop(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(responses=TURNS)
    engine = ClaudeCodeEngine(runner)
    stream = Scripted(
        [], steps=[*first_result(), assistant_line("m2"), engine.cancel, assistant_line("m3")]
    )
    runner.streams["claude"] = stream
    launch = native(launcher_for(engine, session_for(CheckSequence(checks(NEW_ERROR)))), tmp_path)
    assert len(stream.sent) == 1 and NEW_ERROR in stream.sent[0]
    assert launch.implementation is not None and launch.implementation.reason == "stopped"
    assert [step.state for step in launch.implementation.steps] == ["stopped"]
    assert (launch.run.status, launch.run.end_reason) == ("failed", CANCELLED_SUBTYPE)
    assert launch.run.partial
    assert stop_line(launch) == "stopped by the user"


def test_a_user_stop_during_a_verification_that_passes_is_the_user_stop(tmp_path: Path) -> None:
    holder: list[ClaudeCodeEngine] = []

    def verify(commands: Sequence[str], stopped: Callable[[], bool]) -> tuple[VerifyResult, ...]:
        holder[0].cancel()
        return checks()

    stream = Scripted([], steps=first_result())
    engine = claude(stream)
    holder.append(engine)
    launch = native(launcher_for(engine, session_for(verify)), tmp_path)
    assert launch.implementation is not None and launch.implementation.passed
    assert (launch.run.status, launch.run.end_reason) == ("failed", CANCELLED_SUBTYPE)
    assert stop_line(launch) == "stopped by the user"


def codex_lines(
    *cut: Callable[[], None], completed: bool = False
) -> list[str | Callable[[], None]]:
    steps: list[str | Callable[[], None]] = [
        json.dumps({"type": "thread.started", "thread_id": "T1"}),
        json.dumps({"type": "item.completed", "item": {"type": "command_execution", "id": "c1"}}),
    ]
    if completed:
        steps.append(
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 1_000, "output_tokens": 100}}
            )
        )
    steps.extend(cut)
    steps.append(
        json.dumps({"type": "item.completed", "item": {"type": "command_execution", "id": "c2"}})
    )
    return steps


def recorded(ledger: MemoryLedger) -> Callable[[], None]:
    def add() -> None:
        ledger.add_events(
            [
                LedgerEvent(
                    run_id="R",
                    source="codex_otlp",
                    kind="sse_event:response.completed",
                    model=CODEX_MODEL,
                    input_tokens=200_000,
                    output_tokens=4_000,
                    ts="2026-10-02T00:00:01Z",
                )
            ]
        )

    return add


def codex_launch(tmp_path: Path, ledger: MemoryLedger, engine: CodexEngine) -> Launch:
    return native(launcher_for(engine, ledger=ledger), tmp_path, CODEX_MODEL)


@pytest.mark.parametrize("cut", ["wall", "user"])
def test_a_codex_run_cut_before_its_turn_ended_prices_its_telemetry_as_partial(
    tmp_path: Path, cut: str
) -> None:
    ledger = MemoryLedger()
    holder: list[CodexEngine] = []

    def stop() -> None:
        if cut == "wall":
            holder[0].halt(WALL_LIMIT_SUBTYPE)
        else:
            holder[0].cancel()

    stream = Scripted([], steps=codex_lines(recorded(ledger), stop))
    engine = CodexEngine(FakeRunner(streams={"codex": stream}))
    holder.append(engine)
    launch = codex_launch(tmp_path, ledger, engine)
    run = launch.run
    assert launch.outcome.result is not None and launch.outcome.result.partial
    assert run.partial and run.cost_source == "estimated"
    assert run.cost_usd == pytest.approx(0.44)
    assert usd(run.cost_usd, run.cost_source, run.partial) == "$0.4400 (estimated, partial)"
    expected = WALL_LIMIT_SUBTYPE if cut == "wall" else CANCELLED_SUBTYPE
    assert (run.status, run.end_reason) == ("failed", expected)
    line = "stopped by the time limit of 30 min" if cut == "wall" else "stopped by the user"
    assert english(stop_message(run)) == line


def test_a_codex_run_cut_without_usage_has_no_cost_rather_than_zero(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    holder: list[CodexEngine] = []
    stream = Scripted([], steps=codex_lines(lambda: holder[0].halt(WALL_LIMIT_SUBTYPE)))
    engine = CodexEngine(FakeRunner(streams={"codex": stream}))
    holder.append(engine)
    run = codex_launch(tmp_path, ledger, engine).run
    assert run.partial and run.cost_usd is None
    assert usd(run.cost_usd, run.cost_source, run.partial) == "n/a"


def test_a_codex_run_cut_after_its_turn_completed_is_not_partial(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    holder: list[CodexEngine] = []
    stream = Scripted(
        [], steps=codex_lines(lambda: holder[0].halt(WALL_LIMIT_SUBTYPE), completed=True)
    )
    engine = CodexEngine(FakeRunner(streams={"codex": stream}))
    holder.append(engine)
    run = codex_launch(tmp_path, ledger, engine).run
    assert not run.partial and run.end_reason == WALL_LIMIT_SUBTYPE
    assert run.cost_usd == pytest.approx(0.003)


def test_an_opencode_run_stopped_by_the_user_keeps_its_step_costs_as_partial(
    tmp_path: Path,
) -> None:
    step = json.dumps(
        {"type": "step_finish", "sessionID": "s", "part": {"cost": 0.02, "tokens": {"input": 5}}}
    )
    runner = FakeRunner()
    engine = OpenCodeEngine(runner)
    runner.streams["opencode"] = Scripted([], steps=[step, engine.cancel, step])
    run = native(launcher_for(engine), tmp_path, "opencode-model").run
    assert run.partial and run.cost_usd == pytest.approx(0.02)
    assert (run.status, run.end_reason) == ("failed", CANCELLED_SUBTYPE)
    assert usd(run.cost_usd, run.cost_source, run.partial) == "$0.0200 (partial)"


def test_a_wall_longer_than_the_timer_maximum_never_crashes_the_timer_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crashes: list[type[BaseException]] = []
    crashed = threading.Event()

    def hook(args: threading.ExceptHookArgs) -> None:
        if isinstance(args.thread, threading.Timer):
            crashes.append(args.exc_type)
            crashed.set()

    def pause() -> None:
        crashed.wait(0.3)

    monkeypatch.setattr(threading, "excepthook", hook)
    stream = Scripted([], steps=[pause, result_line("done")])
    launch = launcher_for(claude(stream)).launch(
        LaunchSpec("mandate", "go", str(tmp_path), (), max_wall_s=threading.TIMEOUT_MAX * 2),
        lambda _: None,
    )
    assert crashes == []
    assert (launch.run.status, launch.run.end_reason) == ("ok", "success")
