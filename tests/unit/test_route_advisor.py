from __future__ import annotations

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.instinct import DecisionMaker
from cuanta.application.routing import (
    RouteAdvisor,
    RouteInputs,
    role_stats,
    with_overrides,
)
from cuanta.domain.instinct import Choice
from cuanta.domain.ledger import RoutingDecision
from cuanta.domain.messages import english
from cuanta.domain.models import ModelEntry, Tier, TierSource
from cuanta.domain.routing import Role, RouteMode, RoutingPolicy

CATALOG = tuple(
    ModelEntry("claude", name, name, "anthropic", tier=tier, tier_source=TierSource.ANCHOR)
    for name, tier in (
        ("haiku", Tier.ECONOMY),
        ("sonnet", Tier.STANDARD),
        ("opus", Tier.PREMIUM),
    )
)


def advisor(ledger: MemoryLedger) -> RouteAdvisor:
    decisions = DecisionMaker(HeuristicInstinct(), ledger, lambda: "2026-09-23T00:00:00Z")
    return RouteAdvisor(decisions, lambda: CATALOG, ledger, lambda: "2026-09-23T00:00:00Z")


def test_auto_plan_asks_scope_tiers_and_risk_and_explains_them() -> None:
    ledger = MemoryLedger()
    plan = advisor(ledger).plan(
        RoutingPolicy(), RouteInputs("bug", "fix typo in README", "README.md")
    )
    assert plan.scope is not None and plan.scope.option == "trivial"
    assert plan.risk is not None
    senior = plan.route(Role.SENIOR)
    assert senior is not None and senior.model is not None
    assert senior.model.id == "opus"
    assert "below 60%): policy default premium" in english(senior.reason)
    primitives = [decision.primitive for decision in ledger.decisions()]
    assert primitives.count("choose") == 6
    assert primitives.count("score") == 1


def test_low_clarity_caps_every_role_at_standard() -> None:
    plan = advisor(MemoryLedger()).plan(
        with_overrides(RoutingPolicy(), mode="fixed"),
        RouteInputs("investigation", "look at the landing page", clarity=0.2),
    )
    tiers = {route.role: route.requested for route in plan.routes}
    assert tiers[Role.SENIOR] is Tier.STANDARD
    assert tiers[Role.DOCS] is Tier.ECONOMY
    senior = plan.route(Role.SENIOR)
    assert senior is not None
    assert "clarity 0.2 of 2" in english(senior.reason)


def test_vague_one_line_investigation_is_never_complex() -> None:
    ledger = MemoryLedger()
    plan = advisor(ledger).plan(
        RoutingPolicy(), RouteInputs("investigation", "revisar la landing", "", clarity=0.2)
    )
    assert plan.scope is not None
    assert plan.scope.option in {"trivial", "normal"}
    docs = plan.route(Role.DOCS)
    assert docs is not None and docs.requested is Tier.ECONOMY


def test_fixed_mode_uses_the_policy_and_never_asks() -> None:
    ledger = MemoryLedger()
    policy = with_overrides(RoutingPolicy(), mode="fixed")
    plan = advisor(ledger).plan(policy, RouteInputs("feature", "add export"))
    assert plan.scope is None
    assert ledger.decisions() == ()
    tiers = {route.role: route.tier for route in plan.routes}
    assert tiers[Role.SENIOR] is Tier.PREMIUM
    assert tiers[Role.DOCS] is Tier.ECONOMY


def test_off_mode_leaves_engine_defaults() -> None:
    plan = advisor(MemoryLedger()).plan(
        with_overrides(RoutingPolicy(), mode="off"), RouteInputs("bug", "x")
    )
    assert all(route.model is None for route in plan.routes)
    assert plan.policy.mode is RouteMode.OFF


def test_persistent_failure_escalates_the_senior() -> None:
    policy = with_overrides(RoutingPolicy(), preset="save", mode="fixed")
    plan = advisor(MemoryLedger()).plan(policy, RouteInputs("bug", "x", persistent_failure=True))
    senior = plan.route(Role.SENIOR)
    assert senior is not None and senior.tier is Tier.STANDARD
    assert "already the cap" in english(senior.reason)
    opened = with_overrides(RoutingPolicy(), mode="fixed", role_models=None)
    lowered = RoutingPolicy(roles={**opened.roles, Role.SENIOR: Tier.STANDARD})
    fixed = with_overrides(lowered, mode="fixed")
    raised = advisor(MemoryLedger()).plan(fixed, RouteInputs("bug", "x", persistent_failure=True))
    route = raised.route(Role.SENIOR)
    assert route is not None and route.tier is Tier.PREMIUM


def test_record_close_and_learning_loop() -> None:
    ledger = MemoryLedger()
    routes = advisor(ledger)
    policy = RoutingPolicy(min_samples=2, success_threshold=0.5)
    for index in range(3):
        plan = routes.plan(with_overrides(policy, mode="fixed"), RouteInputs("bug", "x"))
        routes.record(f"R{index}", "bug", plan)
        routes.close(f"R{index}", "green", 0.5, 0)
    decisions = ledger.routing_decisions()
    assert len(decisions) == 15
    assert all(decision.outcome == "green" for decision in decisions)
    stats = {(row.role, row.tier): row for row in role_stats(decisions)}
    assert stats[("senior", "premium")].samples == 3
    assert stats[("senior", "premium")].success == 1.0
    assert stats[("senior", "premium")].average_cost == 0.5
    learned = routes.plan(policy, RouteInputs("bug", "x"))
    senior = learned.route(Role.SENIOR)
    assert senior is not None
    assert "succeeded 100% of 3" in english(senior.reason)


def test_role_stats_ignore_open_decisions() -> None:
    open_decision = RoutingDecision("R", "bug", "senior", "premium", "claude", "opus", "why")
    assert role_stats((open_decision,)) == ()


def test_investigations_default_to_a_standard_analyst_and_economy_docs() -> None:
    plan = advisor(MemoryLedger()).plan(
        with_overrides(RoutingPolicy(), mode="fixed", preset="best"),
        RouteInputs("investigation", "why is checkout slow on the first request after deploy"),
    )
    tiers = {route.role: route.requested for route in plan.routes}
    assert tiers[Role.ANALYST] is Tier.STANDARD
    assert tiers[Role.DOCS] is Tier.ECONOMY


def test_an_investigation_decides_and_records_only_the_analyst() -> None:
    ledger = MemoryLedger()
    routes_advisor = advisor(ledger)
    plan = routes_advisor.plan(
        RoutingPolicy(),
        RouteInputs("investigation", "how does the cart work", roles=(Role.ANALYST,)),
    )
    assert [route.role for route in plan.routes] == [Role.ANALYST]
    tier_questions = [
        decision for decision in ledger.decisions() if "model tier" in decision.question
    ]
    assert len(tier_questions) == 1
    assert "analyst" in tier_questions[0].question
    routes_advisor.record("r1", "investigation", plan)
    assert [item.role for item in ledger.routing_decisions()] == ["analyst"]


def test_known_scope_and_risk_are_not_asked_again() -> None:
    ledger = MemoryLedger()
    plan = advisor(ledger).plan(
        RoutingPolicy(),
        RouteInputs(
            "investigation",
            "how does the cart work",
            roles=(Role.ANALYST,),
            scope=Choice("normal", 0.8),
            risk=0.3,
        ),
    )
    assert plan.scope == Choice("normal", 0.8)
    assert plan.risk == 0.3
    questions = [decision.question for decision in ledger.decisions()]
    assert not any("How large is this mandate" in question for question in questions)
    assert not any("How risky" in question for question in questions)
