from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal

from cuanta.domain.ledger import Run

RunOutcome = Literal["", "accepted", "rejected"]
PENDING: RunOutcome = ""
ACCEPTED: RunOutcome = "accepted"
REJECTED: RunOutcome = "rejected"
MANDATE_KIND = "mandate"
CROSS_KIND = "cross"
RUNNING = "running"
STALE_RUNNING = timedelta(hours=24)


def is_attempt(run: Run) -> bool:
    return run.kind == MANDATE_KIND or (run.kind == CROSS_KIND and not run.parent_id)


def _moment(text: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def stale(run: Run, now_iso: str) -> bool:
    if run.status != RUNNING or not now_iso:
        return False
    started, now = _moment(run.started_at), _moment(now_iso)
    return started is not None and now is not None and now - started > STALE_RUNNING


def finished(run: Run, now_iso: str = "") -> bool:
    return run.status != RUNNING or stale(run, now_iso)


def pipeline_running(root: Run, roles: Iterable[Run], now_iso: str = "") -> bool:
    return any(not finished(role, now_iso) for role in (root, *roles))


def eligible(run: Run, now_iso: str = "") -> bool:
    return is_attempt(run) and finished(run, now_iso)
