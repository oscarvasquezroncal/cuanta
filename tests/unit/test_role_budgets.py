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
    REPAIR_FRACTION,
    ROLE_FLOOR,
    USD_MARGIN_FALLBACK,
    Overshoot,
    OvershootMargins,
    allocate_budget,
    floor_fraction,
    floors_usd,
    history_weights,
    native_cap,
    overshoot_margins,
    repair_budget,
    role_split,
    usd_margin,
)
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy

X3_SONNET_OVERSHOOTS = (
    Overshoot("claude-sonnet-5", 0.2520, 0.3007),
    Overshoot("claude-sonnet-5", 0.2760, 0.3508),
    Overshoot("claude-sonnet-5", 0.2100, 0.2839),
    Overshoot("claude-sonnet-5", 0.2730, 0.3003),
    Overshoot("claude-sonnet-5", 0.2100, 0.2547),
    Overshoot("claude-sonnet-5", 0.2890, 0.3375),
    Overshoot("claude-sonnet-5", 0.2730, 0.3535),
)
SONNET = {"claude-sonnet-5": "sonnet"}


def test_the_usd_margin_is_the_p90_overshoot_with_a_fallback_below_three_samples() -> None:
    assert usd_margin(()) == USD_MARGIN_FALLBACK == 0.08
    assert usd_margin((0.01, 0.02)) == USD_MARGIN_FALLBACK
    assert usd_margin((0.01, 0.02, 0.03)) == pytest.approx(0.03)
    assert usd_margin((0.0, 0.0, 0.0, float("nan"), -1.0)) == 0.0
    assert Overshoot("m", 0.3, 0.2).usd == 0.0
    assert Overshoot("m", 0.3, 0.35).usd == pytest.approx(0.05)


def test_margins_are_kept_per_model_and_match_aliases_and_dated_names() -> None:
    margins = overshoot_margins(
        (*X3_SONNET_OVERSHOOTS, Overshoot("claude-opus-5-5", 0.5, 0.6)), SONNET
    )
    assert margins.count("sonnet") == 7
    assert margins.usd("sonnet") == pytest.approx(0.0748, abs=1e-6)
    assert margins.usd("claude-sonnet-5-20260901") == margins.usd("sonnet")
    assert margins.usd("claude-sonnet-5[1m]") == margins.usd("sonnet")
    assert margins.count("claude-opus-5-5") == 1
    assert margins.usd("claude-opus-5-5") == USD_MARGIN_FALLBACK
    assert OvershootMargins().usd("haiku") == USD_MARGIN_FALLBACK
    assert overshoot_margins((Overshoot("m", 0.0, 1.0),)).count("m") == 0


def test_the_native_cap_is_the_share_minus_the_margin_never_below_half_the_share() -> None:
    assert native_cap(1.0, 0.08) == pytest.approx(0.92)
    assert native_cap(0.1, 0.08) == pytest.approx(0.05)
    assert native_cap(1.0, -1.0) == 1.0
    assert native_cap(0.0, 0.08) == 0.0
    assert floors_usd((Role.SENIOR, Role.TESTER), 2.0) == pytest.approx(0.56)
    assert floors_usd((Role.ORCHESTRATOR,), 1.0) == pytest.approx(0.05)


def test_the_x3_gsap_senior_is_no_longer_cut_by_a_quarter_of_its_share() -> None:
    share = 0.3853
    proportional = 0.2890
    measured_overshoot = 0.0485
    margin = overshoot_margins(X3_SONNET_OVERSHOOTS, SONNET).usd("sonnet")
    cap = native_cap(share, margin)
    assert cap == pytest.approx(share - 0.0748, abs=1e-6)
    assert cap > proportional
    assert cap + measured_overshoot <= share
    assert share - (cap + measured_overshoot) < share - (proportional + measured_overshoot)
    for large in (0.5, 1.0, 2.0, 4.0):
        cut = large - native_cap(large, margin)
        assert cut == pytest.approx(margin)
        assert cut / large < 0.25
    assert native_cap(share, USD_MARGIN_FALLBACK) > proportional


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
    stopped = Run(
        "S",
        "cross",
        engine="claude",
        model="m-analyst",
        scope="analyst",
        task_type="feature",
        depth="normal",
        status="failed",
        end_reason="error_max_budget_usd",
        cost_usd=0.3,
    )
    assert role_history(plan(), [stopped], "feature", "normal")[Role.ANALYST] == [0.3]
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
        margins = container.budget_margin(ledger)
        assert margins.count("") == 3
        assert margins.usd("") == pytest.approx(0.298 - 0.2786)
        assert margins.usd("sonnet") == USD_MARGIN_FALLBACK
    finally:
        container.close()


def test_fixes_with_docs_reserve_a_repair_share_from_the_whole_team() -> None:
    costs: dict[Role, float | None] = {
        Role.ANALYST: 0.3,
        Role.SENIOR: 0.5,
        Role.TESTER: 0.2,
        Role.DOCS: 0.1,
    }
    budget = repair_budget(costs, 2.0, docs=True)
    assert budget.repair_usd == pytest.approx(2.0 * REPAIR_FRACTION)
    assert budget.from_docs is False
    assert set(budget.shares) == set(costs)
    assert budget.shares == pytest.approx(allocate_budget(costs, 2.0 - budget.repair_usd))
    assert sum(budget.shares.values()) + budget.repair_usd == pytest.approx(2.0)


def test_fixes_with_docs_keep_every_share_at_its_floor_of_the_whole_cap() -> None:
    skewed: dict[Role, float | None] = {
        Role.ANALYST: 0.1,
        Role.SENIOR: 3.0,
        Role.TESTER: 0.1,
        Role.DOCS: 0.02,
    }
    budget = repair_budget(skewed, 2.0, docs=True)
    assert budget.repair_usd == pytest.approx(2.0 * REPAIR_FRACTION)
    for role, share in budget.shares.items():
        assert share >= 2.0 * floor_fraction(role) - 1e-9
    assert sum(budget.shares.values()) + budget.repair_usd == pytest.approx(2.0)
    pinned = repair_budget(skewed, 2.0, docs=True, floors={Role.ANALYST: 0.3})
    assert pinned.shares[Role.ANALYST] >= 2.0 * 0.3 - 1e-9
    assert pinned.shares[Role.TESTER] >= 2.0 * ROLE_FLOOR - 1e-9
    assert sum(pinned.shares.values()) + pinned.repair_usd == pytest.approx(2.0)


def test_fixes_without_docs_fund_the_repair_share_from_the_docs_share() -> None:
    costs: dict[Role, float | None] = {Role.ANALYST: 0.3, Role.SENIOR: 0.5, Role.TESTER: 0.2}
    budget = repair_budget(costs, 2.0, docs=False)
    with_docs = allocate_budget({**costs, Role.DOCS: None}, 2.0)
    assert budget.from_docs is True
    assert Role.DOCS not in budget.shares
    assert budget.repair_usd == pytest.approx(with_docs[Role.DOCS])
    assert budget.repair_usd >= 2.0 * 0.04
    for role in costs:
        assert budget.shares[role] == pytest.approx(with_docs[role])
    assert sum(budget.shares.values()) + budget.repair_usd == pytest.approx(2.0)
    weighted = repair_budget({**costs, Role.DOCS: 0.2}, 2.0, docs=False)
    assert Role.DOCS not in weighted.shares
    assert weighted.repair_usd == pytest.approx(
        allocate_budget({**costs, Role.DOCS: 0.2}, 2.0)[Role.DOCS]
    )


def test_repair_budget_without_a_cap_or_roles_is_empty() -> None:
    assert repair_budget({Role.SENIOR: 0.5}, 0.0, docs=False).shares == {}
    assert repair_budget({}, 1.0, docs=True).repair_usd == 0.0
    with pytest.raises(ValueError, match="finite"):
        repair_budget({Role.SENIOR: 0.5}, float("inf"), docs=True)


def test_only_fixes_split_a_repair_share_off_the_role_budgets() -> None:
    costs: dict[Role, float | None] = {Role.ANALYST: 0.3, Role.SENIOR: 0.5, Role.TESTER: 0.2}
    feature = role_split(costs, 2.0, fix=False)
    assert feature.shares == allocate_budget(costs, 2.0)
    assert (feature.repair_usd, feature.from_docs) == (0.0, False)
    assert role_split(costs, 2.0, fix=True) == repair_budget(costs, 2.0, docs=False)
    with_docs = {**costs, Role.DOCS: 0.1}
    assert role_split(with_docs, 2.0, fix=True) == repair_budget(with_docs, 2.0, docs=True)


def test_a_fix_estimate_reserves_the_repair_share_the_runner_uses() -> None:
    fix = estimate(plan(), (), load_prices(), "bug", "normal", 1.0)
    feature = estimate(plan(), (), load_prices(), "feature", "normal", 1.0)
    assert (feature.repair_usd, feature.repair_from_docs) == (0.0, False)
    assert sum(cost.share for cost in feature.roles) == pytest.approx(1.0)
    assert fix.repair_from_docs is True and fix.repair_usd > 0
    assert sum(cost.share for cost in fix.roles) + fix.repair_usd == pytest.approx(1.0)


def test_a_fix_that_cannot_run_a_repair_turn_splits_like_a_feature() -> None:
    unchecked = estimate(plan(), (), load_prices(), "bug", "normal", 1.0, repair=False)
    feature = estimate(plan(), (), load_prices(), "feature", "normal", 1.0)
    assert (unchecked.repair_usd, unchecked.repair_from_docs) == (0.0, False)
    assert [cost.share for cost in unchecked.roles] == pytest.approx(
        [cost.share for cost in feature.roles]
    )
