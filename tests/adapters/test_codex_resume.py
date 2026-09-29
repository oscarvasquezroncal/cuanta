from __future__ import annotations

import json

from cuanta.adapters.engines.codex import RESUME_TOKENS, CodexEngine
from cuanta.domain.engine import (
    BUDGET_LIMIT_SUBTYPE,
    GOVERNOR_STOP_SUBTYPE,
    EngineEvent,
    EngineRequest,
    RunResult,
    SessionStarted,
    ToolCall,
)
from cuanta.domain.sandbox import STATE_ROOT_ENV
from cuanta.ports.engine import Resumable, RunStop
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream

THREAD = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"
RESUME_HELP = (
    "Usage: codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]\n"
    "  -c, --config <key=value>\n  -m, --model <MODEL>\n  --skip-git-repo-check\n  --json\n"
)
EXEC_HELP = (
    "Run Codex non-interactively\n\nUsage: codex exec [OPTIONS] [PROMPT]\n"
    "  -c, --config <key=value>\n  -m, --model <MODEL>\n  -s, --sandbox <SANDBOX_MODE>\n"
    "  --skip-git-repo-check\n  --json\n"
)


def item(number: int, kind: str = "command_execution") -> str:
    return json.dumps(
        {"type": "item.completed", "item": {"id": f"item_{number}", "type": kind, "command": "ls"}}
    )


def codex_lines(items: int) -> list[str]:
    return [
        json.dumps({"type": "thread.started", "thread_id": THREAD}),
        *(item(number) for number in range(1, items + 1)),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}),
    ]


def test_resume_runs_on_the_thread_with_the_sandbox_as_config_and_no_sandbox_flag() -> None:
    engine = CodexEngine(FakeRunner())
    request = EngineRequest(
        prompt="termina ahora",
        cwd="/copy",
        env={STATE_ROOT_ENV: "/project"},
        model="gpt-6-sol",
        temporary_copy=True,
        resume_session=THREAD,
    )
    command = engine.command(request)
    assert command[:6] == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--config",
        'sandbox_mode="workspace-write"',
    ]
    assert "--sandbox" not in command and "-C" not in command and "--cd" not in command
    assert command[-4:] == ["--model", "gpt-6-sol", THREAD, "-"]
    assert "--skip-git-repo-check" in command
    assert 'approval_policy="never"' in command
    for setting in ("features.multi_agent=false", "features.multi_agent_v2=false"):
        assert command[command.index(setting) - 1] == "--config"
    assert engine.stdin_text(request) == "termina ahora"
    read_only = engine.command(
        EngineRequest(prompt="x", cwd="/copy", env={}, read_only=True, resume_session=THREAD)
    )
    assert read_only[5] == 'sandbox_mode="read-only"'
    assert read_only[-2:] == [THREAD, "-"]


def test_the_first_launch_keeps_the_sandbox_flag_and_no_thread() -> None:
    command = CodexEngine(FakeRunner()).command(
        EngineRequest(prompt="x", cwd="/repo", env={}, model="m")
    )
    assert command[:5] == ["codex", "exec", "--json", "--sandbox", "workspace-write"]
    assert "resume" not in command and THREAD not in command
    assert command[-3:] == ["--model", "m", "-"]


def test_resume_capability_is_read_once_from_the_installed_resume_help() -> None:
    runner = FakeRunner(responses={"codex exec resume --help": Completed(0, RESUME_HELP, "")})
    engine = CodexEngine(runner)
    assert isinstance(engine, Resumable) and isinstance(engine, RunStop)
    assert engine.resumable() and engine.resumable()
    assert runner.calls == [("codex", "exec", "resume", "--help")]
    for token in RESUME_TOKENS:
        missing = RESUME_HELP.replace(token, "")
        stale = FakeRunner(responses={"codex exec resume --help": Completed(0, missing, "")})
        assert not CodexEngine(stale).resumable()
    assert not CodexEngine(FakeRunner()).resumable()


def test_a_codex_without_exec_resume_is_not_resumable() -> None:
    old = FakeRunner(responses={"codex exec resume --help": Completed(0, EXEC_HELP, "")})
    assert not CodexEngine(old).resumable()


def test_a_halt_ends_the_process_and_keeps_the_thread_for_the_resume() -> None:
    stream = FakeStream(codex_lines(5), code=1)
    runner = FakeRunner(streams={"codex": stream})
    engine = CodexEngine(runner)
    seen: list[EngineEvent] = []

    def on_event(event: EngineEvent) -> None:
        seen.append(event)
        calls = [found for found in seen if isinstance(found, ToolCall)]
        if len(calls) == 2 and isinstance(event, ToolCall):
            engine.halt(GOVERNOR_STOP_SUBTYPE)

    outcome = engine.run(EngineRequest(prompt="x", cwd="", env={}, model="m"), on_event)
    assert stream.terminated and stream.closed
    assert [type(event) for event in seen] == [SessionStarted, ToolCall, ToolCall, RunResult]
    result = outcome.result
    assert result is not None and not outcome.ok
    assert result.subtype == GOVERNOR_STOP_SUBTYPE
    assert result.terminal_reason == "governor_stop"
    assert result.session_id == THREAD
    assert outcome.tool_calls == 2
    again = FakeStream(codex_lines(3))
    runner.streams["codex"] = again
    finished = engine.run(EngineRequest(prompt="x", cwd="", env={}, model="m"), lambda _: None)
    assert finished.ok and finished.result is not None
    assert finished.result.subtype == "success" and not again.terminated


def test_the_budget_stop_keeps_its_terminal_reason() -> None:
    from cuanta.adapters.engines.base import LineParser, final_result

    stopped = final_result(LineParser(), RunResult(True, "success", 0.1, 1, "s"), 0, "x")
    budget = final_result(
        LineParser(), RunResult(True, "success", 0.1, 1, "s"), 0, BUDGET_LIMIT_SUBTYPE
    )
    assert stopped is not None and stopped.terminal_reason == "cost_unknown"
    assert budget is not None and budget.terminal_reason == "max_budget_usd"
