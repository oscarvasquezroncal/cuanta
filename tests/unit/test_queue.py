from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.storage.queue_file import (
    LOCK_FILE,
    QUEUE_FILE,
    WRITE_LOCK_FILE,
    FileQueueStore,
)
from cuanta.application.cache_state import PrefixQuery
from cuanta.application.doctor import Doctor, DoctorReport
from cuanta.application.home import HomeQuery
from cuanta.application.queue import MandateQueue
from cuanta.domain.cache import UNKNOWN_PREFIX, PrefixState, PrefixWindow
from cuanta.domain.errors import DomainFailure, EnvironmentFailure
from cuanta.domain.messages import english
from cuanta.domain.queue import (
    QueueEntry,
    QueueOutcome,
    QueueSlot,
    QueueStatus,
    queue_order,
    warm_message,
    warm_payload,
)
from cuanta.tui.i18n import Catalog
from tests.tui.fakes import CHECKS, DETECTION

UNTIL = datetime(2026, 9, 29, 19, 32, tzinfo=UTC)
Launch = Callable[[QueueSlot], QueueOutcome]


def slot(entry_id: str, provider: str, model: str = "", copy: bool = False) -> QueueSlot:
    entry = QueueEntry(entry_id, "2026-09-29T19:00:00Z", ("--what", entry_id))
    return QueueSlot(entry, provider, model, copy=copy)


def test_queue_order_keeps_each_engine_and_model_together_and_is_stable() -> None:
    slots = [
        slot("a", "claude", "opus"),
        slot("b", "codex"),
        slot("c", "claude", "sonnet"),
        slot("d", "claude", "OPUS "),
        slot("e", "codex"),
        slot("f", "claude", "sonnet"),
        slot("g", "codex", "gpt-6-sol"),
    ]
    ordered = [item.entry.id for item in queue_order(slots)]
    assert ordered == ["a", "d", "b", "e", "c", "f", "g"]
    assert queue_order(queue_order(slots)) == queue_order(slots)
    assert queue_order([]) == ()
    same = [slot("x", "claude"), slot("y", "claude"), slot("z", "claude")]
    assert queue_order(same) == tuple(same)
    copies = [slot("p", "claude"), slot("s", "claude", copy=True), slot("q", "claude")]
    assert [item.entry.id for item in queue_order(copies)] == ["p", "q", "s"]


def test_the_warm_window_reads_until_hh_mm_or_unknown() -> None:
    warm = PrefixWindow(PrefixState.WARM, UNTIL)
    cold = PrefixWindow(PrefixState.COLD, UNTIL)
    assert english(warm_message(warm, "14:32")) == "warm prefix until 14:32"
    assert english(warm_message(cold, "14:32")).startswith("prefix cold since 14:32")
    assert english(warm_message(UNKNOWN_PREFIX, "")).startswith("warm prefix: unknown")
    timeless = PrefixWindow(PrefixState.WARM)
    assert english(warm_message(timeless, "")).startswith("warm prefix: unknown")
    spanish = Catalog("es")
    assert spanish.message(warm_message(warm, "14:32")) == "prefijo caliente hasta 14:32"
    assert spanish.message(warm_message(cold, "14:32")).startswith("prefijo frío desde 14:32")
    unknown = spanish.message(warm_message(UNKNOWN_PREFIX, ""))
    assert unknown.startswith("prefijo caliente: desconocido")
    assert warm_payload(warm) == {"state": "warm", "until": "2026-09-29T19:32:00+00:00"}
    assert warm_payload(cold)["state"] == "cold"
    assert warm_payload(UNKNOWN_PREFIX) == {"state": "unknown", "until": None}
    assert warm_payload(timeless) == {"state": "unknown", "until": None}


def test_a_slot_title_is_one_short_line() -> None:
    long = QueueSlot(QueueEntry("q1", "", ()), "claude", request="fix\n  the " + "x" * 80)
    assert long.title.startswith("fix the x") and long.title.endswith("…")
    assert len(long.title) == 60


def queue_in(tmp_path: Path) -> tuple[MandateQueue, FileQueueStore, list[str]]:
    store = FileQueueStore(tmp_path / ".cuanta")
    readied: list[str] = []
    queue = MandateQueue(store, lambda: "2026-09-29T19:00:00Z", lambda: readied.append("ready"))
    return queue, store, readied


def added(queue: MandateQueue, count: int) -> list[str]:
    return [queue.add(["--what", str(index)]).id for index in range(count)]


def slots_of(queue: MandateQueue, providers: dict[str, str]) -> list[QueueSlot]:
    return [QueueSlot(entry, providers[entry.id]) for entry in queue.entries()]


def failing(ids: set[str], seen: list[str]) -> Launch:
    def launch(item: QueueSlot) -> QueueOutcome:
        seen.append(item.entry.id)
        if item.entry.id in ids:
            return QueueOutcome(item, QueueStatus.FAILED, "red")
        return QueueOutcome(item, QueueStatus.OK)

    return launch


def test_run_stops_at_the_first_failure_and_keeps_what_did_not_finish(tmp_path: Path) -> None:
    queue, store, readied = queue_in(tmp_path)
    assert added(queue, 4) == ["q1", "q2", "q3", "q4"]
    assert readied == ["ready"] * 4
    providers = {"q1": "claude", "q2": "codex", "q3": "claude", "q4": "codex"}
    seen: list[str] = []
    report = queue.run(slots_of(queue, providers), failing({"q3"}, seen), keep_going=False)
    assert seen == ["q1", "q3"]
    assert [(item.slot.entry.id, item.status) for item in report.outcomes] == [
        ("q1", QueueStatus.OK),
        ("q3", QueueStatus.FAILED),
        ("q2", QueueStatus.NOT_RUN),
        ("q4", QueueStatus.NOT_RUN),
    ]
    assert report.outcomes[1].detail == "red"
    assert not report.ok and report.left == 3
    assert [entry.id for entry in store.entries()] == ["q2", "q3", "q4"]
    assert not (tmp_path / ".cuanta" / LOCK_FILE).exists()


def test_keep_going_runs_everything_and_keeps_only_failures(tmp_path: Path) -> None:
    queue, store, _ = queue_in(tmp_path)
    added(queue, 3)
    seen: list[str] = []
    providers = dict.fromkeys(("q1", "q2", "q3"), "claude")
    report = queue.run(slots_of(queue, providers), failing({"q1"}, seen), keep_going=True)
    assert seen == ["q1", "q2", "q3"]
    assert [entry.id for entry in store.entries()] == ["q1"]
    assert report.left == 1 and not report.ok
    clean = queue.run(slots_of(queue, {"q1": "claude"}), failing(set(), seen), keep_going=False)
    assert clean.ok and clean.left == 0 and store.entries() == ()


def test_a_second_run_is_refused_and_the_lock_is_released_after_an_error(tmp_path: Path) -> None:
    queue, store, _ = queue_in(tmp_path)
    added(queue, 1)
    slots = slots_of(queue, {"q1": "claude"})
    assert store.claim()
    with pytest.raises(DomainFailure, match="another cuanta queue run is active") as caught:
        queue.run(slots, failing(set(), []), keep_going=False)
    assert LOCK_FILE in caught.value.hint
    store.release()

    def interrupted(item: QueueSlot) -> QueueOutcome:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        queue.run(slots, interrupted, keep_going=False)
    assert store.claim()
    store.release()
    assert [entry.id for entry in store.entries()] == ["q1"]


def test_run_skips_mandates_that_left_the_queue_after_it_was_listed(tmp_path: Path) -> None:
    queue, store, _ = queue_in(tmp_path)
    added(queue, 3)
    slots = slots_of(queue, dict.fromkeys(("q1", "q2", "q3"), "claude"))
    assert store.remove({"q2"}) == ("q2",)
    seen: list[str] = []

    def launch(item: QueueSlot) -> QueueOutcome:
        seen.append(item.entry.id)
        assert store.remove({"q3"}) == ("q3",)
        return QueueOutcome(item, QueueStatus.OK)

    report = queue.run(slots, launch, keep_going=False)
    assert seen == ["q1"]
    assert [(item.slot.entry.id, item.status) for item in report.outcomes] == [
        ("q1", QueueStatus.OK),
        ("q2", QueueStatus.GONE),
        ("q3", QueueStatus.GONE),
    ]
    assert report.ok and report.left == 0 and store.entries() == ()


def test_queue_writes_take_a_short_write_lock_and_fail_loud_when_it_stays(
    tmp_path: Path,
) -> None:
    directory = tmp_path / ".cuanta"
    now = [0.0]
    pauses: list[float] = []

    def pause(seconds: float) -> None:
        pauses.append(seconds)
        now[0] += seconds

    store = FileQueueStore(directory, wait_s=0.1, clock=lambda: now[0], pause=pause)
    first = store.append(("--what", "one"), "")
    lock = directory / WRITE_LOCK_FILE
    assert first.id == "q1" and not lock.exists()
    lock.write_text("", encoding="utf-8")
    with pytest.raises(EnvironmentFailure, match="locked for writing") as caught:
        store.append(("--what", "two"), "")
    assert "delete it" in caught.value.hint and isinstance(caught.value.__cause__, OSError)
    with pytest.raises(EnvironmentFailure, match="locked for writing"):
        store.remove({"q1"})
    assert pauses and [entry.id for entry in store.entries()] == ["q1"]

    def released(seconds: float) -> None:
        lock.unlink()

    waiting = FileQueueStore(directory, wait_s=1.0, clock=lambda: 0.0, pause=released)
    assert waiting.append(("--what", "two"), "").id == "q2"
    assert not lock.exists()


def test_add_selected_and_clear(tmp_path: Path) -> None:
    queue, store, readied = queue_in(tmp_path)
    with pytest.raises(DomainFailure, match="nothing to queue"):
        queue.add([])
    assert readied == [] and not (tmp_path / ".cuanta").exists()
    added(queue, 3)
    assert [entry.id for entry in queue.selected(("q2",))] == ["q2"]
    assert len(queue.selected()) == 3
    with pytest.raises(DomainFailure, match="no queued mandate q7, q8"):
        queue.clear(("q8", "q7", "q1"))
    assert queue.clear(("q2",)) == ("q2",)
    assert queue.clear() == ("q1", "q3")
    assert queue.clear() == ()
    assert queue.add(["--what", "again"]).id == "q4"
    assert store.entries()[0].args == ("--what", "again")


BROKEN = (
    ("{", "not valid JSON"),
    ("[]", "not a JSON object"),
    ('{"version": 2, "next": 1, "items": []}', "version 2"),
    ('{"version": 1, "items": []}', "next id"),
    ('{"version": 1, "next": true, "items": []}', "next id"),
    ('{"version": 1, "next": 1, "items": {}}', "items"),
    ('{"version": 1, "next": 1, "items": [3]}', "not a list of mandates"),
    ('{"version": 1, "next": 1, "items": [{"id": "q1", "args": []}]}', "missing an id"),
    ('{"version": 1, "next": 1, "items": [{"id": "q1", "added_at": "", "args": [1]}]}', "id"),
)


def test_the_queue_file_is_versioned_json(tmp_path: Path) -> None:
    directory = tmp_path / ".cuanta"
    store = FileQueueStore(directory)
    assert store.entries() == () and not directory.exists()
    store.append(("--what", "ñandú"), "2026-09-29T19:00:00Z")
    path = directory / QUEUE_FILE
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "next": 2,
        "items": [{"id": "q1", "added_at": "2026-09-29T19:00:00Z", "args": ["--what", "ñandú"]}],
    }
    assert b"\r\n" not in path.read_bytes()
    assert list(directory.glob("*.tmp")) == []
    assert store.remove({"q9"}) == ()
    assert store.lock_file().endswith(LOCK_FILE)


@pytest.mark.parametrize(("text", "reason"), BROKEN)
def test_a_broken_queue_file_fails_loud_and_is_never_overwritten(
    tmp_path: Path, text: str, reason: str
) -> None:
    store = FileQueueStore(tmp_path)
    path = tmp_path / QUEUE_FILE
    path.write_text(text, encoding="utf-8")
    with pytest.raises(EnvironmentFailure, match=reason) as caught:
        store.entries()
    assert "delete it to empty the queue" in caught.value.hint
    with pytest.raises(EnvironmentFailure):
        store.append(("--what", "x"), "")
    with pytest.raises(EnvironmentFailure):
        store.remove({"q1"})
    assert path.read_text(encoding="utf-8") == text


class StubDoctor:
    def run(self) -> DoctorReport:
        return DoctorReport(DETECTION, CHECKS)


class StubPrefix:
    def run(self, engine: str) -> PrefixWindow:
        return UNKNOWN_PREFIX


def test_home_reports_how_many_mandates_are_queued() -> None:
    ledger = MemoryLedger()
    parts = (
        cast(Doctor, StubDoctor()),
        lambda: ledger,
        lambda: False,
        lambda: "0.4.0",
        lambda: date(2026, 1, 1),
        cast(PrefixQuery, StubPrefix()),
        "claude",
        lambda: "2026-01-01T00:00:00Z",
    )
    legacy = HomeQuery(*parts)
    queued = HomeQuery(*parts, lambda: 3)
    assert legacy.run().queued == 0 and not legacy.run().queue_unreadable
    assert queued.run().queued == 3 and not queued.run().queue_unreadable


def test_a_broken_queue_file_does_not_take_down_home(tmp_path: Path) -> None:
    (tmp_path / QUEUE_FILE).write_text("{", encoding="utf-8")
    store = FileQueueStore(tmp_path)
    ledger = MemoryLedger()
    home = HomeQuery(
        cast(Doctor, StubDoctor()),
        lambda: ledger,
        lambda: False,
        lambda: "0.4.0",
        lambda: date(2026, 1, 1),
        cast(PrefixQuery, StubPrefix()),
        "claude",
        lambda: "2026-01-01T00:00:00Z",
        lambda: len(store.entries()),
    )
    snapshot = home.run()
    assert snapshot.queue_unreadable and snapshot.queued == 0
    assert snapshot.report.checks == CHECKS and snapshot.forge_version == "0.4.0"
