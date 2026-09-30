from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace

from cuanta.domain.engine import EngineOutcome, RunResult
from cuanta.domain.implementation import (
    MAX_REPAIRS,
    MIN_REPAIR_HEADROOM_USD,
    ImplementationReport,
    ImplementationStep,
    VerificationDelta,
    baseline_key,
    engine_stop_reason,
    implementation_fingerprint,
    planned_steps,
    repair_feedback,
    unavailable_checks,
    verification_delta,
)
from cuanta.domain.role_handoff import VerifyResult
from cuanta.domain.stable import stable_json
from cuanta.ports.workspace import Workspace

Verify = Callable[[Sequence[str], Callable[[], bool]], tuple[VerifyResult, ...]]


class VerificationBaselines:
    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def get(
        self,
        snapshot: Mapping[str, str],
        commands: Sequence[str],
        verify: Verify,
        stopped: Callable[[], bool],
        context: str = "",
    ) -> tuple[VerifyResult, ...]:
        path = f".cuanta/baselines/{baseline_key(snapshot, commands, context)}.json"
        raw = self._workspace.read_text(path)
        if raw is not None:
            loaded = self._read(raw, commands)
            if loaded is not None:
                return loaded
        results = verify(commands, stopped)
        complete = {result.command for result in results} == set(commands)
        if complete and not unavailable_checks(results, commands):
            self._workspace.write_text(
                path,
                stable_json(
                    [
                        {
                            "command": result.command,
                            "exit_code": result.exit_code,
                            "errors": result.errors,
                        }
                        for result in results
                    ]
                ),
            )
        return results

    def _read(self, raw: str, commands: Sequence[str]) -> tuple[VerifyResult, ...] | None:
        try:
            rows = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(rows, list):
            return None
        results: list[VerifyResult] = []
        for row in rows:
            if not isinstance(row, dict):
                return None
            command, code, errors = row.get("command"), row.get("exit_code"), row.get("errors")
            if (
                not isinstance(command, str)
                or not isinstance(code, int)
                or isinstance(code, bool)
                or not isinstance(errors, list)
                or not all(isinstance(error, str) for error in errors)
            ):
                return None
            results.append(VerifyResult(command, code, errors=tuple(errors)))
        return tuple(results) if {result.command for result in results} == set(commands) else None


class ImplementationSession:
    def __init__(
        self,
        commands: tuple[str, ...],
        baseline: tuple[VerifyResult, ...],
        verify: Verify,
        monotonic: Callable[[], float],
        cap_usd: float = 0.0,
        max_repairs: int = MAX_REPAIRS,
        timeout_s: float = 900.0,
        stepped: bool = False,
        max_turns: int = 0,
        measured: Callable[[str, float], None] | None = None,
        snapshot: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.commands = commands
        self.baseline = baseline
        self._verify = verify
        self._monotonic = monotonic
        self._started = monotonic()
        self._cap = cap_usd
        self._max_repairs = min(MAX_REPAIRS, max(0, max_repairs))
        self._timeout = timeout_s
        self._max_turns = max_turns
        self._planning = stepped
        self._snapshot = snapshot if stepped else None
        self._planning_fingerprint = (
            implementation_fingerprint(self._snapshot()) if self._snapshot is not None else ""
        )
        self._measured = measured
        self._repair_started: float | None = None
        self._expired = threading.Event()
        self._checks: list[VerificationDelta] = []
        self._steps: tuple[ImplementationStep, ...] = (
            () if stepped else (ImplementationStep("Implementation", "running"),)
        )
        self._index = 0
        self._repairs = 0
        self._reason = ""
        self._engine_subtype = ""

    def stopped(self) -> bool:
        return self._expired.is_set() or self._monotonic() - self._started >= self._timeout

    @contextmanager
    def running(self, halt: Callable[[], None]) -> Iterator[None]:
        def expire() -> None:
            self._expired.set()
            halt()

        timer = threading.Timer(max(0.0, self._timeout), expire)
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()
            if self._expired.is_set():
                self._reason = "time_limit"

    def _rail(self, result: RunResult) -> str:
        if self.stopped():
            return "time_limit"
        if self._max_turns > 0 and result.num_turns >= self._max_turns:
            return "turn_limit"
        if result.cost_usd is None:
            return "cost_unknown"
        if self._cap > 0 and result.cost_usd + MIN_REPAIR_HEADROOM_USD >= self._cap:
            return "cost_limit"
        return ""

    def _send(self, result: RunResult, text: str, send: Callable[[str], bool]) -> bool:
        self._reason = self._rail(result)
        if self._reason:
            return False
        if not send(text):
            self._reason = "session_closed"
            return False
        return True

    def _step(self, state: str) -> None:
        if not self._steps:
            return
        steps = list(self._steps)
        steps[self._index] = replace(steps[self._index], state=state)
        self._steps = tuple(steps)

    def on_result(self, result: RunResult, send: Callable[[str], bool]) -> None:
        if self.report.passed:
            return
        if not result.ok or self._reason:
            if not result.ok:
                self._engine_subtype = result.subtype
            self._reason = self._reason or engine_stop_reason(
                result.subtype, result.terminal_reason
            )
            self._step("failed")
            return
        if self.stopped():
            self._reason = "time_limit"
            self._step("failed")
            return
        if self._planning:
            if (
                self._snapshot is not None
                and implementation_fingerprint(self._snapshot()) != self._planning_fingerprint
            ):
                self._reason = "planning_changes"
                return
            self._steps = planned_steps(result.text)
            self._planning = False
            if not self._steps:
                self._reason = "invalid_step_plan"
                return
            self._start_step(result, send)
            return
        if self._repair_started is not None and self._measured is not None:
            self._measured("repair", self._monotonic() - self._repair_started)
        self._repair_started = None
        started = self._monotonic()
        results = self._verify(self.commands, self.stopped)
        if self._measured is not None:
            self._measured("verification", self._monotonic() - started)
        checked = verification_delta(self.baseline, results, self.commands)
        self._checks.append(checked)
        if checked.passed and not self.stopped():
            self._step("green")
            if self._index + 1 < len(self._steps):
                self._index += 1
                self._start_step(result, send)
            return
        self._step("failed")
        if self.stopped():
            self._reason = "time_limit"
            return
        if self._repairs >= self._max_repairs:
            self._reason = "repair_limit"
            return
        errors = repair_feedback(checked.introduced)
        text = (
            "Cuanta verification found these NEW errors (pre-existing errors are excluded):\n"
            f"{errors}\nRepair only this step and these errors; preserve all completed steps. "
            "Stop after the repair so cuanta can verify again."
        )
        if self._send(result, text, send):
            self._repairs += 1
            self._repair_started = self._monotonic()
            self._step("repairing")

    def _start_step(self, result: RunResult, send: Callable[[str], bool]) -> None:
        title = self._steps[self._index].title
        text = (
            f"Cuanta authorizes step {self._index + 1}/{len(self._steps)}: {title}\n"
            "All earlier steps are green. Implement only this step, then stop for verification."
        )
        self._step("running" if self._send(result, text, send) else "blocked")

    @property
    def report(self) -> ImplementationReport:
        return ImplementationReport(
            tuple(self._checks),
            self._steps,
            self._repairs,
            self._reason,
            self._max_repairs,
            self._timeout,
            max(0.0, self._monotonic() - self._started),
            engine_subtype=self._engine_subtype,
        )

    def settle(self, outcome: EngineOutcome) -> EngineOutcome:
        report = self.report
        if report.passed:
            return outcome
        result = outcome.result
        if result is None:
            return outcome
        reason = report.reason or "verification_failed"
        remaining = report.checks[-1].introduced if report.checks else ()
        text = result.text + "\n\nCuanta verification: " + reason
        if remaining:
            text += "\n" + "\n".join(remaining)
        if report.engine_subtype and not result.ok:
            return replace(outcome, result=replace(result, text=text))
        return replace(
            outcome,
            result=replace(
                result, ok=False, subtype="error_implementation", terminal_reason=reason, text=text
            ),
        )
