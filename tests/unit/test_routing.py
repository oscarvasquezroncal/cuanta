from __future__ import annotations

from dataclasses import replace

from cuanta.domain.instinct import heuristic_risk, heuristic_role_tier
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier, TierSource
from cuanta.domain.routing import (
    Caps,
    Preset,
    Role,
    RoleRequest,
    RoutingPolicy,
    TierOutcome,
    apply_preset,
    capped,
    cost_range,
    default_requests,
    escalate,
    gated,
    learned_tier,
    parse_policy,
    plan_route,
    raise_for_risk,
    route_role,
    search_order,
    single_model,
)


def model(engine: str, name: str, tier: Tier, default: bool = False) -> ModelEntry:
    return ModelEntry(
        engine, name, name, "p", tier=tier, tier_source=TierSource.ANCHOR, default=default
    )


CATALOG = (
    model("claude", "haiku", Tier.ECONOMY),
    model("claude", "sonnet", Tier.STANDARD),
    model("claude", "opus", Tier.PREMIUM),
    model("claude", "fable", Tier.FRONTIER),
    model("codex", "gpt-5.6-luna", Tier.ECONOMY),
    model("codex", "gpt-5.6-sol", Tier.PREMIUM),
)


def request(role: Role, tier: Tier, confidence: float | None = None) -> RoleRequest:
    return RoleRequest(role, tier, msg("route.policy", tier=tier.value), confidence)


def test_parse_policy_reads_the_flattened_routing_table() -> None:
    policy = parse_policy(
        {
            "mode": "fixed",
            "engines": ("codex", "claude", "gemini"),
            "roles.docs": "standard",
            "caps.senior": "frontier",
            "caps.frontier": True,
            "caps.mandate_usd": 3,
            "min_confidence": 0.7,
            "models.tester": "gpt-5.6-sol",
            "learn.min_samples": 3,
        }
    )
    assert policy.mode.value == "fixed"
    assert policy.engines == ("codex", "claude")
    assert policy.tier_for(Role.DOCS) is Tier.STANDARD
    assert policy.tier_for(Role.SENIOR) is Tier.PREMIUM
    assert policy.caps.ceiling(Role.SENIOR) is Tier.FRONTIER
    assert policy.caps.mandate_usd == 3.0
    assert policy.min_confidence == 0.7
    assert policy.role_models == {Role.TESTER: "gpt-5.6-sol"}
    assert policy.min_samples == 3


def test_presets_only_write_roles_and_caps() -> None:
    save = apply_preset(RoutingPolicy(min_confidence=0.9), Preset.SAVE)
    assert save.tier_for(Role.SENIOR) is Tier.STANDARD
    assert save.caps.ceiling(Role.SENIOR) is Tier.STANDARD
    assert save.min_confidence == 0.9
    assert parse_policy({"preset": "best"}).tier_for(Role.ANALYST) is Tier.PREMIUM


def test_frontier_is_never_reached_unless_allowed() -> None:
    closed = RoutingPolicy(caps=Caps(max_tier={Role.SENIOR: Tier.FRONTIER}))
    assert capped(closed, Role.SENIOR, Tier.FRONTIER) is Tier.PREMIUM
    opened = replace(closed, caps=replace(closed.caps, frontier=True))
    route = route_role(opened, CATALOG, request(Role.SENIOR, Tier.FRONTIER))
    assert route.model is not None and route.model.id == "fable"


def test_mapping_follows_engine_order_and_steps_down_then_up() -> None:
    assert search_order(Tier.STANDARD, Tier.PREMIUM) == (
        Tier.STANDARD,
        Tier.ECONOMY,
        Tier.PREMIUM,
    )
    codex_first = RoutingPolicy(engines=("codex", "claude"))
    routes = plan_route(codex_first, CATALOG, default_requests(codex_first))
    chosen = {route.role: route.model.id if route.model else "" for route in routes}
    assert chosen[Role.SENIOR] == "gpt-5.6-sol"
    assert chosen[Role.ORCHESTRATOR] == "sonnet"
    only_codex = RoutingPolicy(engines=("codex",))
    standard = route_role(only_codex, CATALOG, request(Role.ANALYST, Tier.STANDARD))
    assert standard.tier is Tier.ECONOMY
    assert "stepped down" in english(standard.reason)
    no_economy = tuple(entry for entry in CATALOG if entry.tier is not Tier.ECONOMY)
    docs = route_role(RoutingPolicy(), no_economy, request(Role.DOCS, Tier.ECONOMY))
    assert docs.tier is Tier.STANDARD
    assert "stepped up" in english(docs.reason)


def test_a_cap_is_never_exceeded() -> None:
    policy = RoutingPolicy(caps=Caps(max_tier={Role.DOCS: Tier.ECONOMY}))
    only_premium = tuple(entry for entry in CATALOG if entry.tier is Tier.PREMIUM)
    route = route_role(policy, only_premium, request(Role.DOCS, Tier.PREMIUM))
    assert route.model is None
    assert "no model available" in english(route.reason)
    capped_route = route_role(policy, CATALOG, request(Role.DOCS, Tier.PREMIUM))
    assert capped_route.tier is Tier.ECONOMY
    assert "capped at economy" in english(capped_route.reason)


def test_pinned_models_win() -> None:
    policy = RoutingPolicy(role_models={Role.TESTER: "codex:gpt-5.6-luna"})
    route = route_role(policy, CATALOG, request(Role.TESTER, Tier.PREMIUM))
    assert route.model is not None and route.model.id == "gpt-5.6-luna"
    assert "pinned by you" in english(route.reason)


def test_low_confidence_falls_back_to_the_policy_default() -> None:
    policy = RoutingPolicy(min_confidence=0.6)
    unsure = gated(policy, request(Role.SENIOR, Tier.ECONOMY, 0.4))
    assert unsure.tier is Tier.PREMIUM
    assert "40% sure" in english(unsure.reason)
    sure = request(Role.SENIOR, Tier.ECONOMY, 0.8)
    assert gated(policy, sure) == sure


def test_escalation_raises_one_level_within_the_cap() -> None:
    policy = RoutingPolicy()
    raised = escalate(policy, request(Role.SENIOR, Tier.STANDARD))
    assert raised.tier is Tier.PREMIUM
    assert "raised from standard to premium" in english(raised.reason)
    stuck = escalate(policy, request(Role.SENIOR, Tier.PREMIUM))
    assert stuck.tier is Tier.PREMIUM
    assert "already the cap" in english(stuck.reason)


def test_learning_prefers_the_cheapest_tier_that_met_the_threshold() -> None:
    policy = RoutingPolicy(success_threshold=0.8, min_samples=4)
    outcomes = (
        TierOutcome(Tier.ECONOMY, 3, 3),
        TierOutcome(Tier.STANDARD, 5, 4),
        TierOutcome(Tier.PREMIUM, 10, 10),
    )
    learned = learned_tier(policy, Role.SENIOR, outcomes)
    assert learned is not None and learned.tier is Tier.STANDARD
    assert "80% of 5" in english(learned.reason)
    assert learned_tier(policy, Role.SENIOR, (TierOutcome(Tier.ECONOMY, 2, 2),)) is None


def test_risk_raises_only_the_tester() -> None:
    policy = RoutingPolicy()
    tester = request(Role.TESTER, Tier.STANDARD)
    assert raise_for_risk(tester, 1.8, policy).tier is Tier.PREMIUM
    assert raise_for_risk(tester, 0.5, policy) == tester
    senior = request(Role.SENIOR, Tier.STANDARD)
    assert raise_for_risk(senior, 2.0, policy) == senior


def test_single_model_is_the_highest_routed_tier() -> None:
    routes = plan_route(RoutingPolicy(), CATALOG, default_requests(RoutingPolicy()))
    chosen = single_model(routes)
    assert chosen is not None and chosen.tier is Tier.PREMIUM


def test_cost_range_percentiles() -> None:
    assert cost_range([]).samples == 0
    estimate = cost_range([1.0, 2.0, 3.0, 4.0, 10.0])
    assert (estimate.p50, estimate.p90, estimate.samples) == (3.0, 10.0, 5)


def test_heuristic_tier_and_risk_answers() -> None:
    options = ("economy", "standard", "premium")
    base = {"role": "senior", "default": "premium"}
    assert heuristic_role_tier({**base, "scope": "trivial"}, options).option == "standard"
    assert heuristic_role_tier({**base, "scope": "complex"}, options).option == "premium"
    analyst = {"role": "analyst", "default": "standard", "blast_radius": 40}
    assert heuristic_role_tier(analyst, options).option == "premium"
    red_tester = {"role": "tester", "default": "premium", "scope": "trivial", "tests": "red"}
    assert heuristic_role_tier(red_tester, options).option == "premium"
    risky = {"blast_radius": 50, "tests": "red", "type": "refactor", "scope": "complex"}
    assert heuristic_risk(risky, 0, 2) == 2
    assert heuristic_risk({}, 0, 2) == 0
