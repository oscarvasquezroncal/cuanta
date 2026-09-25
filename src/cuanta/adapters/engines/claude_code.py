from __future__ import annotations

from cuanta.adapters.engines.base import LineParser, StreamingEngine
from cuanta.adapters.engines.claude_stream import parse_line
from cuanta.domain.engine import EngineEvent, EngineRequest

REQUIRED_FLAGS = (
    "--print",
    "--output-format",
    "--verbose",
    "--permission-mode",
    "--allowedTools",
    "--disallowedTools",
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


def build_command(binary: tuple[str, ...], request: EngineRequest) -> list[str]:
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
    if request.model:
        command.extend(["--model", request.model])
    if request.max_budget_usd > 0:
        command.extend(["--max-budget-usd", f"{request.max_budget_usd:.2f}"])
    if request.agents_file:
        command.extend(["--agents", request.agents_file])
    if request.effort:
        command.extend(["--effort", request.effort])
    if request.append_system_prompt:
        command.extend(["--append-system-prompt", request.append_system_prompt])
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

    def parser(self) -> ClaudeStreamParser:
        return ClaudeStreamParser()
