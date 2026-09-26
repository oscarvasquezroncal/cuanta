from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from cuanta.application.instinct import DecisionMaker
from cuanta.domain.costs import sum_costs
from cuanta.domain.instinct import SCOPES, Choice
from cuanta.domain.ledger import RoutingDecision
from cuanta.domain.messages import english, msg
from cuanta.domain.models import TIER_ORDER, ModelEntry, Tier, parse_tier, tier_rank
from cuanta.domain.routing import (
    ROLES,
    Role,
    RoleRequest,
    RoleRoute,
    RouteMode,
    RoutingPolicy,
    TierOutcome,
    apply_preset,
    clarity_capped,
    default_requests,
    escalate,
    gated,
    investigation_policy,
    learned_tier,
    plan_route,
    policy_reason,
    raise_for_risk,
)
from cuanta.ports.ledger import Ledger

SUMMARY_LIMIT = 200
RISK_LOW = 0.0
RISK_HIGH = 2.0
GREEN = "green"
RED = "red"


@dataclass(frozen=True, slots=True)
class RouteInputs:
    task_type: str
    what: str
    where: str = ""
    tests: str = "unknown"
    blast_radius: int = 0
    persistent_failure: bool = False
    clarity: float | None = None
    roles: tuple[Role, ...] = ROLES
    scope: Choice | None = None
    risk: float | None = None


@dataclass(frozen=True, slots=True)
class RoutePlan:
    policy: RoutingPolicy
    scope: Choice | None
    risk: float | None
    requests: tuple[RoleRequest, ...]
    routes: tuple[RoleRoute, ...]
    backend: str

    def route(self, role: Role) -> RoleRoute | None:
        return next((route for route in self.routes if route.role is role), None)


def tiers_allowed(policy: RoutingPolicy, role: Role) -> tuple[str, ...]:
    ceiling = tier_rank(policy.caps.ceiling(role))
    return tuple(tier.value for tier in TIER_ORDER if tier_rank(tier) <= ceiling)


def with_overrides(
    policy: RoutingPolicy,
    mode: str = "",
    preset: str = "",
    role_models: Mapping[str, str] | None = None,
) -> RoutingPolicy:
    from cuanta.domain.routing import Preset

    changed = policy
    if preset:
        changed = apply_preset(changed, Preset(preset))
    if mode:
        changed = replace(changed, mode=RouteMode(mode))
    if role_models:
        pinned = dict(changed.role_models)
        pinned.update({Role(name): model for name, model in role_models.items()})
        changed = replace(changed, role_models=pinned)
    return changed


class RouteAdvisor:
    def __init__(
        self,
        decisions: DecisionMaker,
        catalog: Callable[[], Sequence[ModelEntry]],
        ledger: Ledger,
        clock_iso: Callable[[], str],
    ) -> None:
        self._decisions = decisions
        self._catalog = catalog
        self._ledger = ledger
        self._clock_iso = clock_iso

    def _state(self, inputs: RouteInputs, scope: str) -> dict[str, object]:
        return {
            "type": inputs.task_type,
            "summary": inputs.what[:SUMMARY_LIMIT],
            "blast_radius": inputs.blast_radius,
            "tests": inputs.tests,
            "scope": scope,
            "clarity": inputs.clarity,
        }

    def outcomes(self, task_type: str, role: Role) -> tuple[TierOutcome, ...]:
        counts: dict[Tier, list[int]] = {}
        for decision in self._ledger.routing_decisions():
            if decision.task_type != task_type or decision.role != role.value:
                continue
            tier = parse_tier(decision.tier)
            if tier is None or decision.outcome not in {GREEN, RED}:
                continue
            bucket = counts.setdefault(tier, [0, 0])
            bucket[0] += 1
            bucket[1] += 1 if decision.outcome == GREEN else 0
        return tuple(TierOutcome(tier, total, ok) for tier, (total, ok) in counts.items())

    def _similar(self, task_type: str, role: Role) -> str:
        parts = [
            f"{outcome.tier.value}: {outcome.successes}/{outcome.samples} green"
            for outcome in sorted(self.outcomes(task_type, role), key=lambda item: item.tier)
        ]
        return "; ".join(parts) or "no history"

    def _ask_tier(
        self, policy: RoutingPolicy, role: Role, inputs: RouteInputs, scope: str
    ) -> RoleRequest:
        default = policy.tier_for(role)
        if role in policy.fixed_roles:
            return RoleRequest(role, default, msg("route.fixed_role", tier=default.value))
        learned = learned_tier(policy, role, self.outcomes(inputs.task_type, role))
        if learned is not None:
            return learned
        state = self._state(inputs, scope)
        state.update(
            {
                "kind": "route_tier",
                "role": role.value,
                "default": default.value,
                "similar": self._similar(inputs.task_type, role),
            }
        )
        options = tiers_allowed(policy, role)
        question = english(msg("question.route_tier", role=role.value))
        choice, _ = self._decisions.choose(question, options, state)
        tier = parse_tier(choice.option) or default
        if tier is default:
            reason = policy_reason(role, tier)
        elif inputs.blast_radius > 0:
            reason = msg(
                "route.instinct_reach",
                tier=msg(f"tier.{tier.value}"),
                scope=msg(f"scope.{scope}"),
                radius=inputs.blast_radius,
            )
        else:
            reason = msg(
                "route.instinct_scope", tier=msg(f"tier.{tier.value}"), scope=msg(f"scope.{scope}")
            )
        return gated(policy, RoleRequest(role, tier, reason, choice.probability))

    def _scope(self, inputs: RouteInputs) -> Choice:
        state = self._state(inputs, "")
        state.update({"kind": "scope", "what": inputs.what, "where": inputs.where})
        scope, _ = self._decisions.choose(english(msg("question.mandate_scope")), SCOPES, state)
        return scope

    def _risk(self, inputs: RouteInputs, scope: str) -> float:
        state = self._state(inputs, scope)
        state["kind"] = "risk"
        score, _ = self._decisions.score(
            english(msg("question.route_risk")), RISK_LOW, RISK_HIGH, state
        )
        return score.value

    def plan(self, policy: RoutingPolicy, inputs: RouteInputs) -> RoutePlan:
        entries = tuple(self._catalog())
        backend = self._decisions.backend.name
        if policy.mode is RouteMode.OFF:
            routes = tuple(
                RoleRoute(role, policy.tier_for(role), None, None, msg("route.off"))
                for role in inputs.roles
            )
            return RoutePlan(policy, None, None, (), routes, backend)
        scope: Choice | None = None
        risk: float | None = None
        policy = investigation_policy(policy, inputs.task_type)
        requests = list(default_requests(policy, inputs.roles))
        if policy.mode is RouteMode.AUTO:
            scope = inputs.scope or self._scope(inputs)
            requests = [
                self._ask_tier(policy, request.role, inputs, scope.option) for request in requests
            ]
            risk = inputs.risk if inputs.risk is not None else self._risk(inputs, scope.option)
            requests = [raise_for_risk(request, risk, policy) for request in requests]
        if inputs.persistent_failure:
            requests = [
                escalate(policy, request) if request.role is Role.SENIOR else request
                for request in requests
            ]
        requests = [clarity_capped(request, inputs.clarity) for request in requests]
        routes = plan_route(policy, entries, requests)
        return RoutePlan(policy, scope, risk, tuple(requests), routes, backend)

    def record(self, run_id: str, task_type: str, plan: RoutePlan) -> None:
        for route in plan.routes:
            self._ledger.add_routing_decision(
                RoutingDecision(
                    run_id=run_id,
                    task_type=task_type,
                    role=route.role.value,
                    tier=(route.tier or route.requested).value,
                    engine=route.engine,
                    model=route.model.resolved or route.model.id if route.model else "",
                    reason=english(route.reason),
                    confidence=route.confidence,
                    backend=plan.backend,
                    created_at=self._clock_iso(),
                )
            )

    def close(self, run_id: str, tests: str, cost_usd: float | None, retries: int) -> None:
        self._ledger.set_routing_outcome(run_id, tests, cost_usd, retries)


@dataclass(frozen=True, slots=True)
class RoleStats:
    task_type: str
    role: str
    tier: str
    samples: int
    green: int
    cost_usd: float | None
    priced: int

    @property
    def success(self) -> float:
        return self.green / self.samples if self.samples else 0.0

    @property
    def average_cost(self) -> float | None:
        return self.cost_usd / self.samples if self.samples and self.cost_usd is not None else None


def role_stats(decisions: Sequence[RoutingDecision]) -> tuple[RoleStats, ...]:
    grouped: dict[tuple[str, str, str], list[RoutingDecision]] = {}
    for decision in decisions:
        if decision.outcome in {GREEN, RED}:
            key = (decision.task_type, decision.role, decision.tier)
            grouped.setdefault(key, []).append(decision)
    rows: list[RoleStats] = []
    for (task_type, role, tier), items in sorted(grouped.items()):
        priced = [item.cost_usd for item in items if item.cost_usd is not None]
        rows.append(
            RoleStats(
                task_type,
                role,
                tier,
                len(items),
                sum(1 for item in items if item.outcome == GREEN),
                sum_costs(item.cost_usd for item in items),
                len(priced),
            )
        )
    return tuple(rows)
