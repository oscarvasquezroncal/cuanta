from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.code_index import IndexService
from cuanta.application.forecast import PlannedForecast
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.progress import RecordingSink, SlowSteps
from cuanta.bootstrap import Container
from cuanta.domain.code_index import IndexStatus
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import Message, msg
from cuanta.domain.progress import Status, StepFinished, StepStarted, took
from tests.fakes import FakeRunner, VirtualTime

REQUEST = MandateRequest("bug", "fix total", "wrong sum", out_of_scope="payments")


@pytest.fixture
def virtual(monkeypatch: pytest.MonkeyPatch) -> VirtualTime:
    time = VirtualTime()

    def slow_steps(self: Container) -> SlowSteps:
        return SlowSteps(self.progress, time.monotonic, time.schedule)

    monkeypatch.setattr(Container, "slow_steps", slow_steps)
    return time


def slow_later_updates(
    monkeypatch: pytest.MonkeyPatch, time: VirtualTime, seconds: float
) -> list[float]:
    original = IndexService.update
    taken: list[float] = []

    def update(self: IndexService) -> IndexStatus:
        spent = seconds if taken else 0.0
        taken.append(spent)
        time.advance(spent)
        return original(self)

    monkeypatch.setattr(IndexService, "update", update)
    return taken


def prepare(root: Path, sink: RecordingSink) -> None:
    root.mkdir()
    container = Container.for_project(root)
    container.runner = FakeRunner()
    container.workspace().write_text("docs/MANDATE_TEMPLATE.md", "```\n=== REQUEST ===\n```\n")
    container.progress = sink
    try:
        container.mandate_flow(MemoryLedger()).prepare(
            REQUEST, 0, MandateOptions(simple=True), preview=True
        )
    finally:
        container.close()


def test_a_slow_plan_is_a_step_and_a_quick_forecast_is_not(
    tmp_path: Path, virtual: VirtualTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = slow_later_updates(monkeypatch, virtual, 3.0)
    sink = RecordingSink()
    prepare(tmp_path / "project", sink)
    spent = sum(taken)
    assert spent >= 3.0
    assert sink.events == [
        StepStarted("plan", "plan", msg("progress.plan")),
        StepFinished("plan", Status.OK, f"{spent:,.0f} s", took(spent), spent),
    ]


def test_a_slow_forecast_is_its_own_step_after_the_plan(
    tmp_path: Path, virtual: VirtualTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = MandateFlow._forecast

    def forecast(
        self: MandateFlow, *args: Any, **kwargs: Any
    ) -> tuple[PlannedForecast | None, Message | None]:
        planned = original(self, *args, **kwargs)
        virtual.advance(2.75)
        return planned

    monkeypatch.setattr(MandateFlow, "_forecast", forecast)
    sink = RecordingSink()
    prepare(tmp_path / "project", sink)
    assert sink.events == [
        StepStarted("forecast", "forecast", msg("progress.forecast")),
        StepFinished("forecast", Status.OK, "3 s", took(2.75), 2.75),
    ]


def test_a_quick_preparation_publishes_nothing(
    tmp_path: Path, virtual: VirtualTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_later_updates(monkeypatch, virtual, 0.5)
    sink = RecordingSink()
    prepare(tmp_path / "project", sink)
    assert sink.events == []
