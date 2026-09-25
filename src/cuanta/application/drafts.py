from __future__ import annotations

from collections.abc import Callable

from cuanta.domain.drafts import LAST, Draft, newest, overflow
from cuanta.ports.drafts import DraftStore


class Drafts:
    def __init__(self, store: DraftStore, clock_iso: Callable[[], str]) -> None:
        self._store = store
        self._clock_iso = clock_iso

    def last(self) -> str:
        found = self._store.get(LAST)
        return found.story if found is not None else ""

    def listed(self, current: str = "") -> tuple[Draft, ...]:
        return newest(self._store.all(), exclude=current)

    def autosave(self, draft_id: str, story: str) -> None:
        if not story.strip():
            self._store.remove(draft_id)
            return
        self._store.put(Draft(draft_id, story, self._clock_iso()))
        for stale in overflow(self._store.all()):
            self._store.remove(stale)

    def launched(self, draft_id: str, story: str) -> None:
        self._store.remove(draft_id)
        if story.strip():
            self._store.put(Draft(LAST, story, self._clock_iso()))

    def load(self, draft_id: str) -> str:
        found = self._store.get(draft_id)
        return found.story if found is not None else ""
