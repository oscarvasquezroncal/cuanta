from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.codex import CodexEngine, CodexParser
from cuanta.adapters.engines.opencode import (
    ATTACHED_PROMPT,
    OpenCodeEngine,
    OpenCodeParser,
    prompt_file,
)
from cuanta.domain.engine import AssistantText, EngineRequest, RunResult, SessionStarted, ToolCall
from cuanta.domain.errors import DomainFailure
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream

CODEX_LINES = [
    '{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"bash -lc pytest -q","aggregated_output":"1 failed","exit_code":1,"status":"completed"}}',
    '{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"Fixed the bug."}}',
    '{"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122}}',
]

OPENCODE_LINES = [
    json.dumps(
        {"type": "step_start", "timestamp": 1, "sessionID": "ses_1", "part": {"type": "step-start"}}
    ),
    json.dumps(
        {
            "type": "tool_use",
            "timestamp": 2,
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "read",
                "callID": "c1",
                "state": {"status": "completed", "input": {"filePath": "src/a.ts"}},
            },
        }
    ),
    json.dumps(
        {
            "type": "text",
            "timestamp": 3,
            "sessionID": "ses_1",
            "part": {"type": "text", "text": "done"},
        }
    ),
    json.dumps(
        {
            "type": "step_finish",
            "timestamp": 4,
            "sessionID": "ses_1",
            "part": {
                "type": "step-finish",
                "cost": 0.001,
                "tokens": {
                    "input": 671,
                    "output": 8,
                    "reasoning": 0,
                    "cache": {"read": 21415, "write": 0},
                },
            },
        }
    ),
    json.dumps(
        {
            "type": "step_finish",
            "timestamp": 5,
            "sessionID": "ses_1",
            "part": {
                "type": "step-finish",
                "cost": 0.002,
                "tokens": {
                    "input": 100,
                    "output": 50,
                    "reasoning": 10,
                    "cache": {"read": 200, "write": 30},
                },
            },
        }
    ),
]


def test_codex_parser_sums_turn_usage() -> None:
    parser = CodexParser("gpt-5-codex")
    events = [event for line in CODEX_LINES for event in parser.feed(line)]
    assert isinstance(events[0], SessionStarted)
    assert any(isinstance(event, ToolCall) and event.name == "shell" for event in events)
    assert any(isinstance(event, AssistantText) for event in events)
    result = parser.finish(0)
    assert result is not None and result.ok
    usage = result.models[0]
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (315, 24448, 122)
    assert parser.feed('{"type":"turn.failed"}') == []
    failed = parser.finish(0)
    assert failed is not None and not failed.ok
    assert result.cost_usd is None


def test_opencode_without_cost_fields_reports_an_unknown_cost() -> None:
    parser = OpenCodeParser("zai/glm")
    parser.feed('{"type":"step_finish","sessionID":"s","part":{"tokens":{"input":5,"output":2}}}')
    result = parser.finish(0)
    assert result is not None
    assert result.cost_usd is None


def test_opencode_parser_sums_step_deltas() -> None:
    parser = OpenCodeParser("anthropic/claude")
    events = [event for line in OPENCODE_LINES for event in parser.feed(line)]
    assert isinstance(events[0], SessionStarted)
    tool = next(event for event in events if isinstance(event, ToolCall))
    assert tool.name == "read"
    assert tool.inputs == {"filePath": "src/a.ts"}
    result = parser.finish(0)
    assert result is not None
    usage = result.models[0]
    assert (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
        usage.reasoning_tokens,
    ) == (771, 58, 21615, 30, 10)
    assert result.cost_usd is not None
    assert round(result.cost_usd, 3) == 0.003
    assert result.num_turns == 2


def test_engine_commands_and_capabilities() -> None:
    runner = FakeRunner(
        binaries={"codex": "/bin/codex", "opencode": "/bin/opencode"},
        responses={
            "codex exec --help": Completed(0, "Usage: codex exec [OPTIONS] --json", ""),
            "opencode run --help": Completed(0, "--format  json  --model  --dir", ""),
        },
    )
    request = EngineRequest(prompt="fix it", cwd="/repo", env={}, model="m")
    codex = CodexEngine(runner)
    assert codex.command(request) == ["codex", "exec", "--json", "--model", "m", "-"]
    assert codex.stdin_text(request) == "fix it"
    assert codex.missing_flags() == ()
    opencode = OpenCodeEngine(runner)
    assert opencode.command(request) == [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "m",
        "--dir",
        "/repo",
        "--file",
        str(prompt_file(request)),
        ATTACHED_PROMPT,
    ]
    assert opencode.stdin_text(request) is None
    assert opencode.missing_flags() == ()
    assert codex.available() and opencode.available()


def test_streaming_engine_run_synthesizes_result() -> None:
    runner = FakeRunner(
        binaries={"opencode": "/bin/opencode"},
        streams={"opencode run": FakeStream(OPENCODE_LINES)},
    )
    seen: list[object] = []
    outcome = OpenCodeEngine(runner).run(EngineRequest(prompt="x", cwd="", env={}), seen.append)
    assert outcome.ok
    assert outcome.tool_calls == 1
    assert isinstance(seen[-1], RunResult)
    assert outcome.tokens == 771 + 58 + 21615 + 30 + 10


def test_prompts_travel_on_stdin_not_the_command_line(tmp_path: Path) -> None:
    runner = FakeRunner(
        binaries={"claude": "/bin/claude", "codex": "/bin/codex", "opencode": "/bin/opencode"},
    )
    huge = "x" * 200_000
    request = EngineRequest(prompt=huge, cwd=str(tmp_path), env={})
    for engine in (ClaudeCodeEngine(runner), CodexEngine(runner)):
        engine.run(request, lambda event: None)
        assert all(len(arg) < 1_000 for arg in runner.calls[-1])
        assert runner.stdins[-1] == huge
    written: list[str] = []

    class WatchedOpenCode(OpenCodeEngine):
        def prepare(self, request: EngineRequest) -> None:
            super().prepare(request)
            written.append(prompt_file(request).read_text(encoding="utf-8"))

    WatchedOpenCode(runner).run(request, lambda event: None)
    assert written == [huge]
    assert not prompt_file(request).exists()
    assert all(len(arg) < 1_000 for arg in runner.calls[-1])


def test_an_oversized_command_line_fails_with_a_cuanta_message() -> None:
    runner = FakeRunner(binaries={"claude": "/bin/claude"})
    request = EngineRequest(
        prompt="x", cwd="", env={}, allowed_tools=tuple(f"Bash(tool{i} *)" for i in range(3_000))
    )
    with pytest.raises(DomainFailure, match="command line would be"):
        ClaudeCodeEngine(runner).run(request, lambda event: None)
    assert runner.calls == []
