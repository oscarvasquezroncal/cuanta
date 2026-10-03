from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from cuanta.domain.engine import EngineOutcome, RunResult
from cuanta.domain.implementation import (
    ENGINE_STOPS,
    MAX_REPAIRS,
    MIN_REPAIR_HEADROOM_USD,
    SESSION_CLOSED,
    UNSETTLED_STATES,
    USER_STOP,
    ImplementationReport,
    ImplementationStep,
    VerificationDelta,
    baseline_key,
    engine_stop_reason,
    implementation_fingerprint,
    planned_steps,
    repair_feedback,
    settled_state,
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


def _never_stopped() -> bool:
    return False


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
        self._repair_spent = 0.0
        self._exit_code: int | None = None
        self._checks: list[VerificationDelta] = []
        self._steps: tuple[ImplementationStep, ...] = (
            () if stepped else (ImplementationStep("Implementation", "running"),)
        )
        self._index = 0
        self._repairs = 0
        self._reason = ""
        self._engine_subtype = ""

    def stopped(self) -> bool:
        return self._repair_spent >= self._timeout

    def _rail(self, result: RunResult) -> str:
        if self._max_turns > 0 and result.num_turns >= self._max_turns:
            return "turn_limit"
        if self._cap <= 0:
            return ""
        if result.cost_usd is None:
            return "cost_unknown"
        if result.cost_usd + MIN_REPAIR_HEADROOM_USD >= self._cap:
            return "cost_limit"
        return ""

    def _send(self, result: RunResult, text: str, send: Callable[[str], bool]) -> bool:
        self._reason = self._rail(result)
        if self._reason:
            return False
        if not send(text):
            self._reason = SESSION_CLOSED
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
            stopped = not result.ok and result.subtype in ENGINE_STOPS
            if not self._reason or (stopped and self._reason == SESSION_CLOSED):
                self._reason = engine_stop_reason(result.subtype, result.terminal_reason)
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
        repairing = self._repair_started is not None
        if self._repair_started is not None:
            repaired = self._monotonic() - self._repair_started
            self._repair_spent += repaired
            if self._measured is not None:
                self._measured("repair", repaired)
        self._repair_started = None
        started = self._monotonic()
        results = self._verify(self.commands, _never_stopped)
        verified = self._monotonic() - started
        if repairing:
            self._repair_spent += verified
        if self._measured is not None:
            self._measured("verification", verified)
        checked = verification_delta(self.baseline, results, self.commands)
        self._checks.append(checked)
        if checked.passed:
            self._step("green")
            if self._index + 1 < len(self._steps):
                self._index += 1
                self._start_step(result, send)
            return
        self._step("failed")
        if self._repairs >= self._max_repairs:
            self._reason = "repair_limit"
            return
        if self.stopped():
            self._reason = "repair_time_limit"
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
            exit_code=self._exit_code,
            cap_usd=self._cap,
            max_turns=self._max_turns,
            repair_elapsed_s=self._repair_spent,
        )

    def _ending(self, outcome: EngineOutcome) -> str:
        if outcome.cancelled:
            return USER_STOP
        if outcome.result is None:
            return "engine_exited"
        if self._planning or any(step.state in UNSETTLED_STATES for step in self._steps):
            return "unfinished"
        return "verification_failed"

    def settle(self, outcome: EngineOutcome) -> EngineOutcome:
        self._exit_code = outcome.exit_code
        yielding = outcome.cancelled and self._reason == SESSION_CLOSED
        if not self.report.passed and (not self._reason or yielding):
            self._reason = self._ending(outcome)
        self._steps = tuple(replace(step, state=settled_state(step.state)) for step in self._steps)
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
