from __future__ import annotations

from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.report import first_request_event
from cuanta.ports.ledger import EventQuery, Ledger


def latest_with_requests(
    ledger: Ledger, limit: int, engine: str = ""
) -> tuple[Run, tuple[LedgerEvent, ...]] | None:
    matched = 0
    for run in ledger.runs():
        if engine and run.engine != engine:
            continue
        matched += 1
        if limit and matched > limit:
            break
        events = ledger.events(EventQuery(run_id=run.id))
        if first_request_event(events) is not None:
            return run, events
    return None
