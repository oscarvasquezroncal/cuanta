from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from cuanta.domain.costs import CostTotal, median, total_costs
from cuanta.domain.estimates import RunEstimate, estimate_bounds, estimate_error
from cuanta.domain.ledger import Run
from cuanta.domain.outcomes import eligible, is_attempt
from cuanta.domain.real_costs import (
    CROSS_MIX,
    UNTYPED,
    CostRow,
    attempts,
    cost_report,
    cost_row,
    costs_since,
    seconds_between,
    since_date,
    type_key,
)
from cuanta.domain.routing import CostRange

SINCE = "2026-08-27T00:00:00Z"


def run(
    run_id: str,
    task_type: str = "feature",
    cost: float | None = 1.0,
    outcome: str = "",
    **changes: Any,
) -> Run:
    base = Run(
        run_id,
        "mandate",
        "claude",
        started_at="2026-09-20T10:00:00Z",
        ended_at="2026-09-20T10:02:00Z",
        status="ok",
        cost_usd=cost,
        task_type=task_type,
        outcome=outcome,
        cost_source="reported",
    )
    return replace(base, **changes)


def test_estimate_bounds_mirror_the_team_step_message() -> None:
    assert estimate_bounds(CostRange(0.4, 0.9, 5, 0.2, 1.4), None) == RunEstimate(
        "history", 0.4, 0.9, 5
    )
    assert estimate_bounds(CostRange(0.5, 0.6, 2, 0.3, 0.7), 2.0) == RunEstimate(
        "history", 0.3, 0.7, 2
    )
    assert estimate_bounds(CostRange(0.5, 0.5, 1, 0.5, 0.5), None) == RunEstimate(
        "history", 0.5, 0.5, 1
    )
    assert estimate_bounds(CostRange(None, None, 0), 1.25) == RunEstimate("plan", 1.25, 1.25, 0)
    assert estimate_bounds(CostRange(None, None, 0), None) == RunEstimate()


@pytest.mark.parametrize(
    ("low", "high", "actual", "expected"),
    [
        (0.4, 0.9, 0.6, 0.0),
        (0.4, 0.9, 0.9, 0.0),
        (0.4, 0.9, 1.8, 1.0),
        (0.4, 0.9, 0.2, -0.5),
        (1.0, 1.0, 1.5, 0.5),
        (1.0, None, 0.5, -0.5),
        (None, None, 0.5, None),
        (0.4, 0.9, None, None),
        (0.0, 0.0, 0.5, None),
    ],
)
def test_estimate_error_is_zero_inside_the_range_and_signed_outside(
    low: float | None, high: float | None, actual: float | None, expected: float | None
) -> None:
    error = estimate_error(low, high, actual)
    assert error == (pytest.approx(expected) if expected is not None else None)


def test_median_and_totals_never_turn_unknown_into_zero() -> None:
    assert median([]) is None
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([4.0, 1.0, 2.0, 3.0]) == 2.5
    total = total_costs([(1.0, False), (None, False), (0.5, True)])
    assert total == CostTotal(1.5, 2, 1, 1)
    assert total.lower_bound and total.value == 1.5
    empty = total_costs([(None, False)])
    assert empty.value is None and empty.lower_bound


def test_attempts_keep_mandates_and_cross_roots_only() -> None:
    root = run("C1", "bug", 0.5, kind="cross", engine="codex", ended_at="2026-09-20T10:01:00Z")
    child = run("C2", "bug", 0.25, kind="cross", parent_id="C1", ended_at="2026-09-20T10:05:00Z")
    found = attempts(
        (
            run("M1"),
            root,
            child,
            run("L1", kind="loop"),
            run("I1", kind="init"),
            run("R1", status="running"),
        )
    )
    assert [item.run.id for item in found] == ["M1", "C1"]
    cross = found[1]
    assert cross.cost == 0.75
    assert cross.seconds == 300
    assert cross.type == "fix"
    assert cross.mix == CROSS_MIX
    lost = attempts((root, replace(child, cost_usd=None)))
    assert lost[0].cost is None
    team = attempts((root, replace(child, engine="codex")))
    assert team[0].mix == "codex"
    unnamed = attempts((replace(root, engine=""), replace(child, engine="")))
    assert unnamed[0].mix == CROSS_MIX


def test_outcome_eligibility_follows_the_attempt_rule() -> None:
    assert eligible(run("M1"))
    assert not eligible(run("M2", status="running"))
    assert is_attempt(run("C1", kind="cross"))
    assert not is_attempt(run("C2", kind="cross", parent_id="C1"))
    assert not is_attempt(run("L1", kind="loop"))


def test_cost_per_accepted_change_divides_every_attempt_by_the_accepted_ones() -> None:
    items = attempts(
        (
            run("A", cost=1.0, outcome="accepted"),
            run("B", cost=2.0, outcome="rejected"),
            run("C", cost=3.0, status="failed"),
            run("D", cost=None),
        )
    )
    row = cost_row("feature", items)
    assert row.runs == 4 and row.accepted == 1 and row.rejected == 1 and row.pending == 2
    assert row.spend.known_usd == 6.0 and row.spend.missing == 1
    assert row.per_accepted == 6.0 and row.lower_bound
    assert row.median_cost == 2.0
    nobody = cost_row("feature", attempts((run("E", outcome="rejected"),)))
    assert nobody.per_accepted is None
    unknown = cost_row("feature", attempts((run("F", cost=None, outcome="accepted"),)))
    assert unknown.per_accepted is None and unknown.spend.value is None


def test_estimate_errors_use_only_runs_with_both_values() -> None:
    items = attempts(
        (
            run("A", cost=1.2, estimate_low=0.5, estimate_high=1.0, estimate_source="history"),
            run("B", cost=0.8, estimate_low=0.5, estimate_high=1.0, estimate_source="history"),
            run("C", cost=None, estimate_low=0.5, estimate_high=1.0, estimate_source="history"),
            run("D", cost=3.0),
        )
    )
    row = cost_row("feature", items)
    assert row.errors == 2
    assert row.median_error == pytest.approx(0.1)


def test_the_report_keeps_every_type_row_and_adds_untyped_and_other_engines() -> None:
    report = cost_report(
        (
            run("A", "bug", 1.0, "accepted"),
            run("B", "", 2.0),
            run("C", "investigation", 0.5, engine="opencode", cost_source="estimated"),
        ),
        SINCE,
    )
    assert [row.key for row in report.by_type] == [
        "investigation",
        "fix",
        "feature",
        "refactor",
        "docs",
        UNTYPED,
    ]
    assert [row.key for row in report.by_mix] == ["claude", "codex", CROSS_MIX, "opencode"]
    fix = report.row("fix")
    assert fix is not None and fix.accepted == 1
    investigation = report.row("investigation")
    assert investigation is not None and investigation.spend.estimated == 1
    assert report.total.runs == 3 and not report.empty
    assert cost_report((), SINCE).empty


def test_windows_and_dates() -> None:
    assert costs_since("2026-09-26T12:30:00Z") == "2026-08-27T00:00:00Z"
    assert costs_since("2026-09-26T21:30:00-05:00") == "2026-08-28T00:00:00Z"
    assert costs_since("not a time") == ""
    assert since_date("2026-09-01") == "2026-09-01T00:00:00Z"
    assert since_date("2026-13-01") is None
    assert seconds_between("2026-09-20T10:00:00Z", "2026-09-20T10:00:30Z") == 30
    assert seconds_between("2026-09-20T10:00:30Z", "2026-09-20T10:00:00Z") is None
    assert seconds_between("", "2026-09-20T10:00:00Z") is None
    assert type_key("bug") == "fix" and type_key("") == UNTYPED


def test_pipelines_still_running_are_not_attempts_yet() -> None:
    root = run("C1", kind="cross")
    busy = run("C2", kind="cross", parent_id="C1", status="running", cost=None)
    assert attempts((root, busy)) == ()
    assert [item.run.id for item in attempts((root, replace(busy, status="ok")))] == ["C1"]


def test_runs_stuck_running_for_a_day_count_with_an_unknown_cost() -> None:
    stuck = run("S", status="running", cost=None, started_at="2026-09-20T10:00:00Z")
    assert attempts((stuck,), "2026-09-20T12:00:00Z") == ()
    (item,) = attempts((stuck,), "2026-09-22T10:00:00Z")
    assert item.cost is None
    assert eligible(stuck, "2026-09-22T10:00:00Z") and not eligible(stuck)


def test_legacy_zero_costs_without_a_source_are_unknown() -> None:
    legacy = run("L", cost=0.0, cost_source="unknown")
    free = run("F", cost=0.0, cost_source="reported")
    report = cost_report((legacy, free), SINCE)
    feature = report.row("feature")
    assert feature is not None
    assert (feature.spend.known, feature.spend.missing) == (1, 1)


def test_in_range_needs_most_runs_inside_the_range() -> None:
    def row_for(*costs: float) -> CostRow:
        runs = tuple(
            run(f"R{index}", cost=cost, estimate_low=1.0, estimate_high=2.0)
            for index, cost in enumerate(costs)
        )
        return cost_row("feature", attempts(runs))

    split = row_for(0.5, 3.0)
    assert split.median_error == 0 and not split.error_in_range
    mostly = row_for(1.5, 1.2, 3.0)
    assert mostly.error_in_range
    assert row_for(1.5, 1.5).estimated is False
