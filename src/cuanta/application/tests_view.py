from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cuanta.application.gateway import GatewayReport
from cuanta.domain.ledger import SignatureRecord, TestRunRecord
from cuanta.ports.ledger import Ledger


@dataclass(frozen=True, slots=True)
class Hairball:
    id: str
    tests: int
    location: str
    triage: str
    error: str
    verbatim: str
    first_test: str


@dataclass(frozen=True, slots=True)
class TestsSummary:
    runner: str
    status: str
    passed: int
    failed: int
    errored: int
    skipped: int
    duration_s: float
    capsule: str
    started_at: str
    hairballs: tuple[Hairball, ...]

    @property
    def green(self) -> bool:
        return self.failed + self.errored == 0 and not self.hairballs


def from_report(report: GatewayReport, started_at: str = "") -> TestsSummary:
    outcome = report.outcome
    triage = dict(report.triage)
    return TestsSummary(
        runner=report.runner,
        status=report.status.value,
        passed=outcome.passed,
        failed=outcome.failed,
        errored=outcome.errored,
        skipped=outcome.skipped,
        duration_s=outcome.duration_s,
        capsule=report.capsule,
        started_at=started_at,
        hairballs=tuple(
            Hairball(
                item.id,
                item.tests,
                item.location,
                triage.get(item.id, ""),
                item.error,
                item.verbatim,
                item.first,
            )
            for item in report.signatures
        ),
    )


def from_records(record: TestRunRecord, signatures: Sequence[SignatureRecord]) -> TestsSummary:
    return TestsSummary(
        runner=record.runner,
        status=record.status,
        passed=record.passed,
        failed=record.failed,
        errored=record.errored,
        skipped=record.skipped,
        duration_s=record.duration_s,
        capsule=record.capsule_id,
        started_at=record.started_at,
        hairballs=tuple(
            Hairball(
                item.signature_id,
                item.tests,
                item.location,
                "",
                item.error,
                item.verbatim,
                item.first_test,
            )
            for item in signatures
        ),
    )


class LatestTests:
    def __init__(
        self, ledger_factory: Callable[[], Ledger], has_ledger: Callable[[], bool]
    ) -> None:
        self._ledger_factory = ledger_factory
        self._has_ledger = has_ledger

    def run(self) -> TestsSummary | None:
        if not self._has_ledger():
            return None
        ledger = self._ledger_factory()
        try:
            records = ledger.test_runs(limit=1)
            if not records:
                return None
            return from_records(records[0], ledger.signatures(records[0].id))
        finally:
            ledger.close()
