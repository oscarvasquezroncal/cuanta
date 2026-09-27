from __future__ import annotations

import time
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.application.costs import CostsQuery
from cuanta.domain.ledger import Run

RUNS = 5000
BUDGET_S = 0.5
TYPES = ("bug", "feature", "investigation", "refactor")
OUTCOMES = ("", "accepted", "rejected")


def _seed(path: Path) -> None:
    ledger = SqliteLedger(path)
    try:
        for index in range(RUNS):
            day = 1 + index % 25
            ledger.add_run(
                Run(
                    f"R{index:05d}",
                    "cross" if index % 10 == 0 else "mandate",
                    "claude",
                    started_at=f"2026-09-{day:02d}T10:00:00Z",
                    ended_at=f"2026-09-{day:02d}T10:03:00Z",
                    status="ok",
                    cost_usd=0.1 * (index % 7) if index % 13 else None,
                    task_type=TYPES[index % len(TYPES)],
                    outcome=OUTCOMES[index % len(OUTCOMES)],
                    estimate_low=0.2,
                    estimate_high=0.5,
                    estimate_source="history",
                )
            )
    finally:
        ledger.close()


@pytest.mark.perf
def test_real_costs_for_a_busy_month_stay_fast(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    _seed(path)
    query = CostsQuery(lambda: SqliteLedger(path), lambda: True, lambda: "2026-09-26T12:00:00Z")
    query.report()
    started = time.perf_counter()
    report = query.report()
    elapsed = time.perf_counter() - started
    assert report.total.runs == RUNS
    assert elapsed < BUDGET_S
