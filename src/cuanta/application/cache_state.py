from __future__ import annotations

from collections.abc import Callable

from cuanta.application.recent_runs import latest_with_requests
from cuanta.domain.cache import (
    CACHE_ENGINE,
    UNKNOWN_PREFIX,
    CacheClock,
    PrefixWindow,
    moment_of,
    prefix_window,
)
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.report import first_request_event
from cuanta.ports.ledger import Ledger

RUN_SCAN = 20
MEASURED_AUTH = frozenset({"subscription", "api"})


class PrefixQuery:
    def __init__(
        self,
        ledger_factory: Callable[[], Ledger],
        has_ledger: Callable[[], bool],
        ttl_s: int,
        now_ms: Callable[[], int],
        auth: str,
        api_key_present: Callable[[], bool],
        measured_model: str = "",
    ) -> None:
        self._ledger_factory = ledger_factory
        self._has_ledger = has_ledger
        self._ttl_s = ttl_s
        self._now_ms = now_ms
        self._auth = auth
        self._api_key_present = api_key_present
        self._measured_model = measured_model

    def run(self, engine: str) -> PrefixWindow:
        events = self._events(engine)
        if events is None:
            return UNKNOWN_PREFIX
        return prefix_window(events, self._ttl_s, moment_of(self._now_ms()))

    def clock(self, engine: str) -> CacheClock:
        return CacheClock(self._ttl_s if self._trusted(engine) else 0, moment_of(self._now_ms()))

    def _trusted(self, engine: str) -> bool:
        return not (
            self._ttl_s <= 0
            or self._auth not in MEASURED_AUTH
            or self._api_key_present() != (self._auth == "api")
            or engine != CACHE_ENGINE
        )

    def _events(self, engine: str) -> tuple[LedgerEvent, ...] | None:
        if not self._trusted(engine) or not self._has_ledger():
            return None
        ledger = self._ledger_factory()
        try:
            latest = latest_with_requests(ledger, RUN_SCAN, CACHE_ENGINE)
            events = latest[1] if latest is not None else ()
            if self._measured_model:
                first = first_request_event(events)
                if first is None or first.model != self._measured_model:
                    return None
            return events
        finally:
            ledger.close()
