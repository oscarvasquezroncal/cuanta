from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cuanta.domain.costs import sum_costs
from cuanta.domain.loop import Iteration, StopReason, next_stop
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status, finished, note, started
from cuanta.ports.progress import ProgressSink


@dataclass(frozen=True, slots=True)
class TestStep:
    status: str
    signatures: int


@dataclass(frozen=True, slots=True)
class FixStep:
    run_id: str
    ok: bool
    cost_usd: float | None


@dataclass(frozen=True, slots=True)
class LoopReport:
    loop_id: str
    iterations: tuple[Iteration, ...]
    stop: StopReason
    spent_usd: float | None
    final_status: str

    @property
    def ok(self) -> bool:
        return self.stop is StopReason.GREEN


class FixLoop:
    def __init__(
        self,
        run_tests: Callable[[str], TestStep],
        fix: Callable[[str, int], FixStep],
        progress: ProgressSink,
    ) -> None:
        self._run_tests = run_tests
        self._fix = fix
        self._progress = progress

    def run(self, loop_id: str, max_iterations: int, budget_usd: float) -> LoopReport:
        iterations: list[Iteration] = []
        spent: float | None = 0.0
        number = 0
        while True:
            self._progress.publish(started(f"test-{number}", msg("loop.test")))
            step = self._run_tests(loop_id)
            status = Status.OK if step.status == "green" else Status.FAIL
            self._progress.publish(
                finished(f"test-{number}", status, msg(f"loop.suite_{step.status}"))
            )
            stop = next_stop(step.status, number, max_iterations, spent, budget_usd)
            if stop is not None:
                if stop is StopReason.GREEN and number == 0:
                    self._progress.publish(note(Status.INFO, msg("loop.nap")))
                return LoopReport(loop_id, tuple(iterations), stop, spent, step.status)
            number += 1
            key = f"fix-{number}"
            self._progress.publish(
                started(key, msg("loop.pounce", number=number, total=max_iterations))
            )
            fix = self._fix(loop_id, number)
            spent = sum_costs((spent, fix.cost_usd))
            iterations.append(
                Iteration(number, step.status, step.signatures, fix.run_id, fix.ok, fix.cost_usd)
            )
            outcome = Status.OK if fix.ok else Status.FAIL
            self._progress.publish(
                finished(
                    key,
                    outcome,
                    msg("loop.cost", cost=f"{fix.cost_usd:.2f}")
                    if fix.cost_usd is not None
                    else msg("loop.cost_unknown"),
                )
            )
            if not fix.ok:
                stop = StopReason.ENGINE_ERROR
                return LoopReport(loop_id, tuple(iterations), stop, spent, step.status)
