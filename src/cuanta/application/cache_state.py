from __future__ import annotations

from collections.abc import Callable

from cuanta.application.recent_runs import latest_with_requests
from cuanta.domain.cache import (
    CACHE_ENGINE,
    UNKNOWN_PREFIX,
    PrefixWindow,
    moment_of,
    prefix_window,
)
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
        if (
            self._ttl_s <= 0
            or self._auth not in MEASURED_AUTH
            or self._api_key_present() != (self._auth == "api")
            or engine != CACHE_ENGINE
            or not self._has_ledger()
        ):
            return UNKNOWN_PREFIX
        ledger = self._ledger_factory()
        try:
            latest = latest_with_requests(ledger, RUN_SCAN, CACHE_ENGINE)
            events = latest[1] if latest is not None else ()
            if self._measured_model:
                first = first_request_event(events)
                if first is None or first.model != self._measured_model:
                    return UNKNOWN_PREFIX
            return prefix_window(events, self._ttl_s, moment_of(self._now_ms()))
        finally:
            ledger.close()
