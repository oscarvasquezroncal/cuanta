from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.spectrum import usage_events

WEEK = 7


def window_start(today: date, days: int = WEEK) -> date:
    return today - timedelta(days=days - 1)


def daily_tokens(events: Sequence[LedgerEvent], today: date, days: int = WEEK) -> tuple[int, ...]:
    first = window_start(today, days)
    by_run: dict[str, list[LedgerEvent]] = defaultdict(list)
    for event in events:
        by_run[event.run_id or event.session_id].append(event)
    totals = [0] * days
    for group in by_run.values():
        for event in usage_events(group):
            try:
                day = date.fromisoformat(event.ts[:10])
            except ValueError:
                continue
            offset = (day - first).days
            if 0 <= offset < days:
                totals[offset] += event.total_tokens
    return tuple(totals)
