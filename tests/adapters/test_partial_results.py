from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.storage.migrations import LATEST_VERSION, MIGRATIONS
from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.domain.engine import (
    GOVERNOR_STOP_SUBTYPE,
    WALL_LIMIT_SUBTYPE,
    EngineEvent,
    EngineRequest,
    RunResult,
    ToolCall,
)
from cuanta.domain.ledger import Run
from cuanta.ports.system import Completed
from tests.adapters.test_claude_turns import ASSISTANT, HELP_WITH_TURNS, RESULT
from tests.fakes import FakeRunner, FakeStream
from tests.real_run import REAL_MODEL, assistant_line, init_line


def test_a_halted_claude_run_marks_its_synthesized_result_partial() -> None:
    engine = ClaudeCodeEngine(
        FakeRunner(
            streams={
                "claude": FakeStream(
                    [
                        init_line(),
                        assistant_line("m1", tool=("Read", "t1", {"file_path": "a.py"})),
                        assistant_line("m2"),
                    ]
                )
            }
        )
    )

    def halt(event: EngineEvent) -> None:
        if isinstance(event, ToolCall):
            engine.halt(GOVERNOR_STOP_SUBTYPE)

    outcome = engine.run(EngineRequest(prompt="x", cwd="", env={}, model=REAL_MODEL), halt)
    assert outcome.result is not None and outcome.result.partial is True
    assert outcome.result.subtype == GOVERNOR_STOP_SUBTYPE and outcome.result.cost_usd is None


def test_a_halt_that_lands_while_the_stream_waits_still_names_its_reason() -> None:
    class Waiting(FakeStream):
        def lines(self) -> Iterator[str]:
            yield init_line()
            holder[0].halt(WALL_LIMIT_SUBTYPE)

    holder: list[ClaudeCodeEngine] = []
    engine = ClaudeCodeEngine(FakeRunner(streams={"claude": Waiting([])}))
    holder.append(engine)
    outcome = engine.run(EngineRequest(prompt="x", cwd="", env={}), lambda _: None)
    assert outcome.result is not None
    assert outcome.result.subtype == WALL_LIMIT_SUBTYPE
    assert outcome.result.terminal_reason == "max_wall" and outcome.result.partial


def test_a_session_cut_after_a_follow_up_turn_marks_its_last_result_partial() -> None:
    runner = FakeRunner(
        responses={"claude --help": Completed(0, HELP_WITH_TURNS, "")},
        streams={"claude -p": FakeStream([ASSISTANT, RESULT, ASSISTANT])},
    )
    engine = ClaudeCodeEngine(runner)

    def repair(event: EngineEvent) -> None:
        if isinstance(event, RunResult):
            engine.send_turn("repair")

    outcome = engine.run(
        EngineRequest(prompt="go", cwd=".", env={}, stream_input=True, continue_results=True),
        repair,
    )
    assert outcome.result is not None and outcome.result.partial is True
    assert (outcome.result.cost_usd, outcome.result.num_turns) == (0.01, 2)


def test_partial_runs_round_trip_as_booleans(tmp_path: Path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    try:
        ledger.add_run(
            Run(
                "P",
                "mandate",
                status="failed",
                cost_usd=4.01,
                cost_source="estimated",
                turns=3,
                max_turns=100_000,
                partial=True,
                max_wall_s=1800.0,
            )
        )
        stored = ledger.get_run("P")
        assert stored is not None and stored.partial is True and type(stored.partial) is bool
        assert stored.max_wall_s == 1800.0
        ledger.update_run(Run("P", "mandate", status="failed", partial=False))
        updated = ledger.get_run("P")
        assert updated is not None and updated.partial is False
    finally:
        ledger.close()


def test_the_migration_adds_the_partial_and_wall_columns(tmp_path: Path) -> None:
    path = tmp_path / "v13.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        for version, statements in enumerate(MIGRATIONS[:13], start=1):
            connection.executescript(statements)
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
        connection.execute("INSERT INTO runs(id, kind) VALUES ('R', 'mandate')")
    upgraded = SqliteLedger(path)
    try:
        assert upgraded.schema_version() == LATEST_VERSION
        run = upgraded.get_run("R")
        assert run is not None and run.partial is False and run.max_wall_s == 0.0
    finally:
        upgraded.close()
