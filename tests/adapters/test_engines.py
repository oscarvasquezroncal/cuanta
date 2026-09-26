from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.claude_stream import parse_line
from cuanta.adapters.engines.codex import CodexEngine, CodexParser
from cuanta.adapters.engines.opencode import (
    ATTACHED_PROMPT,
    OpenCodeEngine,
    OpenCodeParser,
    prompt_file,
)
from cuanta.domain.engine import (
    BUDGET_LIMIT_SUBTYPE,
    COST_UNKNOWN_SUBTYPE,
    AssistantText,
    EngineRequest,
    RunResult,
    SessionStarted,
    StepUsage,
    ToolCall,
)
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import (
    Shape,
    investigation_builtin_tools,
    investigation_denied,
    investigation_tools,
)
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


def test_codex_parser_reads_turn_usage() -> None:
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
    assert result.models[0].cost_usd is None


def test_codex_cumulative_usage_partitions_cache_writes_and_reasoning_once() -> None:
    parser = CodexParser("gpt-6-luna")
    for tokens in [(100, 40, 60, 10, 5), (130, 50, 70, 20, 7)]:
        fields = (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
        parser.feed(
            json.dumps({"type": "turn.completed", "usage": dict(zip(fields, tokens, strict=True))})
        )
    result = parser.finish(0)
    assert result is not None
    usage = result.models[0]
    assert (
        usage.input_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
        usage.output_tokens,
        usage.reasoning_tokens,
    ) == (10, 50, 70, 13, 7)
    assert usage.total == 150
    assert result.num_turns == 2
    assert usage.cost_usd is None


def test_opencode_without_cost_fields_reports_an_unknown_cost() -> None:
    parser = OpenCodeParser("zai/glm")
    parser.feed('{"type":"step_finish","sessionID":"s","part":{"tokens":{"input":5,"output":2}}}')
    result = parser.finish(0)
    assert result is not None
    assert result.cost_usd is None
    assert result.models[0].cost_usd is None


@pytest.mark.parametrize("cost", [None, True, "0", -1, float("nan"), float("inf"), 10**500])
def test_invalid_reported_costs_remain_unknown(cost: object) -> None:
    parser = OpenCodeParser("m")
    parser.feed(json.dumps({"type": "step_finish", "part": {"cost": cost}}))
    result = parser.finish(0)
    assert result is not None
    assert result.cost_usd is None
    assert result.models[0].cost_usd is None
    claude = parse_line(
        json.dumps(
            {"type": "result", "total_cost_usd": cost, "modelUsage": {"m": {"costUSD": cost}}}
        )
    )[0]
    assert isinstance(claude, RunResult)
    assert claude.cost_usd is None
    assert claude.models[0].cost_usd is None


def test_explicit_zero_cost_is_known_but_missing_claude_cost_is_not() -> None:
    parser = OpenCodeParser("free-model")
    events = parser.feed('{"type":"step_finish","part":{"cost":0,"tokens":{"input":5}}}')
    assert isinstance(events[-1], StepUsage)
    assert events[-1].usage.cost_usd == 0.0
    result = parser.finish(0)
    assert result is not None and result.cost_usd == 0.0
    assert result.models[0].cost_usd == 0.0
    claude = parse_line('{"type":"result","modelUsage":{"m":{"inputTokens":5}}}')[0]
    assert isinstance(claude, RunResult)
    assert claude.cost_usd is None
    assert claude.models[0].cost_usd is None


@pytest.mark.parametrize("unpriced_first", [False, True])
def test_opencode_partially_priced_steps_keep_total_cost_unknown(unpriced_first: bool) -> None:
    parser = OpenCodeParser("m")
    priced = '{"type":"step_finish","part":{"cost":0.01,"tokens":{"input":5}}}'
    unpriced = '{"type":"step_finish","part":{"tokens":{"input":8}}}'
    lines = [unpriced, priced] if unpriced_first else [priced, unpriced]
    steps = [event for line in lines for event in parser.feed(line) if isinstance(event, StepUsage)]
    assert sorted(step.usage.input_tokens for step in steps) == [5, 8]
    assert sum(step.usage.cost_usd is None for step in steps) == 1
    result = parser.finish(0)
    assert result is not None
    assert result.cost_usd is None
    assert result.models[0].cost_usd is None
    assert result.models[0].input_tokens == 13


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
    steps = [event for event in events if isinstance(event, StepUsage)]
    assert [step.usage.input_tokens for step in steps] == [671, 100]
    assert [step.usage.cost_usd for step in steps] == [0.001, 0.002]


def test_engine_commands_and_capabilities() -> None:
    runner = FakeRunner(
        binaries={"codex": "/bin/codex", "opencode": "/bin/opencode"},
        responses={
            "codex exec --help": Completed(
                0,
                "--json --sandbox read-only workspace-write --skip-git-repo-check --config",
                "",
            ),
            "opencode run --help": Completed(0, "--format  json  --model  --dir", ""),
        },
    )
    request = EngineRequest(prompt="fix it", cwd="/repo", env={}, model="m")
    codex = CodexEngine(runner)
    assert codex.command(request) == [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        "--config",
        'approval_policy="never"',
        "--config",
        "sandbox_workspace_write.writable_roots=[]",
        "--config",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "--config",
        "sandbox_workspace_write.exclude_slash_tmp=true",
        "--config",
        "sandbox_workspace_write.network_access=false",
        "--model",
        "m",
        "-",
    ]
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


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("temporary_copy", [False, True])
def test_codex_sandbox_is_explicit_and_only_owned_copies_skip_git(
    read_only: bool, temporary_copy: bool
) -> None:
    request = EngineRequest(
        prompt="x", cwd="/repo", env={}, read_only=read_only, temporary_copy=temporary_copy
    )
    command = CodexEngine(FakeRunner()).command(request)
    assert command[command.index("--sandbox") + 1] == (
        "read-only" if read_only else "workspace-write"
    )
    assert ("--skip-git-repo-check" in command) is temporary_copy
    assert "danger-full-access" not in command
    assert 'approval_policy="never"' in command
    assert command[-1] == "-"


def test_claude_readonly_request_restricts_a_write_capable_role() -> None:
    request = EngineRequest(
        prompt="inspect",
        cwd="/repo",
        env={},
        read_only=True,
        allowed_tools=("Read", "Write", "Edit", "Bash", "Task"),
        disallowed_tools=("Bash(git *)",),
        tools=("Read", "Write", "Bash"),
    )
    command = ClaudeCodeEngine(FakeRunner()).command(request)
    assert command[command.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert command[command.index("--tools") + 1] == "Read,Grep,Glob"
    denied = set(command[command.index("--disallowedTools") + 1].split(","))
    assert set(investigation_denied(False, Shape.SINGLE)) <= denied


def test_claude_readonly_with_no_allowlist_still_limits_builtin_tools() -> None:
    request = EngineRequest(
        prompt="inspect",
        cwd="/repo",
        env={},
        read_only=True,
        disallowed_tools=investigation_denied(False, Shape.PIPELINE),
    )
    command = ClaudeCodeEngine(FakeRunner()).command(request)
    assert command[command.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert command[command.index("--tools") + 1] == "Read,Grep,Glob"
    denied = set(command[command.index("--disallowedTools") + 1].split(","))
    assert set(investigation_denied(False, Shape.SINGLE)) <= denied


@pytest.mark.parametrize("shape", [Shape.SINGLE, Shape.PIPELINE])
@pytest.mark.parametrize("graph", [False, True])
def test_claude_keeps_an_existing_investigation_launch_profile(shape: Shape, graph: bool) -> None:
    allowed = investigation_tools(False, shape, graph)
    denied = investigation_denied(False, shape)
    builtins = investigation_builtin_tools(False, shape, graph)
    request = EngineRequest(
        prompt="inspect",
        cwd="/repo",
        env={},
        read_only=True,
        allowed_tools=allowed,
        disallowed_tools=denied,
        tools=builtins,
    )
    command = ClaudeCodeEngine(FakeRunner()).command(request)
    assert command[command.index("--allowedTools") + 1] == ",".join(allowed)
    assert command[command.index("--disallowedTools") + 1] == ",".join(denied)
    if builtins is None:
        assert "--tools" not in command
    else:
        assert command[command.index("--tools") + 1] == ",".join(builtins)


@pytest.mark.parametrize("engine", [CodexEngine, OpenCodeEngine])
def test_engine_run_labels_usage_with_requested_model(
    engine: type[CodexEngine] | type[OpenCodeEngine], tmp_path: Path
) -> None:
    runner = FakeRunner(
        streams={"codex": FakeStream(CODEX_LINES), "opencode": FakeStream(OPENCODE_LINES)}
    )
    seen: list[object] = []
    request = EngineRequest(prompt="x", cwd=str(tmp_path), env={}, model="requested-model")
    outcome = engine(runner).run(request, seen.append)
    started = next(event for event in seen if isinstance(event, SessionStarted))
    assert started.model == "requested-model"
    assert outcome.result is not None
    assert [usage.model for usage in outcome.result.models] == ["requested-model"]


def test_opencode_refuses_readonly_before_writing_or_spawning(tmp_path: Path) -> None:
    runner = FakeRunner()
    request = EngineRequest(prompt="x", cwd=str(tmp_path), env={}, read_only=True)
    with pytest.raises(DomainFailure, match="OpenCode"):
        OpenCodeEngine(runner).run(request, lambda _: None)
    assert runner.calls == []
    assert not prompt_file(request).parent.exists()


@pytest.mark.parametrize(("cap", "steps", "cost"), [(0.001, 1, 0.001), (0.002, 2, 0.003)])
def test_opencode_stops_at_step_cap_and_closes_before_wait(
    cap: float, steps: int, cost: float, tmp_path: Path
) -> None:
    class WatchedStream(FakeStream):
        terminated = False
        consumed = 0

        def lines(self) -> Iterator[str]:
            for line in self.output:
                self.consumed += 1
                yield line

        def terminate(self) -> None:
            self.terminated = True

        def wait(self) -> int:
            assert self.closed
            return 1

    stream = WatchedStream([*OPENCODE_LINES, '{"type":"text","part":{"text":"after cap"}}'])
    runner = FakeRunner(streams={"opencode": stream})
    engine = OpenCodeEngine(runner)
    seen: list[object] = []

    def collect(event: object) -> None:
        seen.append(event)
        completed = sum(isinstance(item, StepUsage) for item in seen)
        if isinstance(event, StepUsage) and completed == steps:
            assert stream.terminated

    outcome = engine.run(
        EngineRequest(prompt="x", cwd=str(tmp_path), env={}, max_budget_usd=cap), collect
    )
    assert not outcome.ok
    assert stream.terminated and stream.closed
    assert stream.consumed == 3 + steps
    assert not engine.cancelled
    result = outcome.result
    assert result is not None
    assert result.subtype == BUDGET_LIMIT_SUBTYPE
    assert result.terminal_reason == "max_budget_usd"
    assert result.num_turns == steps
    assert result.cost_usd == cost
    assert sum(isinstance(event, RunResult) for event in seen) == 1
    assert not any(isinstance(event, AssistantText) and event.text == "after cap" for event in seen)
    assert not prompt_file(EngineRequest("x", str(tmp_path), {})).exists()


@pytest.mark.parametrize("budget", [0.0, 1.0])
def test_opencode_budget_stops_at_unknown_step_cost_before_later_priced_steps(
    tmp_path: Path, budget: float
) -> None:
    class WatchedStream(FakeStream):
        terminated = False
        consumed = 0

        def lines(self) -> Iterator[str]:
            for line in self.output:
                self.consumed += 1
                yield line

        def terminate(self) -> None:
            self.terminated = True

    stream = WatchedStream(
        [
            '{"type":"step_finish","part":{"tokens":{"input":5}}}',
            '{"type":"step_finish","part":{"cost":0.01,"tokens":{"input":7}}}',
        ]
    )
    engine = OpenCodeEngine(FakeRunner(streams={"opencode": stream}))
    seen: list[object] = []
    outcome = engine.run(EngineRequest("x", str(tmp_path), {}, max_budget_usd=budget), seen.append)
    result = outcome.result
    assert result is not None
    assert stream.closed
    assert result.cost_usd is None and result.models[0].cost_usd is None
    if budget:
        assert not outcome.ok and stream.terminated
        assert result.subtype == COST_UNKNOWN_SUBTYPE
        assert result.terminal_reason == "cost_unknown"
        assert result.models[0].input_tokens == 5
        assert stream.consumed == result.num_turns == 1
    else:
        assert outcome.ok and not stream.terminated
        assert result.models[0].input_tokens == 12
        assert stream.consumed == result.num_turns == 2
    assert sum(isinstance(event, RunResult) for event in seen) == 1


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
