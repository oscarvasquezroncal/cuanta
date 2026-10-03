from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from math import isfinite
from pathlib import Path
from typing import Any

from cuanta.domain.engine import (
    BUDGET_LIMIT_SUBTYPE,
    COMMAND_LINE_LIMIT,
    COST_UNKNOWN_SUBTYPE,
    GOVERNOR_STOP_SUBTYPE,
    WALL_LIMIT_SUBTYPE,
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
STOP_REASONS = {
    BUDGET_LIMIT_SUBTYPE: "max_budget_usd",
    GOVERNOR_STOP_SUBTYPE: "governor_stop",
    WALL_LIMIT_SUBTYPE: "max_wall",
}


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

    def turn_completed(self) -> bool:
        return False


def final_result(
    parser: LineParser, result: RunResult | None, code: int, stopped: str
) -> RunResult | None:
    if result is None:
        result = parser.finish(code)
    if not stopped:
        return result
    return replace(
        result or RunResult(False, stopped, None, 0, "", partial=True),
        ok=False,
        subtype=stopped,
        terminal_reason=STOP_REASONS.get(stopped, "cost_unknown"),
    )


class StreamingEngine:
    engine_name = "engine"
    default_binary = "engine"
    binary_env = "CUANTA_ENGINE_BIN"
    help_args: tuple[str, ...] = ("--help",)
    required_tokens: tuple[str, ...] = ()
    step_cost_cap = False

    def __init__(
        self, runner: ProcessRunner, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._runner = runner
        self._monotonic = monotonic
        self._help: str | None = None
        self.cancelled = False
        self._active: StreamHandle | None = None
        self._turns = False
        self._stop_reason = ""
        self._late = 0
        self._startup_seconds: float | None = None
        self._process_start = 0.0
        self._sent_turns = 0
        self._continued_result = False
        self._continue_results = False
        self._guard = threading.Lock()
        self._holding = False
        self._settled = False
        self._cut = False

    def cancel(self) -> None:
        self.cancelled = True
        active = self._active
        if active is not None:
            active.terminate()

    def halt(self, subtype: str) -> None:
        with self._guard:
            if self._settled:
                return
            self._stop_reason = subtype
            active = None if self._holding else self._active
            self._cut = self._cut or active is not None
        if active is not None:
            active.terminate()

    def accepts_turns(self) -> bool:
        return False

    def turn_line(self, text: str) -> str:
        return text

    def send_turn(self, text: str) -> bool:
        active = self._active
        if active is None or not self._turns:
            return False
        sent = active.send(self.turn_line(text))
        if sent:
            self._sent_turns += 1
        return sent

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

    def _events(self, parser: LineParser, line: str, stream: StreamHandle) -> list[EngineEvent]:
        events = parser.feed(line)
        if events and self._startup_seconds is None:
            self._startup_seconds = max(0.0, self._monotonic() - self._process_start)
        if (
            self._turns
            and not self._continue_results
            and any(isinstance(event, RunResult) for event in events)
        ):
            stream.end_input()
        return events

    def _kept(self, result: RunResult | None, later: RunResult) -> RunResult:
        if self._continued_result:
            self._continued_result = False
            return later
        if not self._turns or result is None or not result.ok:
            return later
        self._late += 1
        return replace(
            result, cost_usd=later.cost_usd, num_turns=later.num_turns, models=later.models
        )

    def _begin(self, request: EngineRequest) -> None:
        self._startup_seconds = None
        self._turns = request.stream_input and self.accepts_turns()
        self._stop_reason = ""
        self._late = 0
        self._sent_turns = 0
        self._continued_result = False
        self._continue_results = request.continue_results
        with self._guard:
            self._holding = False
            self._settled = False
            self._cut = False
        self.prepare(request)
        self._process_start = self._monotonic()

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

    def _answered(self, submitted: int) -> bool:
        with self._guard:
            self._holding = False
            if self._sent_turns == submitted and not self._cut:
                self._settled = True
                self._stop_reason = ""
            return bool(self._stop_reason) and not self._cut

    def _deliver(
        self, event: EngineEvent, on_event: Callable[[EngineEvent], None], stream: StreamHandle
    ) -> None:
        if not isinstance(event, RunResult):
            on_event(event)
            return
        submitted = self._sent_turns
        with self._guard:
            self._holding = True
        try:
            on_event(event)
        finally:
            halted = self._answered(submitted)
        if self._turns and self._continue_results:
            self._continued_result = self._sent_turns > submitted
            if not self._continued_result:
                stream.end_input()
        if halted:
            with self._guard:
                self._cut = True
            stream.terminate()

    def _cut_short(self, stopped: str) -> bool:
        return self.cancelled or stopped == WALL_LIMIT_SUBTYPE

    def _final(
        self,
        parser: LineParser,
        result: RunResult | None,
        code: int,
        stopped: str,
        on_event: Callable[[EngineEvent], None],
    ) -> RunResult | None:
        synthesized = result is None or bool(stopped)
        unfinished = result is None and self._cut_short(stopped) and not parser.turn_completed()
        final = final_result(parser, result, code, stopped)
        if final is not None and (self._continued_result or unfinished):
            final = replace(final, partial=True)
        if synthesized and final is not None:
            on_event(final)
        return final

    def run(self, request: EngineRequest, on_event: Callable[[EngineEvent], None]) -> EngineOutcome:
        parser = self.parser(request)
        cwd = Path(request.cwd) if request.cwd else None
        command = self._validated_command(request)
        self._begin(request)
        try:
            stream = self._runner.stream(
                command,
                cwd=cwd,
                env=request.env,
                stdin_text=self.stdin_text(request),
                unset=request.unset_env,
                keep_stdin=self._turns,
            )
        except BaseException:
            self.cleanup(request)
            raise
        result: RunResult | None = None
        tool_calls = 0
        spent = 0.0
        stopped = ""
        closed = False
        with self._guard:
            self._active = stream
            pending = bool(self._stop_reason)
            self._cut = self._cut or pending
        if self.cancelled or pending:
            stream.terminate()
        try:
            for line in stream.lines():
                if self.cancelled:
                    break
                for event in self._events(parser, line, stream):
                    if isinstance(event, ToolCall):
                        tool_calls += 1
                    if isinstance(event, RunResult):
                        result = self._kept(result, event)
                    spent, stopped = self._step_limit(event, spent, request.max_budget_usd)
                    if stopped:
                        stream.terminate()
                    self._deliver(event, on_event, stream)
                    stopped = stopped or self._stop_reason
                    if stopped:
                        break
                if stopped:
                    break
            stopped = stopped or self._stop_reason
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
        return EngineOutcome(
            exit_code=code,
            result=self._final(parser, result, code, stopped, on_event),
            tool_calls=tool_calls,
            stderr_tail=tail,
            late_results=self._late,
            startup_seconds=self._startup_seconds,
            cancelled=self.cancelled,
        )
