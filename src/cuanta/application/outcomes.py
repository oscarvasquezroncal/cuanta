from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cuanta.application.trials import TrialStore
from cuanta.domain.errors import DomainFailure
from cuanta.domain.ledger import Run
from cuanta.domain.outcomes import (
    ACCEPTED,
    CROSS_KIND,
    REJECTED,
    is_attempt,
    pipeline_running,
)
from cuanta.domain.sandbox import SANDBOX_MODE
from cuanta.ports.ledger import Ledger


@dataclass(frozen=True, slots=True)
class OutcomeChange:
    run_id: str
    outcome: str
    at: str
    reason: str = ""


class RunOutcomes:
    def __init__(
        self,
        ledger: Ledger,
        trials: TrialStore,
        clock_iso: Callable[[], str],
        learn_run: Callable[[str], None] | None = None,
    ) -> None:
        self._learn_run = learn_run
        self._ledger = ledger
        self._trials = trials
        self._clock_iso = clock_iso

    def root(self, run_id: str) -> Run:
        run = self._ledger.get_run(run_id)
        if run is None:
            raise DomainFailure(f"no run {run_id}", "list them: cuanta runs list")
        if run.kind == CROSS_KIND and run.parent_id:
            parent = self._ledger.get_run(run.parent_id)
            run = parent if parent is not None else run
        if not is_attempt(run):
            raise DomainFailure(
                f"run {run.id} is a {run.kind or 'plain'} run; only mandates carry an outcome",
                "accept or reject the mandate that started it",
            )
        return run

    def roles(self, root: Run) -> tuple[Run, ...]:
        if root.kind != CROSS_KIND or root.parent_id:
            return ()
        found = self._ledger.runs(kind=CROSS_KIND, since=root.started_at)
        return tuple(run for run in found if run.parent_id == root.id)

    def pending(self, run_id: str) -> Run:
        run = self.root(run_id)
        if pipeline_running(run, self.roles(run), self._clock_iso()):
            raise DomainFailure(f"run {run.id} is still running", "wait for it to finish")
        if run.outcome:
            raise DomainFailure(f"run {run.id} is already {run.outcome}", "nothing to do")
        return run

    def _mark(self, run: Run, outcome: str, reason: str) -> OutcomeChange:
        now = self._clock_iso()
        if not self._ledger.set_run_outcome(run.id, outcome, now, reason):
            raise DomainFailure(f"run {run.id} was decided meanwhile", "check cuanta runs show")
        self._ledger.set_routing_accepted(run.id, outcome == ACCEPTED)
        if self._learn_run is not None:
            self._learn_run(run.id)
        return OutcomeChange(run.id, outcome, now, reason)

    def accept(self, run_id: str, reason: str = "") -> OutcomeChange:
        return self._mark(self.pending(run_id), ACCEPTED, reason)

    def reject(self, run_id: str, reason: str = "") -> OutcomeChange:
        run = self.pending(run_id)
        if run.mode != SANDBOX_MODE or run.end_reason == "error_sandbox_record":
            return self._mark(run, REJECTED, reason)
        self._trials.discard(run.id, reason)
        if self._learn_run is not None:
            self._learn_run(run.id)
        stored = self._ledger.get_run(run.id)
        return OutcomeChange(run.id, REJECTED, stored.outcome_at if stored else "", reason)
