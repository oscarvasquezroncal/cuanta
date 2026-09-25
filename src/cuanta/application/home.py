from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from cuanta.application.doctor import CheckResult, Doctor, DoctorReport, result
from cuanta.domain.consumption import daily_tokens, window_start
from cuanta.domain.detection import ForgeState
from cuanta.domain.ledger import Run
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status
from cuanta.ports.ledger import EventQuery, Ledger

RECENT_RUNS = 8
INIT_COMMAND = "cuanta init"


@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    report: DoctorReport
    runs: tuple[Run, ...]
    daily: tuple[int, ...]
    next_step: CheckResult | None
    forge_version: str

    @property
    def initialized(self) -> bool:
        return self.report.detection.forge_state is ForgeState.INITIALIZED

    def check(self, name: str) -> CheckResult | None:
        for check in self.report.checks:
            if check.name == name:
                return check
        return None


QUIET_CHECKS = frozenset({"listener"})


def next_step(report: DoctorReport) -> CheckResult | None:
    if report.detection.forge_state is ForgeState.FRESH:
        return result("init", Status.INFO, msg("home.fresh"), INIT_COMMAND)
    for status in (Status.FAIL, Status.WARN):
        for check in report.checks:
            if check.status is status and check.fix and check.name not in QUIET_CHECKS:
                return check
    return None


class HomeQuery:
    def __init__(
        self,
        doctor: Doctor,
        ledger_factory: Callable[[], Ledger],
        has_ledger: Callable[[], bool],
        forge_version: Callable[[], str],
        today: Callable[[], date],
    ) -> None:
        self._doctor = doctor
        self._ledger_factory = ledger_factory
        self._has_ledger = has_ledger
        self._forge_version = forge_version
        self._today = today

    def run(self) -> HomeSnapshot:
        report = self._doctor.run()
        today = self._today()
        runs: tuple[Run, ...] = ()
        daily: tuple[int, ...] = (0,) * 7
        if self._has_ledger():
            ledger = self._ledger_factory()
            try:
                runs = ledger.runs(limit=RECENT_RUNS)
                since = window_start(today).isoformat()
                daily = daily_tokens(ledger.events(EventQuery(since=since)), today)
            finally:
                ledger.close()
        return HomeSnapshot(report, runs, daily, next_step(report), self._forge_version())
