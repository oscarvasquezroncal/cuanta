from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.system.prices import load_prices
from cuanta.application.estimate import estimate, role_history
from cuanta.application.routing import RoutePlan
from cuanta.domain.ledger import Run
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.role_budgets import (
    DEFAULT_MARGIN,
    MAX_MARGIN,
    MIN_MARGIN,
    Overrun,
    floors_usd,
    history_weights,
    learned_margin,
    soft_cap,
)
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy


def test_the_margin_starts_at_ten_percent_and_learns_from_measured_overruns() -> None:
    assert learned_margin(()) == DEFAULT_MARGIN
    assert learned_margin((Overrun(0.28, 0.298),)) == DEFAULT_MARGIN
    measured = (
        Overrun(0.4205847, 0.4231824),
        Overrun(0.2785714, 0.298069),
        Overrun(0.05, 0.05157085),
        Overrun(0.10, 0.10456355),
    )
    assert learned_margin(measured) == pytest.approx((0.298069 / 0.2785714 - 1) * 1.25)
    assert learned_margin(tuple(Overrun(1.0, 1.0) for _ in range(5))) == MIN_MARGIN
    assert learned_margin(tuple(Overrun(1.0, 3.0) for _ in range(5))) == MAX_MARGIN
    assert Overrun(0.0, 1.0).ratio == 0.0


def test_soft_caps_and_floors() -> None:
    assert soft_cap(1.0, 0.1) == pytest.approx(0.9)
    assert soft_cap(1.0, 0.9) == pytest.approx(1.0 - MAX_MARGIN)
    assert soft_cap(1.0, -1.0) == 1.0
    assert floors_usd((Role.SENIOR, Role.TESTER), 2.0) == pytest.approx(0.56)
    assert floors_usd((Role.ORCHESTRATOR,), 1.0) == pytest.approx(0.05)


def test_history_weights_need_enough_samples_for_every_role() -> None:
    samples = {Role.ANALYST: [0.1, 0.2, 0.3], Role.SENIOR: [0.5, 0.7, 0.6, float("nan")]}
    weights = history_weights(samples, (Role.ANALYST, Role.SENIOR))
    assert weights is not None
    assert weights[Role.ANALYST] == pytest.approx(0.2)
    assert weights[Role.SENIOR] == pytest.approx(0.6)
    assert history_weights(samples, (Role.ANALYST, Role.TESTER)) is None
    assert history_weights({}, ()) is None


def plan() -> RoutePlan:
    engines = {Role.ANALYST: "claude", Role.SENIOR: "codex"}
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD if role in engines else None,
            ModelEntry(engines[role], f"m-{role.value}", "m", "p") if role in engines else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    return RoutePlan(RoutingPolicy(), None, None, (), routes, "heuristic")


def cross(role: Role, engine: str, cost: float, status: str = "ok", depth: str = "normal") -> Run:
    return Run(
        f"R{role.value}{cost}{status}{depth}",
        "cross",
        engine=engine,
        model=f"m-{role.value}",
        scope=role.value,
        task_type="feature",
        depth=depth,
        status=status,
        cost_usd=cost,
    )


def test_shares_follow_role_history_once_every_role_has_samples() -> None:
    runs = [
        *(cross(Role.ANALYST, "claude", cost) for cost in (0.1, 0.1, 0.1)),
        *(cross(Role.SENIOR, "codex", cost) for cost in (0.3, 0.3, 0.3)),
        cross(Role.SENIOR, "claude", 9.0),
        cross(Role.SENIOR, "codex", 9.0, status="failed"),
        cross(Role.SENIOR, "codex", 9.0, depth="deep"),
    ]
    history = role_history(plan(), runs, "feature", "normal")
    unset = role_history(plan(), [cross(Role.ANALYST, "claude", 0.1, depth="")], "feature", "")
    assert unset[Role.ANALYST] == [0.1]
    assert history[Role.ANALYST] == [0.1, 0.1, 0.1] and history[Role.SENIOR] == [0.3, 0.3, 0.3]
    shares = {
        cost.role: cost.share
        for cost in estimate(plan(), runs, load_prices(), "feature", "normal", 1.0).roles
    }
    assert shares[Role.ANALYST] == pytest.approx(0.12 + 0.68 * 0.25)
    assert shares[Role.SENIOR] == pytest.approx(0.20 + 0.68 * 0.75)
    thin = {
        cost.role: cost.share
        for cost in estimate(plan(), runs[:4], load_prices(), "feature", "normal", 1.0).roles
    }
    assert thin[Role.ANALYST] != pytest.approx(shares[Role.ANALYST])


def test_the_margin_learns_from_native_role_caps_not_pipeline_caps(tmp_path: Path) -> None:
    from cuanta.application.run_reports import RunReports
    from cuanta.bootstrap import Container
    from cuanta.domain.config import Config

    container = Container(tmp_path, Config())
    try:
        ledger = container.shared_ledger()
        stopped = "error_max_budget_usd"
        rows = (
            Run("ROOT", "cross", engine="claude", cap_usd=1.0, cost_usd=0.298, end_reason=stopped),
            Run("OLD", "cross", engine="claude", cap_usd=1.0, cost_usd=0.5, end_reason=stopped),
            Run(
                "CHILD",
                "cross",
                engine="claude",
                cap_usd=0.42,
                cost_usd=0.423,
                end_reason=stopped,
                parent_id="ROOT",
            ),
            Run(
                "SINGLE",
                "mandate",
                engine="claude",
                cap_usd=0.05,
                cost_usd=0.0516,
                end_reason=stopped,
            ),
        )
        for run in rows:
            ledger.add_run(run)
        RunReports(container.state_workspace()).save_meta("ROOT", {"native_cap_usd": 0.2786})
        expected = (0.298 / 0.2786 - 1) * 1.25
        assert container.budget_margin(ledger) == pytest.approx(expected)
    finally:
        container.close()
