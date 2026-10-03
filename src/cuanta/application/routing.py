from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from cuanta.application.instinct import DecisionMaker
from cuanta.domain.claude_variants import MODEL_ALIASES
from cuanta.domain.costs import sum_costs
from cuanta.domain.errors import DomainFailure
from cuanta.domain.instinct import SCOPES, Choice
from cuanta.domain.ledger import RoutingDecision
from cuanta.domain.messages import Message, english, msg
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
    pin_issues,
    pinned,
    plan_route,
    policy_reason,
    pure_route,
    raise_for_risk,
    route_role,
)
from cuanta.ports.ledger import Ledger

SUMMARY_LIMIT = 200
RISK_LOW = 0.0
RISK_HIGH = 2.0
GREEN = "green"
RED = "red"
CLAUDE = "claude"
FAST_PIN_HINT = "use --profile balanced to honor role pins"


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
    pure: ModelEntry | None = None

    def route(self, role: Role) -> RoleRoute | None:
        return next((route for route in self.routes if route.role is role), None)


def route_model(plan: RoutePlan, role: Role) -> str:
    route = plan.route(role)
    if route is None or route.model is None:
        return ""
    return route.model.resolved or route.model.id


def _names(entry: ModelEntry) -> frozenset[str]:
    return frozenset(
        name.lower()
        for name in (entry.key, entry.id, entry.resolved, MODEL_ALIASES.get(entry.id, ""))
        if name
    )


def _wanted(reference: str) -> frozenset[str]:
    written = reference.strip().lower()
    name = written.removeprefix(f"{CLAUDE}:")
    return frozenset({written, name, MODEL_ALIASES.get(name, name)})


def _claude(entries: Sequence[ModelEntry]) -> tuple[ModelEntry, ...]:
    return tuple(entry for entry in entries if entry.engine == CLAUDE)


def _pure_entry(
    chosen: str, routes: Sequence[RoleRoute], entries: Sequence[ModelEntry]
) -> ModelEntry:
    listed = pinned(_claude(entries), chosen)
    if listed is not None:
        return listed
    wanted = _wanted(chosen)
    routed = next(
        (
            route.model
            for route in routes
            if route.model is not None and _names(route.model) & wanted
        ),
        None,
    )
    if routed is not None:
        return routed
    return ModelEntry(
        CLAUDE, chosen, chosen, "anthropic", resolved=MODEL_ALIASES.get(chosen, chosen)
    )


def _same_model(reference: str, entry: ModelEntry, entries: Sequence[ModelEntry]) -> bool:
    listed = pinned(_claude(entries), reference)
    names = _names(listed) if listed is not None else _wanted(reference)
    return bool(names & _names(entry))


def pure_plan(
    plan: RoutePlan, model: str, entries: Sequence[ModelEntry] = (), fast: bool = False
) -> RoutePlan:
    active = tuple(route for route in plan.routes if route.model is not None)
    if any(route.engine != CLAUDE for route in active):
        raise DomainFailure("pure implementation requires every role to use Claude")
    names = {route.model.id for route in active if route.model is not None}
    chosen = model or (next(iter(names)) if len(names) == 1 else "")
    if not chosen:
        raise DomainFailure("pure implementation requires one explicit model")
    entry = _pure_entry(chosen, active, entries)
    if any(not _same_model(pin, entry, entries) for pin in plan.policy.role_models.values()):
        if fast:
            reason = msg("route.fast_pin_conflict", model=entry.resolved or entry.id)
            raise DomainFailure(english(reason), FAST_PIN_HINT)
        raise DomainFailure("a role model conflicts with the pure implementation model")
    return replace(
        plan,
        routes=tuple(
            pure_route(route, entry) if route.model is not None else route for route in plan.routes
        ),
        pure=entry,
    )


def without_role(plan: RoutePlan, role: Role) -> RoutePlan:
    if plan.route(role) is None:
        return plan
    return replace(
        plan,
        requests=tuple(item for item in plan.requests if item.role is not role),
        routes=tuple(item for item in plan.routes if item.role is not role),
    )


def _in_place[T: (RoleRequest, RoleRoute)](items: Sequence[T], added: T) -> tuple[T, ...]:
    kept = [item for item in items if item.role is not Role.ANALYST]
    place = next(
        (index for index, item in enumerate(items) if item.role is Role.ANALYST),
        sum(1 for item in items if item.role is Role.ORCHESTRATOR),
    )
    kept.insert(min(place, len(kept)), added)
    return tuple(kept)


def tiers_allowed(policy: RoutingPolicy, role: Role) -> tuple[str, ...]:
    ceiling = tier_rank(policy.caps.ceiling(role))
    return tuple(tier.value for tier in TIER_ORDER if tier_rank(tier) <= ceiling)


def policy_pinned(policy: RoutingPolicy, mode: str = "") -> bool:
    return bool(policy.role_models) and with_overrides(policy, mode).mode is not RouteMode.OFF


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

    def pure(self, plan: RoutePlan, model: str, fast: bool = False) -> RoutePlan:
        return pure_plan(plan, model, tuple(self._catalog()), fast)

    def scout_plan(self, plan: RoutePlan) -> RoutePlan:
        if plan.route(Role.SCOUT) is not None:
            return plan
        policy = plan.policy
        tier = policy.tier_for(Role.SCOUT)
        request = RoleRequest(Role.SCOUT, tier, policy_reason(Role.SCOUT, tier))
        if policy.mode is RouteMode.OFF:
            route = RoleRoute(Role.SCOUT, tier, None, None, msg("route.off"))
        else:
            route = route_role(policy, tuple(self._catalog()), request)
            if plan.pure is not None and route.model is not None:
                route = pure_route(route, plan.pure)
        return replace(
            plan,
            requests=_in_place(plan.requests, request),
            routes=_in_place(plan.routes, route),
        )

    def pin_issues(
        self, plan: RoutePlan, pins: Mapping[str, str], engines: Sequence[str]
    ) -> tuple[Message, ...]:
        if not pins:
            return ()
        wanted = {Role(name): model for name, model in pins.items()}
        return pin_issues(wanted, tuple(self._catalog()), plan.routes, engines)

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
