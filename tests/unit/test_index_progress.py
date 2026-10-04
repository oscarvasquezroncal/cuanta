from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.application.code_index import IndexService
from cuanta.application.progress import RecordingSink, SlowSteps
from cuanta.bootstrap import Container
from cuanta.domain import index_limit
from cuanta.domain.code_index import IndexStatus
from cuanta.domain.index_limit import IndexTooLarge
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status, StepFinished, StepStarted, file_count, took
from tests.fakes import FakeRunner, VirtualTime

REQUEST = MandateRequest("bug", "fix total", "wrong sum", out_of_scope="payments")
INDEX = msg("progress.index")


@pytest.fixture
def virtual(monkeypatch: pytest.MonkeyPatch) -> VirtualTime:
    time = VirtualTime()

    def slow_steps(self: Container) -> SlowSteps:
        return SlowSteps(self.progress, time.monotonic, time.schedule)

    monkeypatch.setattr(Container, "slow_steps", slow_steps)
    return time


def timed_updates(
    monkeypatch: pytest.MonkeyPatch, time: VirtualTime, first: float, then: float
) -> list[float]:
    original = IndexService.update
    taken: list[float] = []

    def update(self: IndexService) -> IndexStatus:
        seconds = then if taken else first
        taken.append(seconds)
        time.advance(seconds)
        return original(self)

    monkeypatch.setattr(IndexService, "update", update)
    return taken


def container_for(root: Path, sink: RecordingSink) -> Container:
    root.mkdir(exist_ok=True)
    (root / "cart.py").write_text("total = 1\n", encoding="utf-8")
    container = Container.for_project(root)
    container.runner = FakeRunner()
    container.progress = sink
    return container


def test_only_the_first_of_the_index_updates_in_a_preparation_is_a_step(
    tmp_path: Path, virtual: VirtualTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = timed_updates(monkeypatch, virtual, 52.0, 3.0)
    sink = RecordingSink()
    container = container_for(tmp_path, sink)
    try:
        container.change_plan(REQUEST)
        container.context_pack(REQUEST)
        container.refresh_index()
    finally:
        container.close()
    assert taken == [52.0, 3.0, 3.0]
    assert sink.events == [
        StepStarted("index", "index", INDEX),
        StepFinished("index", Status.OK, "1 file · 52 s", took(52.0, file_count(1)), 52.0),
    ]


def test_an_isolated_copy_reports_its_own_first_index_update(
    tmp_path: Path, virtual: VirtualTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    timed_updates(monkeypatch, virtual, 52.0, 52.0)
    sink = RecordingSink()
    container = container_for(tmp_path / "project", sink)
    (tmp_path / "copy").mkdir()
    copy = container.sandbox_container(tmp_path / "copy")
    try:
        container.refresh_index()
        container.refresh_index()
        copy.refresh_index()
        copy.refresh_index()
    finally:
        copy.close()
        container.close()
    steps = [event for event in sink.events if isinstance(event, StepStarted | StepFinished)]
    assert len(steps) == len(sink.events)
    assert [(type(event), event.key) for event in steps] == [
        (StepStarted, "index"),
        (StepFinished, "index"),
        (StepStarted, "index"),
        (StepFinished, "index"),
    ]


def test_an_index_over_the_ceiling_closes_its_step_as_failed_before_the_error(
    tmp_path: Path, virtual: VirtualTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 1)
    sink = RecordingSink()
    container = container_for(tmp_path, sink)
    service = container.index_service()
    service.close()
    timed_updates(monkeypatch, virtual, 4.0, 4.0)
    (tmp_path / "other.py").write_text("other = 2\n", encoding="utf-8")
    try:
        with pytest.raises(IndexTooLarge):
            container.refresh_index()
    finally:
        container.close()
    assert sink.events == [
        StepStarted("index", "index", INDEX),
        StepFinished("index", Status.FAIL, "4 s", took(4.0), 4.0),
    ]


def test_a_slow_ceiling_check_reports_failure_before_creating_the_database(
    tmp_path: Path, virtual: VirtualTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuanta.adapters.system.index_inventory import LocalIndexInventory

    original = LocalIndexInventory.paths

    def paths(self: LocalIndexInventory) -> tuple[str, ...]:
        virtual.advance(4.0)
        return original(self)

    monkeypatch.setattr(LocalIndexInventory, "paths", paths)
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 1)
    sink = RecordingSink()
    container = container_for(tmp_path, sink)
    (tmp_path / "other.py").write_text("other = 2\n", encoding="utf-8")
    try:
        with pytest.raises(IndexTooLarge):
            container.refresh_index()
    finally:
        container.close()
    assert not (tmp_path / ".cuanta" / "index.db").exists()
    assert sink.events == [
        StepStarted("index", "index", INDEX),
        StepFinished("index", Status.FAIL, "4 s", took(4.0), 4.0),
    ]


def test_a_container_without_a_sink_indexes_silently(
    tmp_path: Path, virtual: VirtualTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    timed_updates(monkeypatch, virtual, 52.0, 52.0)
    container = container_for(tmp_path, RecordingSink())
    container.progress = None
    try:
        container.refresh_index()
    finally:
        container.close()
    assert virtual.timers == []
