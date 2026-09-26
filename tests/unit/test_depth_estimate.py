from __future__ import annotations

import pytest

from cuanta.application.estimate import estimate, similar_costs
from cuanta.application.mandate_flow import MandateOptions, resolve_budget, resolve_max_turns
from cuanta.application.routing import RoutePlan
from cuanta.domain.depth import (
    Depth,
    estimate_message,
    over_cap,
    parse_depth,
    plan_cost,
    profile,
    read_budget_line,
    turn_limit,
)
from cuanta.domain.ledger import Run
from cuanta.domain.messages import english
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.routing import (
    ROLES,
    Role,
    RoutingPolicy,
    cost_range,
    default_requests,
    depth_capped,
    plan_route,
    roles_that_run,
)

SONNET = Price(input=3.0, output=15.0, cache_write=3.75, cache_read=0.3)
PRICES = PriceTable(models={"claude-sonnet-5": SONNET})
CATALOG = (
    ModelEntry("claude", "claude-sonnet-5", "Sonnet", "anthropic", tier=Tier.STANDARD),
    ModelEntry("claude", "claude-opus-5-5", "Opus", "anthropic", tier=Tier.PREMIUM),
)


def test_depth_maps_to_caps_effort_and_budgets() -> None:
    quick = profile(Depth.QUICK, "investigation")
    normal = profile(Depth.NORMAL, "investigation")
    deep = profile(Depth.DEEP, "bug")
    assert (quick.effort, normal.effort, deep.effort) == ("low", "medium", "high")
    assert quick.tier_cap is Tier.STANDARD
    assert normal.cost_cap_usd == 0.60
    assert deep.cost_cap_usd == 5.00
    assert quick.pack_tokens < normal.pack_tokens < deep.pack_tokens
    assert quick.read_budget < normal.read_budget < deep.read_budget
    assert parse_depth("DEEP") is Depth.DEEP
    assert parse_depth("whatever") is Depth.NORMAL
    assert "open at most 20 files" in read_budget_line(normal)


def test_depth_sets_turn_limits_with_headroom() -> None:
    for depth, expected in ((Depth.QUICK, 20), (Depth.NORMAL, 40), (Depth.DEEP, 80)):
        for kind in ("investigation", "bug"):
            chosen = profile(depth, kind)
            assert chosen.max_turns == expected
            assert chosen.max_turns >= chosen.read_budget * 2
            assert turn_limit(chosen) == expected
    assert turn_limit(profile(Depth.NORMAL, "investigation"), 30) == 30
    assert turn_limit(None) == 0


def test_resolve_max_turns_prefers_option_then_setting_then_depth() -> None:
    chosen = profile(Depth.NORMAL, "investigation")
    assert resolve_max_turns(MandateOptions(max_turns=5), chosen, 30) == 5
    assert resolve_max_turns(MandateOptions(), chosen, 30) == 30
    assert resolve_max_turns(MandateOptions(no_cap=True), chosen, 0) == 40
    assert resolve_max_turns(MandateOptions(), None, 0) == 40
    assert resolve_max_turns(MandateOptions(), None, 30) == 30
    assert resolve_max_turns(MandateOptions(max_turns=5), None, 30) == 5


@pytest.mark.parametrize(
    ("costs", "planned", "text"),
    [
        ([0.4, 0.9, 1.3], None, "Similar mandates cost $0.90 to $1.30 (p50–p90 of 3)."),
        ([0.545], None, "Based on 1 similar run: $0.55"),
        ([0.5, 0.7], 0.2, "Based on 2 similar runs: $0.50 to $0.70"),
        ([], 0.2, "Estimate from the plan: ~$0.20"),
        ([], None, "No prices yet to estimate this plan."),
    ],
)
def test_estimate_texts_handle_small_samples(
    costs: list[float], planned: float | None, text: str
) -> None:
    assert english(estimate_message(cost_range(costs), planned)) == text


def test_over_cap_uses_p90_or_the_plan() -> None:
    assert over_cap(cost_range([0.4, 0.9, 1.3]), None, 1.0)
    assert not over_cap(cost_range([0.4, 0.9, 1.3]), None, 0.0)
    assert over_cap(cost_range([]), 0.8, 0.6)
    assert not over_cap(cost_range([]), None, 0.6)


def test_plan_cost_grows_with_depth() -> None:
    quick = plan_cost([SONNET], profile(Depth.QUICK, "investigation"))
    deep = plan_cost([SONNET], profile(Depth.DEEP, "investigation"))
    assert quick is not None and deep is not None
    assert 0 < quick < deep
    assert plan_cost([], profile(Depth.NORMAL, "bug")) is None


def test_resolve_budget_prefers_no_cap_then_custom_then_depth() -> None:
    assert resolve_budget(MandateOptions(no_cap=True, budget_usd=2.0), "bug", 5.0) == 0.0
    assert resolve_budget(MandateOptions(budget_usd=0.4, depth="deep"), "bug", 5.0) == 0.4
    assert resolve_budget(MandateOptions(depth="normal"), "investigation", 5.0) == 0.60
    assert resolve_budget(MandateOptions(), "bug", 5.0) == 5.0


def test_only_the_roles_that_run_are_planned() -> None:
    assert roles_that_run("investigation") == (Role.ANALYST,)
    assert roles_that_run("bug") == ROLES
    assert roles_that_run("bug", simple=True) == ()
    policy = RoutingPolicy()
    requests = default_requests(policy, roles_that_run("investigation"))
    assert [request.role for request in requests] == [Role.ANALYST]
    reason = english(requests[0].reason)
    assert reason == "Analyst on standard because your policy uses standard for analysis"
    senior = english(default_requests(policy)[2].reason)
    assert senior == "Senior on premium because your policy uses premium for development"


def test_depth_caps_every_role_tier() -> None:
    capped = depth_capped(RoutingPolicy(), Tier.STANDARD)
    assert capped.tier_for(Role.SENIOR) is Tier.STANDARD
    assert capped.caps.ceiling(Role.TESTER) is Tier.STANDARD
    assert capped.tier_for(Role.DOCS) is Tier.ECONOMY


def test_similar_runs_match_type_and_depth() -> None:
    runs = (
        Run("a", "mandate", cost_usd=0.5, task_type="investigation", depth="normal"),
        Run("b", "mandate", cost_usd=0.9, task_type="investigation", depth=""),
        Run("c", "mandate", cost_usd=2.0, task_type="investigation", depth="deep"),
        Run("d", "mandate", cost_usd=1.0, task_type="bug", depth="normal"),
        Run("e", "init", cost_usd=1.0, task_type="investigation", depth="normal"),
    )
    assert similar_costs(runs, "investigation", "normal") == [0.5, 0.9]
    assert similar_costs(runs, "investigation", "deep") == [2.0]


def test_estimate_gives_a_cost_range_per_role() -> None:
    policy = RoutingPolicy()
    routes = plan_route(policy, CATALOG, default_requests(policy, (Role.ANALYST,)))
    plan = RoutePlan(policy, None, None, (), routes, "heuristic")
    result = estimate(plan, (), PRICES, "investigation", "normal", 0.60)
    assert [item.role for item in result.roles] == [Role.ANALYST]
    low, high = result.roles[0].low, result.roles[0].high
    assert low is not None and high is not None and 0 < low < high
    assert result.planned == pytest.approx(high)
    assert english(result.message).startswith("Estimate from the plan: ~$")
    assert result.depth == "normal"
