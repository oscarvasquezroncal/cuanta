from __future__ import annotations

import json
from typing import Any

from cuanta.adapters.engines.base import LineParser, StreamingEngine, as_dict
from cuanta.domain.engine import (
    AssistantText,
    EngineEvent,
    EngineRequest,
    ModelUsage,
    RunResult,
    SessionStarted,
    ToolCall,
)


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


class CodexParser(LineParser):
    def __init__(self, model: str) -> None:
        self._model = model or "codex"
        self._session = ""
        self._usage = ModelUsage(self._model)
        self._failed = False
        self._turns = 0
        self._last_text = ""

    def feed(self, line: str) -> list[EngineEvent]:
        stripped = line.strip()
        if not stripped.startswith("{"):
            return []
        try:
            data = json.loads(stripped)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        kind = data.get("type")
        if kind == "thread.started":
            self._session = str(data.get("thread_id") or "")
            return [SessionStarted(self._session, self._model)]
        if kind == "turn.completed":
            self._turns += 1
            usage = as_dict(data.get("usage"))
            cached = _int(usage.get("cached_input_tokens"))
            self._usage = ModelUsage(
                self._model,
                input_tokens=self._usage.input_tokens
                + max(_int(usage.get("input_tokens")) - cached, 0),
                output_tokens=self._usage.output_tokens + _int(usage.get("output_tokens")),
                cache_read_tokens=self._usage.cache_read_tokens + cached,
                reasoning_tokens=self._usage.reasoning_tokens
                + _int(usage.get("reasoning_output_tokens")),
            )
            return []
        if kind == "turn.failed":
            self._failed = True
            return []
        if kind == "item.completed":
            item = as_dict(data.get("item"))
            item_type = item.get("type")
            if item_type == "agent_message" and isinstance(item.get("text"), str):
                self._last_text = item["text"]
                return [AssistantText(item["text"])]
            if item_type == "command_execution":
                return [
                    ToolCall(
                        "shell", str(item.get("id") or ""), {"command": item.get("command", "")}
                    )
                ]
            if item_type in {"file_change", "mcp_tool_call", "web_search"}:
                return [ToolCall(str(item_type), str(item.get("id") or ""), {})]
        return []

    def finish(self, exit_code: int) -> RunResult | None:
        return RunResult(
            ok=exit_code == 0 and not self._failed,
            subtype="success" if exit_code == 0 and not self._failed else "error",
            cost_usd=None,
            num_turns=self._turns,
            session_id=self._session,
            models=(self._usage,),
            text=self._last_text,
        )


class CodexEngine(StreamingEngine):
    engine_name = "codex"
    default_binary = "codex"
    binary_env = "CUANTA_CODEX_BIN"
    help_args = ("exec", "--help")
    required_tokens = ("--json",)

    def command(self, request: EngineRequest) -> list[str]:
        command = [*self.binary(), "exec", "--json"]
        if request.model:
            command.extend(["--model", request.model])
        command.append("-")
        return command

    def parser(self) -> CodexParser:
        return CodexParser("")
