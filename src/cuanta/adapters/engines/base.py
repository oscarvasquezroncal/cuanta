from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from math import isfinite
from pathlib import Path
from typing import Any

from cuanta.domain.engine import (
    BUDGET_LIMIT_SUBTYPE,
    COMMAND_LINE_LIMIT,
    COST_UNKNOWN_SUBTYPE,
    EngineEvent,
    EngineOutcome,
    EngineRequest,
    RunResult,
    StepUsage,
    ToolCall,
    command_line_length,
)
from cuanta.domain.errors import DomainFailure
from cuanta.domain.gateway import split_command
from cuanta.ports.system import ProcessRunner, StreamHandle

HELP_TIMEOUT_S = 30.0


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def reported_cost(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        cost = float(value)
    except OverflowError:
        return None
    return cost if isfinite(cost) and cost >= 0 else None


class LineParser:
    def feed(self, line: str) -> list[EngineEvent]:
        raise NotImplementedError

    def finish(self, exit_code: int) -> RunResult | None:
        return None


def final_result(
    parser: LineParser, result: RunResult | None, code: int, stopped: str
) -> RunResult | None:
    if result is None:
        result = parser.finish(code)
    if not stopped:
        return result
    return replace(
        result or RunResult(False, stopped, None, 0, ""),
        ok=False,
        subtype=stopped,
        terminal_reason="max_budget_usd" if stopped == BUDGET_LIMIT_SUBTYPE else "cost_unknown",
    )


class StreamingEngine:
    engine_name = "engine"
    default_binary = "engine"
    binary_env = "CUANTA_ENGINE_BIN"
    help_args: tuple[str, ...] = ("--help",)
    required_tokens: tuple[str, ...] = ()
    step_cost_cap = False

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner
        self._help: str | None = None
        self.cancelled = False
        self._active: StreamHandle | None = None

    def cancel(self) -> None:
        self.cancelled = True
        active = self._active
        if active is not None:
            active.terminate()

    @property
    def name(self) -> str:
        return self.engine_name

    def binary(self) -> tuple[str, ...]:
        override = os.environ.get(self.binary_env, "").strip()
        if override:
            return split_command(override, os.name == "nt")
        return (self.default_binary,)

    def available(self) -> bool:
        head = self.binary()[0]
        return os.path.isfile(head) or self._runner.which(head) is not None

    def version(self) -> str:
        completed = self._runner.run([*self.binary(), "--version"], timeout=HELP_TIMEOUT_S)
        text = (completed.stdout or completed.stderr).strip()
        return text.splitlines()[0] if completed.ok and text else ""

    def help_text(self) -> str:
        if self._help is None:
            completed = self._runner.run([*self.binary(), *self.help_args], timeout=HELP_TIMEOUT_S)
            self._help = completed.stdout + completed.stderr
        return self._help

    def missing_flags(self) -> tuple[str, ...]:
        text = self.help_text()
        return tuple(token for token in self.required_tokens if token not in text)

    def command(self, request: EngineRequest) -> list[str]:
        raise NotImplementedError

    def stdin_text(self, request: EngineRequest) -> str | None:
        return request.prompt

    def prepare(self, request: EngineRequest) -> None:
        return None

    def cleanup(self, request: EngineRequest) -> None:
        return None

    def parser(self, request: EngineRequest) -> LineParser:
        raise NotImplementedError

    def _step_limit(self, event: EngineEvent, spent: float, cap: float) -> tuple[float, str]:
        if not self.step_cost_cap or not isinstance(event, StepUsage) or cap <= 0:
            return spent, ""
        cost = event.usage.cost_usd
        if cost is None:
            return spent, COST_UNKNOWN_SUBTYPE
        spent += cost
        return spent, BUDGET_LIMIT_SUBTYPE if spent >= cap else ""

    def _validated_command(self, request: EngineRequest) -> list[str]:
        command = self.command(request)
        length = command_line_length(command)
        if length > COMMAND_LINE_LIMIT:
            raise DomainFailure(
                f"the {self.name} command line would be {length:,} characters "
                f"(limit {COMMAND_LINE_LIMIT:,})",
                "shorten the request or pass long text as --evidence, which cuanta stores "
                "as a capsule",
            )
        return command

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        parser = self.parser(request)
        cwd = Path(request.cwd) if request.cwd else None
        command = self._validated_command(request)
        self.prepare(request)
        try:
            stream = self._runner.stream(
                command,
                cwd=cwd,
                env=request.env,
                stdin_text=self.stdin_text(request),
                unset=request.unset_env,
            )
        except BaseException:
            self.cleanup(request)
            raise
        result: RunResult | None = None
        tool_calls = 0
        spent = 0.0
        stopped = ""
        closed = False
        self._active = stream
        try:
            for line in stream.lines():
                if self.cancelled:
                    break
                for event in parser.feed(line):
                    if isinstance(event, ToolCall):
                        tool_calls += 1
                    if isinstance(event, RunResult):
                        result = event
                    spent, stopped = self._step_limit(event, spent, request.max_budget_usd)
                    if stopped:
                        stream.terminate()
                    on_event(event)
                    if stopped:
                        break
                if stopped:
                    break
            if self.cancelled:
                stream.terminate()
            if self.cancelled or stopped:
                stream.close()
                closed = True
            code = stream.wait()
            tail = "\n".join(stream.stderr_text().strip().splitlines()[-5:])
        finally:
            self._active = None
            if not closed:
                stream.close()
            self.cleanup(request)
        synthesized = result is None or bool(stopped)
        result = final_result(parser, result, code, stopped)
        if synthesized and result is not None:
            on_event(result)
        return EngineOutcome(exit_code=code, result=result, tool_calls=tool_calls, stderr_tail=tail)
