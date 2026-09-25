from __future__ import annotations

import time
from datetime import UTC, datetime


class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic(self) -> float:
        return time.monotonic()


class FixedClock:
    def __init__(self, iso: str = "2026-01-01T00:00:00Z", ms: int = 1_767_225_600_000) -> None:
        self._iso = iso
        self._ms = ms
        self._tick = 0.0

    def now_iso(self) -> str:
        return self._iso

    def now_ms(self) -> int:
        return self._ms

    def monotonic(self) -> float:
        self._tick += 0.5
        return self._tick
