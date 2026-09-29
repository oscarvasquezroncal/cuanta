from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import (
    REQUIRED_FLAGS,
    TURN_FLAGS,
    ClaudeCodeEngine,
    build_command,
    user_turn,
)
from cuanta.adapters.engines.codex import CodexEngine
from cuanta.adapters.engines.opencode import OpenCodeEngine
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.domain.engine import EngineEvent, EngineOutcome, EngineRequest, RunResult, ToolCall
from cuanta.ports.engine import TurnInput
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream

HELP_WITH_TURNS = '--print --input-format <format> "text" (default), or "stream-json"'
FINISH = "termina ahora: aplica lo que está completo"
ASSISTANT = json.dumps(
    {
        "type": "assistant",
        "message": {
            "id": "m1",
            "model": "claude-sonnet-5",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.txt"}}
            ],
        },
    }
)
RESULT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": 0.01,
        "num_turns": 2,
        "session_id": "s",
        "result": "FINISHED",
    }
)
LATE = json.dumps(
    {
        "type": "result",
        "subtype": "error_max_budget_usd",
        "is_error": True,
        "total_cost_usd": 0.05,
        "num_turns": 3,
        "session_id": "s",
        "result": "",
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 40, "outputTokens": 9, "costUSD": 0.05}},
    }
)
FAILED = json.dumps(
    {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "total_cost_usd": 0.004,
        "num_turns": 1,
        "session_id": "s",
        "result": "",
    }
)
CHILD = """
import json, sys
if "--help" in sys.argv:
    print("--input-format <format>")
    sys.exit(0)
first = json.loads(sys.stdin.readline())
print(json.dumps({"type": "assistant", "message": {"id": "m1", "model": "sonnet",
    "usage": {"input_tokens": 1}, "content": [{"type": "tool_use", "id": "t1",
    "name": "Read", "input": {"file_path": "a.txt"}}]}}), flush=True)
turn = json.loads(sys.stdin.readline())
text = first["message"]["content"][0]["text"] + " | " + turn["message"]["content"][0]["text"]
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
    "total_cost_usd": 0.01, "num_turns": 2, "session_id": "s", "result": text}), flush=True)
sys.exit(0 if sys.stdin.read() == "" else 3)
"""


def turn_text(line: str) -> str:
    data = json.loads(line)
    assert data["type"] == "user" and data["message"]["role"] == "user"
    blocks = data["message"]["content"]
    assert len(blocks) == 1 and blocks[0]["type"] == "text"
    text = blocks[0]["text"]
    assert isinstance(text, str)
    return text


def test_a_steered_request_reads_stream_json_from_stdin() -> None:
    plain = EngineRequest(prompt="go", cwd=".", env={}, model="sonnet", max_budget_usd=0.5)
    legacy = build_command(("claude",), plain)
    command = build_command(("claude",), replace(plain, stream_input=True))
    assert "--input-format" not in legacy
    assert command[:9] == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
    ]
    assert command[:4] + command[6:] == legacy


def test_the_prompt_is_one_stream_json_user_message_and_plain_text_stays_for_the_rest() -> None:
    engine = ClaudeCodeEngine(
        FakeRunner(responses={"claude --help": Completed(0, HELP_WITH_TURNS, "")})
    )
    text = "está listo\nsí"
    steered = EngineRequest(prompt=text, cwd=".", env={}, stream_input=True)
    framed = engine.stdin_text(steered)
    assert framed == user_turn(text)
    assert framed.endswith("\n") and framed.count("\n") == 1 and framed.isascii()
    assert turn_text(framed) == text
    assert engine.stdin_text(EngineRequest(prompt="go", cwd=".", env={})) == "go"
    assert ClaudeCodeEngine(FakeRunner()).stdin_text(steered) == text


def test_turn_input_is_read_from_the_installed_help_and_never_required() -> None:
    assert "--input-format" in TURN_FLAGS
    assert not set(TURN_FLAGS) & set(REQUIRED_FLAGS)
    with_turns = FakeRunner(responses={"claude --help": Completed(0, HELP_WITH_TURNS, "")})
    without = FakeRunner(responses={"claude --help": Completed(0, "--print --verbose", "")})
    assert ClaudeCodeEngine(with_turns).accepts_turns()
    assert not ClaudeCodeEngine(without).accepts_turns()
    assert "--input-format" not in ClaudeCodeEngine(without).missing_flags()
    for other in (CodexEngine(with_turns), OpenCodeEngine(with_turns)):
        assert isinstance(other, TurnInput)
        assert not other.accepts_turns()
        assert not other.send_turn(FINISH)


class Watched(FakeStream):
    def __init__(self) -> None:
        super().__init__([ASSISTANT, RESULT])
        self.ended_before: list[bool] = []

    def lines(self) -> Iterator[str]:
        for line in self.output:
            self.ended_before.append(self.ended)
            yield line


def test_a_steered_run_keeps_stdin_open_sends_turns_and_closes_it_at_the_result() -> None:
    stream = Watched()
    runner = FakeRunner(
        responses={"claude --help": Completed(0, HELP_WITH_TURNS, "")},
        streams={"claude -p": stream},
    )
    engine = ClaudeCodeEngine(runner)
    delivered: list[tuple[str, bool]] = []

    def on_event(event: EngineEvent) -> None:
        if isinstance(event, ToolCall):
            delivered.append((event.name, engine.send_turn(FINISH)))
        if isinstance(event, RunResult):
            delivered.append(("result", engine.send_turn(FINISH)))

    outcome = engine.run(EngineRequest(prompt="go", cwd=".", env={}, stream_input=True), on_event)
    assert outcome.ok
    assert runner.keeps == [True]
    assert "--input-format" in runner.calls[-1]
    stdin = runner.stdins[-1]
    assert stdin is not None and turn_text(stdin) == "go"
    assert [turn_text(line) for line in stream.sent] == [FINISH]
    assert delivered == [("Read", True), ("result", False)]
    assert stream.ended and stream.ended_before == [False, False]
    assert not engine.send_turn(FINISH)


def test_without_turn_input_the_run_is_the_legacy_launch() -> None:
    stream = FakeStream([ASSISTANT, RESULT])
    runner = FakeRunner(
        responses={"claude --help": Completed(0, "--print", "")},
        streams={"claude -p": stream},
    )
    engine = ClaudeCodeEngine(runner)
    sent: list[bool] = []
    outcome = engine.run(
        EngineRequest(prompt="go", cwd=".", env={}, stream_input=True),
        lambda event: sent.append(engine.send_turn(FINISH)),
    )
    assert outcome.ok
    assert runner.keeps == [False]
    assert "--input-format" not in runner.calls[-1] and runner.stdins == ["go"]
    assert sent == [False, False, False] and stream.sent == [] and not stream.ended
    plain = FakeRunner(streams={"claude -p": FakeStream([ASSISTANT, RESULT])})
    ClaudeCodeEngine(plain).run(EngineRequest(prompt="go", cwd=".", env={}), lambda event: None)
    assert plain.keeps == [False] and plain.stdins == ["go"]
    assert "--input-format" not in plain.calls[-1]
    assert len(plain.calls) == 1


def test_a_live_child_receives_the_turn_and_exits_once_stdin_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "turns.py"
    script.write_text(CHILD, encoding="utf-8")
    monkeypatch.setenv("CUANTA_CLAUDE_BIN", f'"{sys.executable}" "{script}"')
    engine = ClaudeCodeEngine(SubprocessRunner())
    assert engine.accepts_turns()
    sent: list[bool] = []

    def on_event(event: EngineEvent) -> None:
        if isinstance(event, ToolCall):
            sent.append(engine.send_turn(FINISH))

    started = time.monotonic()
    request = EngineRequest(prompt="hola", cwd=str(tmp_path), env={}, stream_input=True)
    outcome = engine.run(request, on_event)
    assert time.monotonic() - started < 20
    assert sent == [True]
    assert outcome.exit_code == 0 and outcome.ok
    assert outcome.result is not None and outcome.result.text == f"hola | {FINISH}"


def two_results(lines: list[str], stream_input: bool) -> EngineOutcome:
    runner = FakeRunner(
        responses={"claude --help": Completed(0, HELP_WITH_TURNS, "")},
        streams={"claude -p": FakeStream(lines)},
    )
    request = EngineRequest(prompt="go", cwd=".", env={}, stream_input=stream_input)
    seen: list[EngineEvent] = []
    outcome = ClaudeCodeEngine(runner).run(request, seen.append)
    assert sum(isinstance(event, RunResult) for event in seen) == 2
    return outcome


def test_a_late_turn_after_a_successful_result_only_adds_its_spend() -> None:
    outcome = two_results([ASSISTANT, RESULT, LATE], True)
    result = outcome.result
    assert result is not None and outcome.ok
    assert (result.ok, result.subtype, result.text) == (True, "success", "FINISHED")
    assert (result.cost_usd, result.num_turns) == (0.05, 3)
    assert [(usage.model, usage.input_tokens, usage.output_tokens) for usage in result.models] == [
        ("claude-sonnet-5", 40, 9)
    ]
    assert outcome.late_results == 1


def test_a_failed_first_result_or_a_launch_without_turns_keeps_the_last_result() -> None:
    legacy = two_results([ASSISTANT, RESULT, LATE], False)
    retried = two_results([ASSISTANT, FAILED, RESULT], True)
    assert legacy.result is not None and not legacy.ok
    assert (legacy.result.subtype, legacy.result.text, legacy.late_results) == (
        "error_max_budget_usd",
        "",
        0,
    )
    assert retried.result is not None and retried.ok
    assert (retried.result.subtype, retried.result.cost_usd, retried.late_results) == (
        "success",
        0.01,
        0,
    )
