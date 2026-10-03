from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from cuanta.application.cache_state import PrefixQuery
from cuanta.application.doctor import (
    TEMPLATE_CHECK,
    TEMPLATE_MISSING,
    TEMPLATE_UNUSABLE,
    CheckResult,
    Doctor,
    DoctorReport,
    result,
)
from cuanta.domain.cache import UNKNOWN_PREFIX, PrefixWindow
from cuanta.domain.consumption import daily_tokens, window_start
from cuanta.domain.detection import ForgeState
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.ledger import Run
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status
from cuanta.domain.real_costs import CostReport, cost_report, costs_since
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
    prefix: PrefixWindow = UNKNOWN_PREFIX
    costs: CostReport | None = None
    queued: int = 0
    queue_unreadable: bool = False

    @property
    def initialized(self) -> bool:
        return self.report.detection.forge_state is ForgeState.INITIALIZED

    @property
    def template_missing(self) -> bool:
        return self._template_key() in TEMPLATE_MISSING

    @property
    def template_unusable(self) -> bool:
        return self._template_key() == TEMPLATE_UNUSABLE

    def _template_key(self) -> str:
        found = self.check(TEMPLATE_CHECK)
        return found.message.key if found is not None and found.message is not None else ""

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
    for check in report.checks:
        if check.message is not None and check.message.key == TEMPLATE_UNUSABLE:
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
        prefix: PrefixQuery,
        engine: str,
        now_iso: Callable[[], str],
        queued: Callable[[], int] | None = None,
    ) -> None:
        self._now_iso = now_iso
        self._queued = queued
        self._doctor = doctor
        self._ledger_factory = ledger_factory
        self._has_ledger = has_ledger
        self._forge_version = forge_version
        self._today = today
        self._prefix = prefix
        self._engine = engine

    def _queue(self) -> tuple[int, bool]:
        if self._queued is None:
            return 0, False
        try:
            return self._queued(), False
        except EnvironmentFailure:
            return 0, True

    def run(self) -> HomeSnapshot:
        report = self._doctor.run()
        today = self._today()
        runs: tuple[Run, ...] = ()
        daily: tuple[int, ...] = (0,) * 7
        costs: CostReport | None = None
        if self._has_ledger():
            ledger = self._ledger_factory()
            try:
                runs = ledger.runs(limit=RECENT_RUNS)
                since = window_start(today).isoformat()
                daily = daily_tokens(ledger.events(EventQuery(since=since)), today)
                now = self._now_iso()
                start = costs_since(now)
                costs = cost_report(ledger.runs(since=start), start, now)
            finally:
                ledger.close()
        queued, unreadable = self._queue()
        return HomeSnapshot(
            report,
            runs,
            daily,
            next_step(report),
            self._forge_version(),
            self._prefix.run(self._engine),
            costs,
            queued,
            unreadable,
        )
