from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.application.engine_run import EngineLauncher, LaunchSpec
from cuanta.application.implementer import ImplementationSession
from cuanta.application.mandate_flow import MandateOptions
from cuanta.domain.config import layer_from_table, merge
from cuanta.domain.depth import Depth, profile
from cuanta.domain.engine import WALL_LIMIT_SUBTYPE
from cuanta.domain.limits import (
    LimitRequest,
    LimitSettings,
    LimitsMode,
    RunLimits,
    limit_settings,
    limits_message,
    limits_payload,
    resolve_limits,
)
from cuanta.domain.messages import english
from cuanta.ports.system import Completed
from cuanta.tui.i18n import Catalog
from tests.fakes import FakeRunner, FakeStream
from tests.real_run import result_line
from tests.unit.test_engine_profiles import REQUEST
from tests.unit.test_engine_profiles import flow as profile_flow
from tests.unit.test_implementer import COMMANDS, NEW_ERROR, CheckSequence, checks

DEEP = profile(Depth.DEEP, "feature")


def test_a_run_has_no_limits_unless_the_user_sets_one() -> None:
    assert resolve_limits(LimitRequest(), LimitSettings(), DEEP) == RunLimits()
    assert not RunLimits().active
    depth = LimitSettings(LimitsMode.DEPTH)
    assert resolve_limits(LimitRequest(), depth, DEEP) == RunLimits(5.0, 80, 0.0)
    fixed = LimitSettings(LimitsMode.DEPTH, RunLimits(3.0, 0, 10.0))
    assert resolve_limits(LimitRequest(), fixed, DEEP) == RunLimits(3.0, 80, 10.0)
    asked = LimitRequest(budget_usd=500.0, max_turns=100_000, wall_min=30.0)
    assert resolve_limits(asked, LimitSettings(), DEEP) == RunLimits(500.0, 100_000, 30.0)
    assert resolve_limits(asked, LimitSettings(), DEEP).wall_s == 1800.0
    dropped = resolve_limits(LimitRequest(budget_usd=0.0), depth, DEEP)
    assert dropped == RunLimits(0.0, 80, 0.0)
    off = resolve_limits(
        LimitRequest(mode="off"), LimitSettings(LimitsMode.DEPTH, RunLimits(3.0)), DEEP
    )
    assert off == RunLimits()


def test_the_card_says_no_limits_or_lists_the_active_ones() -> None:
    spanish = Catalog("es")
    assert english(limits_message(RunLimits())) == "no limits"
    assert spanish.message(limits_message(RunLimits())) == "sin límites"
    real = RunLimits(500.0, 100_000, 30.0)
    assert english(limits_message(real)) == "limits: spend cap $500.00 · 100000 turns · 30 min"
    assert spanish.message(limits_message(real)) == (
        "límites: tope de gasto $500.00 · 100000 turnos · 30 min"
    )
    assert english(limits_message(RunLimits(max_turns=13))) == "limits: 13 turns"
    assert english(limits_message(RunLimits(max_turns=1))) == "limits: 1 turn"
    assert spanish.message(limits_message(RunLimits(max_turns=1))) == "límites: 1 turno"
    assert limits_payload(RunLimits()) == {"budget_usd": None, "max_turns": None, "wall_min": None}
    assert limits_payload(real) == {"budget_usd": 500.0, "max_turns": 100_000, "wall_min": 30.0}


def test_limits_are_read_from_the_runs_and_limits_tables() -> None:
    table = {
        "runs": {"limits": "depth"},
        "limits": {"budget_usd": 3, "max_turns": 25, "wall_min": 20},
    }
    assert layer_from_table(table) == {
        "limits_mode": "depth",
        "budget_usd": 3.0,
        "max_turns": 25,
        "wall_min": 20.0,
    }
    assert layer_from_table({"runs": {"limits": "always"}}) == {}
    assert layer_from_table({"limits": {"wall_min": -5}}) == {}
    assert (
        layer_from_table({"budget": {"usd": 9}, "limits": {"budget_usd": 3}})["budget_usd"] == 3.0
    )
    assert layer_from_table({"budget": {"usd": 9}})["budget_usd"] == 9.0
    default = merge([])
    assert (default.limits_mode, default.wall_min) == ("off", 0.0)
    assert limit_settings(default) == LimitSettings()
    configured = merge([layer_from_table(table)])
    assert limit_settings(configured) == LimitSettings(LimitsMode.DEPTH, RunLimits(3.0, 25, 20.0))


def test_a_deep_run_without_limits_carries_no_cap_no_turn_rail_and_no_wall(tmp_path: Path) -> None:
    engine = ClaudeCodeEngine(FakeRunner())
    prepared = profile_flow(tmp_path, engine).prepare(REQUEST, 0, MandateOptions(depth="deep"))
    command = engine.command(prepared.launcher.request(prepared.spec, "R", "trace", None))
    assert "--max-budget-usd" not in command and "--max-turns" not in command
    assert command[command.index("--effort") + 1] == "high"
    assert prepared.spec.max_wall_s == 0.0
    assert prepared.limits == RunLimits()


@pytest.mark.parametrize(
    ("settings", "options", "budget", "turns", "wall"),
    [
        (
            LimitSettings(),
            MandateOptions(depth="deep", budget_usd=500.0, max_turns=100_000, max_wall_min=30.0),
            "500.00",
            "100000",
            1800.0,
        ),
        (LimitSettings(LimitsMode.DEPTH), MandateOptions(), "2.00", "40", 0.0),
        (LimitSettings(LimitsMode.DEPTH), MandateOptions(budget_usd=0.0), None, "40", 0.0),
    ],
)
def test_explicit_limits_and_the_depth_mode_reach_the_claude_command(
    tmp_path: Path,
    settings: LimitSettings,
    options: MandateOptions,
    budget: str | None,
    turns: str,
    wall: float,
) -> None:
    engine = ClaudeCodeEngine(FakeRunner())
    prepared = profile_flow(tmp_path, engine, settings).prepare(REQUEST, 0, options)
    command = engine.command(prepared.launcher.request(prepared.spec, "R", "trace", None))
    if budget is None:
        assert "--max-budget-usd" not in command
    else:
        assert command[command.index("--max-budget-usd") + 1] == budget
    assert command[command.index("--max-turns") + 1] == turns
    assert prepared.spec.max_wall_s == wall


@dataclass
class SilentStream(FakeStream):
    gone: threading.Event = field(default_factory=threading.Event)

    def lines(self) -> Iterator[str]:
        self.gone.wait(5)
        yield from ()

    def terminate(self) -> None:
        self.terminated = True
        self.gone.set()


def test_the_wall_limit_halts_a_silent_engine_and_names_its_reason(tmp_path: Path) -> None:
    stream = SilentStream([])
    launcher = EngineLauncher(
        ClaudeCodeEngine(FakeRunner(streams={"claude": stream})),
        MemoryLedger(),
        FixedClock(),
        lambda: "RUN1",
        lambda size: b"\x01" * size,
        "shop",
        4318,
        None,
    )
    launch = launcher.launch(
        LaunchSpec("mandate", "go", str(tmp_path), (), max_wall_s=0.05), lambda _: None
    )
    assert stream.terminated
    assert launch.run.end_reason == WALL_LIMIT_SUBTYPE and launch.run.status == "failed"
    assert launch.run.max_wall_s == 0.05 and launch.run.partial
    assert launch.outcome.result is not None
    assert launch.outcome.result.terminal_reason == "max_wall"


class RepairStream(FakeStream):
    def lines(self) -> Iterator[str]:
        for index, line in enumerate(self.output):
            if index:
                assert len(self.sent) == 1 and NEW_ERROR in self.sent[0]
            yield line


def test_a_run_without_limits_repairs_and_ends_by_its_own_result_with_every_rail_off(
    tmp_path: Path,
) -> None:
    stream = RepairStream(
        [result_line("first", cost=None), result_line("fixed", cost=None, turns=2)]
    )
    runner = FakeRunner(
        streams={"claude": stream}, responses={"claude --help": Completed(0, "--input-format", "")}
    )
    session = ImplementationSession(
        COMMANDS, checks(), CheckSequence(checks(NEW_ERROR), checks()), lambda: 0.0
    )
    launcher = EngineLauncher(
        ClaudeCodeEngine(runner),
        MemoryLedger(),
        FixedClock(),
        lambda: "R",
        lambda size: b"x" * size,
        "project",
        4318,
        None,
        implementer=lambda _: session,
    )
    launch = launcher.launch(LaunchSpec("mandate", "fix", str(tmp_path), ()), lambda _: None)
    command = runner.calls[-1]
    assert "--max-budget-usd" not in command and "--max-turns" not in command
    assert launch.implementation is not None
    assert launch.implementation.passed and launch.implementation.repairs == 1
    assert launch.outcome.ok
    assert (launch.run.cap_usd, launch.run.max_turns, launch.run.max_wall_s) == (0.0, 0, 0.0)
