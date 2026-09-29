from __future__ import annotations

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.costs import CostsQuery
from cuanta.domain.cost_trend import (
    ASCII_GLYPHS,
    CostTrend,
    cost_trend,
    spark_levels,
    sparkline,
    trend_payload,
)
from cuanta.domain.ledger import Run
from cuanta.domain.real_costs import attempts

NOW = "2026-09-29T12:00:00+00:00"


def attempt_run(
    index: int,
    cost: float | None,
    outcome: str = "",
    engine: str = "claude",
    task_type: str = "feature",
    estimated: bool = False,
) -> Run:
    return Run(
        f"R{index:02d}",
        "mandate",
        engine,
        started_at=f"2026-09-{index:02d}T10:00:00+00:00",
        ended_at=f"2026-09-{index:02d}T10:05:00+00:00",
        status="ok",
        cost_usd=0.0 if cost is None else cost,
        task_type=task_type,
        cost_source="unknown" if cost is None else "estimated" if estimated else "reported",
        outcome=outcome,
    )


def rounded(series: tuple[float | None, ...]) -> tuple[float | None, ...]:
    return tuple(None if value is None else round(value, 6) for value in series)


RUNS = (
    attempt_run(1, 0.5),
    attempt_run(2, 0.3, "accepted"),
    attempt_run(3, 0.4, "rejected"),
    attempt_run(4, 0.2, "accepted"),
    attempt_run(5, 0.9, "accepted", engine="codex", estimated=True),
    attempt_run(6, 0.1, task_type="bug"),
    attempt_run(7, None, "accepted", task_type="bug"),
)


def test_the_trend_groups_by_provider_and_type_and_counts_every_attempt() -> None:
    trend = cost_trend(attempts(RUNS, NOW))
    keys = [(row.provider, row.task_type) for row in trend.rows]
    assert keys == [("claude", "fix"), ("claude", "feature"), ("codex", "feature")]
    feature = trend.rows[1]
    assert (feature.runs, feature.accepted) == (4, 2)
    assert feature.per_accepted_usd == pytest.approx(0.7)
    assert rounded(feature.series) == (None, 0.8, 1.2, 0.7)
    assert not feature.lower_bound and not feature.estimated
    fix = trend.rows[0]
    assert fix.per_accepted_usd == pytest.approx(0.1) and fix.lower_bound
    codex = trend.rows[2]
    assert codex.estimated and codex.per_accepted_usd == pytest.approx(0.9)
    assert trend.runs == 7 and trend.limit == 20


def test_each_group_keeps_its_last_runs_only() -> None:
    trend = cost_trend(attempts(RUNS, NOW), 2)
    feature = next(row for row in trend.rows if row.task_type == "feature")
    assert feature.runs == 2 and rounded(feature.series) == (None, 0.6)
    assert trend.runs == 5
    with pytest.raises(ValueError, match="at least one run"):
        cost_trend((), 0)


def test_a_group_without_an_accepted_change_stays_na() -> None:
    trend = cost_trend(attempts((attempt_run(1, 0.5), attempt_run(2, 0.4)), NOW))
    row = trend.rows[0]
    assert row.per_accepted_usd is None and row.series == (None, None)
    assert not row.lower_bound and not row.estimated
    assert sparkline(row.series) == "··"
    assert CostTrend().empty and not trend.empty


def test_the_sparkline_scales_known_values_and_marks_unknown_ones() -> None:
    assert spark_levels((None, 1.0, 3.0, 2.0)) == (None, 0, 7, 4)
    assert spark_levels((0.5, 0.5)) == (0, 0)
    assert sparkline((None, 1.0, 3.0)) == "·▁█"
    assert sparkline((1.0, 3.0), ASCII_GLYPHS, ".") == "07"


def test_the_payload_lists_each_group_with_its_series() -> None:
    payload = trend_payload(cost_trend(attempts(RUNS[:2], NOW)))
    assert payload == {
        "limit": 20,
        "runs": 2,
        "rows": [
            {
                "provider": "claude",
                "task_type": "feature",
                "runs": 2,
                "accepted": 1,
                "cost_per_accepted_usd": pytest.approx(0.8),
                "cost_per_accepted_lower_bound": False,
                "cost_per_accepted_includes_estimated": False,
                "series": [None, pytest.approx(0.8)],
            }
        ],
    }


def test_the_costs_query_builds_the_trend_from_every_recorded_run() -> None:
    ledger = MemoryLedger()
    for run in RUNS:
        ledger.add_run(run)
    query = CostsQuery(lambda: ledger, lambda: True, lambda: NOW)
    assert query.trend(3).rows[1].runs == 3
    assert CostsQuery(MemoryLedger, lambda: False, lambda: NOW).trend() == CostTrend()
