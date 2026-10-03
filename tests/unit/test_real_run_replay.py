from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.claude_stream import parse_line
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.prices import load_prices
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.engine_run import EngineLauncher, Launch, LaunchSpec
from cuanta.application.results import ResultQuery, run_markdown
from cuanta.cli.fmt import turn_count, usd
from cuanta.domain.engine import EngineEvent, RunResult, ToolCall
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.received import Received
from cuanta.ports.ledger import EventQuery
from tests.fakes import FakeRunner, FakeStream
from tests.real_run import (
    REAL_MODEL,
    REAL_RUN_ID,
    SESSION,
    assistant_line,
    init_line,
    scout_spawn,
    usage,
)
from tests.support import FIXTURES

MAIN = {"input": 10, "cache_read": 170_000, "cache_write": 6_000, "output": 1_324}
SCOUT = {"input": 6, "cache_read": 69_000, "cache_write": 1_200, "output": 164}
SCOUT_REQUESTS = 162
REPLAY_COST = 4.014408


def tokens(shape: dict[str, int]) -> dict[str, int]:
    return usage(shape["input"], shape["output"], shape["cache_read"], shape["cache_write"])


def recorded_stream() -> list[str]:
    lines = [init_line()]
    lines.append(
        assistant_line(
            "msg_main_1", tool=("Read", "toolu_main_1", {"file_path": "a.py"}), tokens=tokens(MAIN)
        )
    )
    lines.append(
        assistant_line(
            "msg_main_2", tool=("Read", "toolu_main_2", {"file_path": "b.py"}), tokens=tokens(MAIN)
        )
    )
    spawn = json.loads(scout_spawn("msg_main_3"))
    spawn["message"]["usage"] = tokens(MAIN)
    lines.append(json.dumps(spawn))
    lines.extend(
        assistant_line(
            f"msg_scout_{index}",
            parent="toolu_scout",
            tool=("Read", f"toolu_scout_{index}", {"file_path": f"src/{index}.py"}),
            tokens=tokens(SCOUT),
        )
        for index in range(SCOUT_REQUESTS)
    )
    return lines


def recorded_telemetry() -> list[LedgerEvent]:
    main = [
        LedgerEvent(
            run_id=REAL_RUN_ID,
            source="claude_code",
            session_id=SESSION,
            kind="api_request",
            agent="",
            model=REAL_MODEL,
            input_tokens=MAIN["input"],
            output_tokens=MAIN["output"],
            cache_read_tokens=MAIN["cache_read"],
            cache_write_tokens=MAIN["cache_write"],
            ts="2026-10-02T16:12:00Z",
        )
        for _ in range(3)
    ]
    scout = [
        LedgerEvent(
            run_id=REAL_RUN_ID,
            source="claude_code",
            session_id=SESSION,
            kind="api_request",
            agent="scout",
            model=REAL_MODEL,
            input_tokens=SCOUT["input"],
            output_tokens=SCOUT["output"],
            cache_read_tokens=SCOUT["cache_read"],
            cache_write_tokens=SCOUT["cache_write"],
            ts="2026-10-02T16:20:00Z",
        )
        for _ in range(SCOUT_REQUESTS)
    ]
    return [*main, *scout]


@dataclass
class Halted:
    engine: list[ClaudeCodeEngine] = field(default_factory=list)
    calls: int = 0

    def __call__(self, event: EngineEvent) -> None:
        if isinstance(event, ToolCall):
            self.calls += 1
            if self.calls == 165:
                self.engine[0].cancel()


def launch_recorded(ledger: MemoryLedger) -> Launch:
    stream = FakeStream(recorded_stream(), code=1)
    engine = ClaudeCodeEngine(FakeRunner(streams={"claude": stream}))
    halted = Halted([engine])
    launcher = EngineLauncher(
        engine,
        ledger,
        FixedClock(),
        lambda: REAL_RUN_ID,
        lambda size: b"\x01" * size,
        "backend",
        4318,
        None,
        prices=load_prices(),
    )
    spec = LaunchSpec(
        kind="mandate",
        prompt="mandate",
        cwd=".",
        allowed_tools=(),
        model=REAL_MODEL,
        pure=True,
        max_turns=100_000,
        max_budget_usd=500.0,
    )
    return launcher.launch(spec, halted)


def test_the_recorded_run_cut_before_its_result_reports_its_partial_cost_and_turns(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    ledger.add_events(recorded_telemetry())
    launched = launch_recorded(ledger)
    run = launched.run
    assert run.cost_usd == pytest.approx(REPLAY_COST)
    assert run.cost_source == "estimated" and run.partial is True
    assert run.turns == 3 and run.status == "failed"
    assert launched.outcome.tool_calls == 165
    assert ledger.get_run(REAL_RUN_ID) == run
    assert usd(run.cost_usd, run.cost_source, run.partial) == "$4.01 (estimated, partial)"
    assert turn_count(run.turns, run.max_turns, run.partial) == "3/100000 (partial)"
    view = ResultQuery(LocalWorkspace(tmp_path), ledger, lambda: date(2026, 10, 2)).load(
        REAL_RUN_ID
    )
    assert view is not None
    markdown = run_markdown(view)
    assert "cost $4.01 (estimated, partial)" in markdown
    assert "- Turns: 3/100000 (partial)" in markdown
    by_agent: dict[str, int] = {}
    for event in ledger.events(EventQuery(run_id=REAL_RUN_ID)):
        if event.kind == "api_request":
            name = event.agent or "main"
            by_agent[name] = by_agent.get(name, 0) + event.total_tokens
    assert by_agent == {"main": 532_002, "scout": 11_399_940}


def test_without_telemetry_the_streamed_usage_prices_the_cut_run() -> None:
    ledger = MemoryLedger()
    launched = launch_recorded(ledger)
    run = launched.run
    assert run.cost_usd == pytest.approx(REPLAY_COST)
    assert run.cost_source == "estimated" and run.partial is True and run.turns == 3
    rows = [
        event
        for event in ledger.events(EventQuery(run_id=REAL_RUN_ID))
        if event.kind == "result_usage"
    ]
    assert len(rows) == 1 and rows[0].source == "claude_stream" and rows[0].model == REAL_MODEL
    assert (
        rows[0].input_tokens,
        rows[0].cache_read_tokens,
        rows[0].cache_write_tokens,
        rows[0].output_tokens,
    ) == (1_002, 11_688_000, 212_400, 30_540)


def test_stream_turns_and_tokens_match_the_engine_totals_on_the_live_capture() -> None:
    received = Received()
    final: RunResult | None = None
    for line in (
        (FIXTURES / "engines" / "claude_stream.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        for event in parse_line(line):
            if isinstance(event, RunResult):
                final = event
            else:
                received.add(event)
    assert final is not None
    assert received.turns == final.num_turns == 2 and received.requests == 2
    streamed = received.usage[0]
    reported = final.models[0]
    assert (streamed.input_tokens, streamed.cache_read_tokens, streamed.cache_write_tokens) == (
        reported.input_tokens,
        reported.cache_read_tokens,
        reported.cache_write_tokens,
    )
    assert streamed.output_tokens < reported.output_tokens


def test_a_run_that_ended_normally_is_not_partial(tmp_path: Path) -> None:
    from tests.real_run import result_line

    stream = FakeStream([init_line(), assistant_line("msg_1"), result_line(cost=0.3, turns=1)])
    launcher = EngineLauncher(
        ClaudeCodeEngine(FakeRunner(streams={"claude": stream})),
        MemoryLedger(),
        FixedClock(),
        lambda: "R",
        lambda size: b"\x01" * size,
        "backend",
        4318,
        None,
        prices=load_prices(),
    )
    run = launcher.launch(
        LaunchSpec(kind="mandate", prompt="x", cwd=str(tmp_path), allowed_tools=()), lambda _: None
    ).run
    assert run.partial is False and run.cost_usd == 0.3 and run.cost_source == "reported"
    assert usd(run.cost_usd, run.cost_source, run.partial) == "$0.3000"
