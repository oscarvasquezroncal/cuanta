from __future__ import annotations

from dataclasses import replace

from cuanta.adapters.engines.base import LineParser, StreamingEngine
from cuanta.adapters.engines.claude_stream import parse_line
from cuanta.domain.engine import EngineEvent, EngineRequest
from cuanta.domain.mandate import (
    READ_ONLY_DENIED,
    Shape,
    investigation_builtin_tools,
    investigation_denied,
    investigation_tools,
)

REQUIRED_FLAGS = (
    "--print",
    "--output-format",
    "--verbose",
    "--permission-mode",
    "--allowedTools",
    "--disallowedTools",
    "--tools",
    "--model",
    "--max-budget-usd",
    "--agents",
    "--strict-mcp-config",
    "--mcp-config",
    "--settings",
    "--effort",
    "--append-system-prompt",
)
REQUIRED_CHOICES = ("stream-json", "dontAsk")
PROBE_FLAGS = ("--exclude-dynamic-system-prompt-sections", "--no-session-persistence")


def readonly_request(request: EngineRequest) -> EngineRequest:
    if not request.read_only:
        return request
    graph = "Bash(graphify *)" in request.allowed_tools
    pipeline = set(investigation_tools(False, Shape.PIPELINE, graph))
    if (
        set(READ_ONLY_DENIED) <= set(request.disallowed_tools)
        and set(request.allowed_tools) == pipeline
        and request.tools is None
    ):
        return request
    allowed = investigation_tools(False, Shape.SINGLE, graph)
    denied = (*request.disallowed_tools, *investigation_denied(False, Shape.SINGLE))
    return replace(
        request,
        allowed_tools=allowed,
        disallowed_tools=tuple(dict.fromkeys(denied)),
        tools=investigation_builtin_tools(False, Shape.SINGLE, graph),
    )


def build_command(binary: tuple[str, ...], request: EngineRequest) -> list[str]:
    request = readonly_request(request)
    command = [
        *binary,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
    ]
    if request.allowed_tools:
        command.extend(["--allowedTools", ",".join(request.allowed_tools)])
    if request.disallowed_tools:
        command.extend(["--disallowedTools", ",".join(request.disallowed_tools)])
    if request.tools is not None:
        command.extend(["--tools", ",".join(request.tools)])
    if request.model:
        command.extend(["--model", request.model])
    if request.max_budget_usd > 0:
        command.extend(["--max-budget-usd", f"{request.max_budget_usd:.2f}"])
    if request.max_turns > 0:
        command.extend(["--max-turns", str(request.max_turns)])
    if request.agents_file:
        command.extend(["--agents", request.agents_file])
    if request.effort:
        command.extend(["--effort", request.effort])
    if request.append_system_prompt:
        command.extend(["--append-system-prompt", request.append_system_prompt])
    if request.stable_prefix:
        command.append("--exclude-dynamic-system-prompt-sections")
    if not request.persist_session:
        command.append("--no-session-persistence")
    if request.mcp_config:
        command.extend(["--strict-mcp-config", "--mcp-config", request.mcp_config])
    if request.settings_file:
        command.extend(["--settings", request.settings_file])
    return command


class ClaudeStreamParser(LineParser):
    def feed(self, line: str) -> list[EngineEvent]:
        return parse_line(line)


class ClaudeCodeEngine(StreamingEngine):
    engine_name = "claude"
    default_binary = "claude"
    binary_env = "CUANTA_CLAUDE_BIN"
    required_tokens = (*REQUIRED_FLAGS, *REQUIRED_CHOICES)

    def command(self, request: EngineRequest) -> list[str]:
        return build_command(self.binary(), request)

    def parser(self, request: EngineRequest) -> ClaudeStreamParser:
        return ClaudeStreamParser()
