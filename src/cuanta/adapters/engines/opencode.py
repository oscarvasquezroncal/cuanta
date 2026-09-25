from __future__ import annotations

import hashlib
import json
from pathlib import Path
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


def _float(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


ATTACHED_PROMPT = "Follow the instructions in the attached prompt file exactly."
PROMPT_DIR = ".cuanta/prompts"


def prompt_file(request: EngineRequest) -> Path:
    digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()[:16]
    return Path(request.cwd or ".") / PROMPT_DIR / f"{digest}.md"


class OpenCodeParser(LineParser):
    def __init__(self, model: str) -> None:
        self._model = model or "opencode"
        self._session = ""
        self._usage = ModelUsage(self._model)
        self._cost = 0.0
        self._priced = False
        self._steps = 0
        self._error = False
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
        events: list[EngineEvent] = []
        session = str(data.get("sessionID") or "")
        if session and not self._session:
            self._session = session
            events.append(SessionStarted(session, self._model))
        part = as_dict(data.get("part"))
        kind = data.get("type")
        if kind == "step_finish":
            self._steps += 1
            tokens = as_dict(part.get("tokens"))
            cache = as_dict(tokens.get("cache"))
            if "cost" in part:
                self._priced = True
            self._cost += _float(part.get("cost"))
            self._usage = ModelUsage(
                self._model,
                input_tokens=self._usage.input_tokens + _int(tokens.get("input")),
                output_tokens=self._usage.output_tokens + _int(tokens.get("output")),
                cache_read_tokens=self._usage.cache_read_tokens + _int(cache.get("read")),
                cache_write_tokens=self._usage.cache_write_tokens + _int(cache.get("write")),
                reasoning_tokens=self._usage.reasoning_tokens + _int(tokens.get("reasoning")),
                cost_usd=self._cost,
            )
        elif kind == "tool_use":
            state = as_dict(part.get("state"))
            inputs = as_dict(state.get("input"))
            events.append(
                ToolCall(str(part.get("tool") or ""), str(part.get("callID") or ""), inputs)
            )
        elif kind == "text" and isinstance(part.get("text"), str):
            self._last_text = part["text"]
            events.append(AssistantText(part["text"]))
        elif kind == "error":
            self._error = True
        return events

    def finish(self, exit_code: int) -> RunResult | None:
        ok = exit_code == 0 and not self._error
        return RunResult(
            ok=ok,
            subtype="success" if ok else "error",
            cost_usd=self._cost if self._priced else None,
            num_turns=self._steps,
            session_id=self._session,
            models=(self._usage,),
            text=self._last_text,
        )


class OpenCodeEngine(StreamingEngine):
    engine_name = "opencode"
    default_binary = "opencode"
    binary_env = "CUANTA_OPENCODE_BIN"
    help_args = ("run", "--help")
    required_tokens = ("--format", "json", "--model")

    def command(self, request: EngineRequest) -> list[str]:
        command = [*self.binary(), "run", "--format", "json"]
        if request.model:
            command.extend(["--model", request.model])
        if request.cwd:
            command.extend(["--dir", request.cwd])
        command.extend(["--file", str(prompt_file(request)), ATTACHED_PROMPT])
        return command

    def stdin_text(self, request: EngineRequest) -> str | None:
        return None

    def prepare(self, request: EngineRequest) -> None:
        target = prompt_file(request)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(request.prompt, encoding="utf-8")

    def cleanup(self, request: EngineRequest) -> None:
        prompt_file(request).unlink(missing_ok=True)

    def parser(self) -> OpenCodeParser:
        return OpenCodeParser("")
