from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from cuanta.domain.messages import Message, msg
from cuanta.domain.models import TIER_ORDER, ModelEntry, Tier, TierSource, parse_tier, tier_rank


class RouteMode(StrEnum):
    AUTO = "auto"
    FIXED = "fixed"
    OFF = "off"


class Role(StrEnum):
    ORCHESTRATOR = "orchestrator"
    ANALYST = "analyst"
    SENIOR = "senior"
    TESTER = "tester"
    DOCS = "docs"


ROLES = tuple(Role)
ENGINE_ORDER = ("claude", "codex", "opencode")
DEFAULT_ROLE_TIERS: Mapping[Role, Tier] = {
    Role.ORCHESTRATOR: Tier.STANDARD,
    Role.ANALYST: Tier.STANDARD,
    Role.SENIOR: Tier.PREMIUM,
    Role.TESTER: Tier.PREMIUM,
    Role.DOCS: Tier.ECONOMY,
}
DEFAULT_CAP = Tier.PREMIUM
DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_SUCCESS_THRESHOLD = 0.8
DEFAULT_MIN_SAMPLES = 5


class Preset(StrEnum):
    SAVE = "save"
    BALANCED = "balanced"
    BEST = "best"


PRESET_ROLES: Mapping[Preset, Mapping[Role, Tier]] = {
    Preset.SAVE: {
        Role.ORCHESTRATOR: Tier.ECONOMY,
        Role.ANALYST: Tier.ECONOMY,
        Role.SENIOR: Tier.STANDARD,
        Role.TESTER: Tier.STANDARD,
        Role.DOCS: Tier.ECONOMY,
    },
    Preset.BALANCED: DEFAULT_ROLE_TIERS,
    Preset.BEST: {
        Role.ORCHESTRATOR: Tier.PREMIUM,
        Role.ANALYST: Tier.PREMIUM,
        Role.SENIOR: Tier.PREMIUM,
        Role.TESTER: Tier.PREMIUM,
        Role.DOCS: Tier.STANDARD,
    },
}
PRESET_CAPS: Mapping[Preset, Tier] = {
    Preset.SAVE: Tier.STANDARD,
    Preset.BALANCED: Tier.PREMIUM,
    Preset.BEST: Tier.PREMIUM,
}


@dataclass(frozen=True, slots=True)
class Caps:
    max_tier: Mapping[Role, Tier] = field(default_factory=dict)
    mandate_usd: float = 0.0
    daily_usd: float = 0.0
    frontier: bool = False

    def ceiling(self, role: Role) -> Tier:
        cap = self.max_tier.get(role, DEFAULT_CAP)
        if not self.frontier and cap is Tier.FRONTIER:
            return Tier.PREMIUM
        return cap


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    mode: RouteMode = RouteMode.AUTO
    engines: tuple[str, ...] = ENGINE_ORDER
    prefer_subscription: bool = True
    roles: Mapping[Role, Tier] = field(default_factory=lambda: dict(DEFAULT_ROLE_TIERS))
    caps: Caps = field(default_factory=Caps)
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    preset: Preset = Preset.BALANCED
    success_threshold: float = DEFAULT_SUCCESS_THRESHOLD
    min_samples: int = DEFAULT_MIN_SAMPLES
    role_models: Mapping[Role, str] = field(default_factory=dict)
    fixed_roles: frozenset[Role] = frozenset()

    def tier_for(self, role: Role) -> Tier:
        return self.roles.get(role, DEFAULT_ROLE_TIERS[role])


@dataclass(frozen=True, slots=True)
class RoleRequest:
    role: Role
    tier: Tier
    reason: Message
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class RoleRoute:
    role: Role
    requested: Tier
    tier: Tier | None
    model: ModelEntry | None
    reason: Message
    confidence: float | None = None

    @property
    def engine(self) -> str:
        return self.model.engine if self.model else ""


def _number(value: object, fallback: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback


def _role(name: str) -> Role | None:
    try:
        return Role(name.strip().lower())
    except ValueError:
        return None


def _tiers(table: Mapping[str, object], prefix: str) -> dict[Role, Tier]:
    found: dict[Role, Tier] = {}
    for key, value in table.items():
        if not key.startswith(prefix) or not isinstance(value, str):
            continue
        role = _role(key[len(prefix) :])
        tier = parse_tier(value)
        if role is not None and tier is not None:
            found[role] = tier
    return found


def apply_preset(policy: RoutingPolicy, preset: Preset) -> RoutingPolicy:
    cap = PRESET_CAPS[preset]
    return replace(
        policy,
        preset=preset,
        roles=dict(PRESET_ROLES[preset]),
        caps=replace(policy.caps, max_tier=dict.fromkeys(ROLES, cap)),
    )


def parse_policy(table: Mapping[str, object]) -> RoutingPolicy:
    policy = RoutingPolicy()
    preset_name = str(table.get("preset", "")).strip().lower()
    if preset_name in {item.value for item in Preset}:
        policy = apply_preset(policy, Preset(preset_name))
    mode = str(table.get("mode", policy.mode.value)).strip().lower()
    engines = table.get("engines")
    listed = (
        tuple(str(item).strip().lower() for item in engines if str(item).strip())
        if isinstance(engines, list | tuple)
        else policy.engines
    )
    roles = {**policy.roles, **_tiers(table, "roles.")}
    caps = replace(
        policy.caps,
        max_tier={**policy.caps.max_tier, **_tiers(table, "caps.")},
        mandate_usd=_number(table.get("caps.mandate_usd"), policy.caps.mandate_usd),
        daily_usd=_number(table.get("caps.daily_usd"), policy.caps.daily_usd),
        frontier=table.get("caps.frontier", policy.caps.frontier) is True,
    )
    role_models = {
        role: str(value)
        for key, value in table.items()
        if key.startswith("models.") and (role := _role(key[len("models.") :])) is not None
    }
    fixed_roles = frozenset(
        role
        for key, value in table.items()
        if key.startswith("decide.")
        and value is False
        and (role := _role(key[len("decide.") :])) is not None
    )
    return replace(
        policy,
        mode=RouteMode(mode) if mode in {item.value for item in RouteMode} else policy.mode,
        engines=tuple(name for name in listed if name in ENGINE_ORDER) or policy.engines,
        prefer_subscription=table.get("prefer_subscription", policy.prefer_subscription)
        is not False,
        roles=roles,
        caps=caps,
        min_confidence=_number(table.get("min_confidence"), policy.min_confidence),
        success_threshold=_number(table.get("learn.success_threshold"), policy.success_threshold),
        min_samples=int(_number(table.get("learn.min_samples"), policy.min_samples)),
        role_models=role_models,
        fixed_roles=fixed_roles,
    )


def capped(policy: RoutingPolicy, role: Role, tier: Tier) -> Tier:
    ceiling = policy.caps.ceiling(role)
    return tier if tier_rank(tier) <= tier_rank(ceiling) else ceiling


def search_order(requested: Tier, ceiling: Tier) -> tuple[Tier, ...]:
    start = tier_rank(requested)
    below = [TIER_ORDER[index] for index in range(start - 1, -1, -1)]
    above = [TIER_ORDER[index] for index in range(start + 1, tier_rank(ceiling) + 1)]
    return (requested, *below, *above)


SOURCE_PREFERENCE = (TierSource.OVERRIDE, TierSource.ANCHOR, TierSource.FAMILY, TierSource.PRICE)


def _rank_key(entry: ModelEntry, engines: Sequence[str]) -> tuple[int, int, int, str]:
    source = entry.tier_source
    return (
        engines.index(entry.engine) if entry.engine in engines else len(engines),
        0 if entry.default else 1,
        SOURCE_PREFERENCE.index(source) if source in SOURCE_PREFERENCE else len(SOURCE_PREFERENCE),
        entry.id,
    )


def candidates(
    entries: Iterable[ModelEntry], engines: Sequence[str], tier: Tier
) -> list[ModelEntry]:
    usable = [entry for entry in entries if entry.engine in engines and entry.tier is tier]
    return sorted(usable, key=lambda entry: _rank_key(entry, engines))


def pinned(entries: Iterable[ModelEntry], reference: str) -> ModelEntry | None:
    wanted = reference.lower()
    for entry in entries:
        if wanted in {entry.key.lower(), entry.id.lower(), entry.resolved.lower()}:
            return entry
    return None


def route_role(
    policy: RoutingPolicy, entries: Sequence[ModelEntry], request: RoleRequest
) -> RoleRoute:
    role = request.role
    forced = policy.role_models.get(role)
    if forced:
        chosen = pinned(entries, forced)
        if chosen is not None:
            return RoleRoute(
                role,
                request.tier,
                chosen.tier,
                chosen,
                msg("route.pinned", model=chosen.key),
                request.confidence,
            )
    requested = capped(policy, role, request.tier)
    ceiling = policy.caps.ceiling(role)
    for tier in search_order(requested, ceiling):
        found = candidates(entries, policy.engines, tier)
        if not found:
            continue
        model = found[0]
        if tier is requested and requested is request.tier:
            reason = request.reason
        elif tier is requested:
            reason = msg("route.capped", requested=request.tier.value, cap=ceiling.value)
        elif tier_rank(tier) < tier_rank(requested):
            reason = msg("route.stepped_down", requested=requested.value, tier=tier.value)
        else:
            reason = msg("route.stepped_up", requested=requested.value, tier=tier.value)
        return RoleRoute(role, request.tier, tier, model, reason, request.confidence)
    return RoleRoute(
        role, request.tier, None, None, msg("route.none", tier=requested.value), request.confidence
    )


def plan_route(
    policy: RoutingPolicy, entries: Sequence[ModelEntry], requests: Sequence[RoleRequest]
) -> tuple[RoleRoute, ...]:
    return tuple(route_role(policy, entries, request) for request in requests)


def policy_reason(role: Role, tier: Tier) -> Message:
    return msg(
        "route.policy_role",
        role=msg(f"role.{role.value}"),
        tier=msg(f"tier.{tier.value}"),
        purpose=msg(f"purpose.{role.value}"),
    )


def default_requests(
    policy: RoutingPolicy, roles: Sequence[Role] = ROLES
) -> tuple[RoleRequest, ...]:
    return tuple(
        RoleRequest(role, policy.tier_for(role), policy_reason(role, policy.tier_for(role)))
        for role in roles
    )


def roles_that_run(task_type: str, simple: bool = False) -> tuple[Role, ...]:
    if simple:
        return ()
    if task_type == "investigation":
        return (Role.ANALYST,)
    return ROLES


def depth_capped(policy: RoutingPolicy, cap: Tier) -> RoutingPolicy:
    ceilings = {role: min(policy.caps.ceiling(role), cap, key=tier_rank) for role in ROLES}
    frontier = policy.caps.frontier and cap is Tier.FRONTIER
    caps = replace(policy.caps, max_tier=ceilings, frontier=frontier)
    roles = {role: min(policy.tier_for(role), ceilings[role], key=tier_rank) for role in ROLES}
    return replace(policy, caps=caps, roles=roles)


def gated(policy: RoutingPolicy, request: RoleRequest) -> RoleRequest:
    if request.confidence is None or request.confidence >= policy.min_confidence:
        return request
    tier = policy.tier_for(request.role)
    reason = msg(
        "route.low_confidence",
        confidence=f"{request.confidence:.0%}",
        minimum=f"{policy.min_confidence:.0%}",
        tier=tier.value,
    )
    return RoleRequest(request.role, tier, reason, request.confidence)


def escalate(policy: RoutingPolicy, request: RoleRequest) -> RoleRequest:
    ceiling = policy.caps.ceiling(request.role)
    rank = tier_rank(request.tier)
    if rank >= tier_rank(ceiling):
        return RoleRequest(
            request.role,
            request.tier,
            msg("route.escalation_capped", tier=request.tier.value),
            request.confidence,
        )
    raised = TIER_ORDER[rank + 1]
    return RoleRequest(
        request.role,
        raised,
        msg("route.escalated", previous=request.tier.value, tier=raised.value),
        request.confidence,
    )


@dataclass(frozen=True, slots=True)
class TierOutcome:
    tier: Tier
    samples: int
    successes: int

    @property
    def rate(self) -> float:
        return self.successes / self.samples if self.samples else 0.0


def learned_tier(
    policy: RoutingPolicy, role: Role, outcomes: Sequence[TierOutcome]
) -> RoleRequest | None:
    ceiling = policy.caps.ceiling(role)
    eligible = [
        outcome
        for outcome in outcomes
        if outcome.samples >= policy.min_samples
        and outcome.rate >= policy.success_threshold
        and tier_rank(outcome.tier) <= tier_rank(ceiling)
    ]
    if not eligible:
        return None
    best = min(eligible, key=lambda outcome: tier_rank(outcome.tier))
    reason = msg(
        "route.learned",
        tier=best.tier.value,
        rate=f"{best.rate:.0%}",
        samples=best.samples,
    )
    return RoleRequest(role, best.tier, reason, best.rate)


def raise_for_risk(request: RoleRequest, risk: float, policy: RoutingPolicy) -> RoleRequest:
    if request.role is not Role.TESTER or risk < 1.5:
        return request
    raised = escalate(policy, request)
    if raised.tier is request.tier:
        return request
    return RoleRequest(
        request.role,
        raised.tier,
        msg("route.risk", risk=f"{risk:.1f}", tier=raised.tier.value),
        request.confidence,
    )


INVESTIGATION_TIERS = {Role.ANALYST: Tier.STANDARD, Role.DOCS: Tier.ECONOMY}


def investigation_policy(policy: RoutingPolicy, task_type: str) -> RoutingPolicy:
    if task_type != "investigation":
        return policy
    roles = dict(policy.roles)
    for role, tier in INVESTIGATION_TIERS.items():
        if role not in policy.fixed_roles:
            roles[role] = tier
    return replace(policy, roles=roles)


CLARITY_CAP = Tier.STANDARD
LOW_CLARITY = 0.6


def clarity_capped(request: RoleRequest, clarity: float | None) -> RoleRequest:
    if clarity is None or clarity >= LOW_CLARITY:
        return request
    if tier_rank(request.tier) <= tier_rank(CLARITY_CAP):
        return request
    reason = msg("route.low_clarity", clarity=f"{clarity:.1f}", tier=CLARITY_CAP.value)
    return RoleRequest(request.role, CLARITY_CAP, reason, request.confidence)


def single_model(routes: Sequence[RoleRoute]) -> RoleRoute | None:
    chosen = [route for route in routes if route.model is not None and route.tier is not None]
    if not chosen:
        return None
    return max(chosen, key=lambda route: tier_rank(route.tier or Tier.ECONOMY))


@dataclass(frozen=True, slots=True)
class CostRange:
    p50: float | None
    p90: float | None
    samples: int
    low: float | None = None
    high: float | None = None


def percentile(values: Sequence[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def cost_range(costs: Sequence[float]) -> CostRange:
    return CostRange(
        percentile(costs, 0.5),
        percentile(costs, 0.9),
        len(costs),
        min(costs) if costs else None,
        max(costs) if costs else None,
    )
