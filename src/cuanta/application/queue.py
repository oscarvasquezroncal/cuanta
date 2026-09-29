from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from cuanta.domain.errors import DomainFailure
from cuanta.domain.queue import QueueEntry, QueueOutcome, QueueSlot, QueueStatus, queue_order
from cuanta.ports.queue import QueueStore


@dataclass(frozen=True, slots=True)
class QueueRun:
    outcomes: tuple[QueueOutcome, ...]
    left: int

    @property
    def ok(self) -> bool:
        return all(
            outcome.status in {QueueStatus.OK, QueueStatus.GONE} for outcome in self.outcomes
        )


class MandateQueue:
    def __init__(
        self, store: QueueStore, now_iso: Callable[[], str], ready: Callable[[], None]
    ) -> None:
        self._store = store
        self._now_iso = now_iso
        self._ready = ready

    def entries(self) -> tuple[QueueEntry, ...]:
        return self._store.entries()

    def add(self, args: Sequence[str]) -> QueueEntry:
        if not args:
            raise DomainFailure(
                "nothing to queue", "pass the options of cuanta mandate, e.g. --type bug --what ..."
            )
        self._ready()
        return self._store.append(tuple(args), self._now_iso())

    def selected(self, ids: Collection[str] = ()) -> tuple[QueueEntry, ...]:
        entries = self._store.entries()
        missing = sorted(set(ids) - {entry.id for entry in entries})
        if missing:
            raise DomainFailure(
                f"no queued mandate {', '.join(missing)}", "cuanta queue list shows the ids"
            )
        return tuple(entry for entry in entries if not ids or entry.id in ids)

    def clear(self, ids: Collection[str] = ()) -> tuple[str, ...]:
        return self._store.remove({entry.id for entry in self.selected(ids)})

    def discard(self, ids: Collection[str]) -> tuple[str, ...]:
        return self._store.remove(set(ids))

    def _queued(self) -> frozenset[str]:
        return frozenset(entry.id for entry in self._store.entries())

    def run(
        self,
        slots: Sequence[QueueSlot],
        launch: Callable[[QueueSlot], QueueOutcome],
        keep_going: bool,
    ) -> QueueRun:
        if not self._store.claim():
            raise DomainFailure(
                "another cuanta queue run is active in this project",
                f"wait for it to end; if none is running, delete {self._store.lock_file()}",
            )
        outcomes: list[QueueOutcome] = []
        try:
            stopped = False
            for slot in queue_order(slots):
                if slot.entry.id not in self._queued():
                    outcomes.append(QueueOutcome(slot, QueueStatus.GONE))
                    continue
                if stopped:
                    outcomes.append(QueueOutcome(slot, QueueStatus.NOT_RUN))
                    continue
                outcome = launch(slot)
                outcomes.append(outcome)
                if outcome.status is QueueStatus.OK:
                    self._store.remove({slot.entry.id})
                elif not keep_going:
                    stopped = True
        finally:
            self._store.release()
        return QueueRun(tuple(outcomes), len(self._store.entries()))
