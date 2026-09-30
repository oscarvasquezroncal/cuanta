from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

from cuanta.domain.ledger import LedgerEvent
from cuanta.ports.ledger import Ledger
from cuanta.ports.system import Clock


class PhaseRecorder:
    def __init__(self, clock: Clock | None = None, ledger: Ledger | None = None) -> None:
        self._clock = clock
        self._ledger = ledger
        self._pending: list[LedgerEvent] = []

    def reset(self) -> None:
        self._pending.clear()

    def start(self) -> float | None:
        return self._clock.monotonic() if self._clock is not None else None

    def elapsed(self, start: float | None) -> float | None:
        if self._clock is None or start is None:
            return None
        return max(0.0, self._clock.monotonic() - start)

    def record(self, phase: str, start: float | None, run_id: str = "", role: str = "") -> None:
        elapsed = self.elapsed(start)
        if elapsed is None:
            return
        self.seconds(phase, elapsed, run_id, role)

    def seconds(self, phase: str, seconds: float, run_id: str = "", role: str = "") -> None:
        if self._clock is None:
            return
        event = LedgerEvent(
            run_id=run_id,
            source="cuanta",
            agent=role,
            kind="phase_timing",
            duration_ms=round(seconds * 1000),
            ts=self._clock.now_iso(),
            raw=json.dumps({"phase": phase, "duration_ms": seconds * 1000}),
        )
        if run_id and self._ledger is not None:
            self._ledger.add_events([event])
        else:
            self._pending.append(event)

    def flush(self, run_id: str) -> None:
        if run_id and self._ledger is not None and self._pending:
            self._ledger.add_events([replace(event, run_id=run_id) for event in self._pending])
            self._pending.clear()

    @contextmanager
    def measure(self, phase: str, run_id: str = "", role: str = "") -> Iterator[None]:
        start = self.start()
        try:
            yield
        finally:
            self.record(phase, start, run_id, role)
