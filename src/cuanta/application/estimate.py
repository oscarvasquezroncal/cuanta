from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from cuanta.application.routing import RoutePlan
from cuanta.domain.depth import (
    DEFAULT_DEPTH,
    DepthProfile,
    context_cost,
    estimate_message,
    over_cap,
    parse_depth,
    plan_cost,
    profile,
)
from cuanta.domain.estimates import RunEstimate, estimate_bounds
from cuanta.domain.ledger import Run
from cuanta.domain.messages import Message
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.real_costs import known_cost
from cuanta.domain.routing import CostRange, Role, cost_range

Estimator = Callable[[RoutePlan | None, str, str], RunEstimate]


@dataclass(frozen=True, slots=True)
class RoleCost:
    role: Role
    low: float | None
    high: float | None


@dataclass(frozen=True, slots=True)
class Estimate:
    similar: CostRange
    planned: float | None
    message: Message
    cap: float
    over: bool
    roles: tuple[RoleCost, ...]
    depth: str
    bounds: RunEstimate = field(default_factory=RunEstimate)


def similar_costs(runs: Sequence[Run], task_type: str, depth: str) -> list[float]:
    wanted = depth or DEFAULT_DEPTH.value
    return [
        cost
        for run in runs
        if run.kind == "mandate"
        and (cost := known_cost(run)) is not None
        and run.task_type == task_type
        and (run.depth or DEFAULT_DEPTH.value) == wanted
    ]


def route_price(plan: RoutePlan, role: Role, prices: PriceTable) -> Price | None:
    route = plan.route(role)
    if route is None or route.model is None:
        return None
    return prices.lookup(route.model.resolved or route.model.id) or prices.lookup(route.model.id)


def role_cost(price: Price | None, chosen: DepthProfile, role: Role) -> RoleCost:
    if price is None:
        return RoleCost(role, None, None)
    lighter = replace(chosen, read_budget=max(1, chosen.read_budget // 2))
    return RoleCost(role, context_cost(price, lighter), context_cost(price, chosen))


def estimate(
    plan: RoutePlan,
    runs: Sequence[Run],
    prices: PriceTable,
    task_type: str,
    depth: str,
    cap: float,
) -> Estimate:
    chosen = profile(parse_depth(depth), task_type)
    similar = cost_range(similar_costs(runs, task_type, chosen.depth.value))
    priced = {route.role: route_price(plan, route.role, prices) for route in plan.routes}
    roles = tuple(role_cost(price, chosen, role) for role, price in priced.items())
    planned = _planned(priced, chosen)
    return Estimate(
        similar=similar,
        planned=planned,
        message=estimate_message(similar, planned),
        cap=cap,
        over=over_cap(similar, planned, cap),
        roles=roles,
        depth=chosen.depth.value,
        bounds=estimate_bounds(similar, planned),
    )


def _planned(priced: Mapping[Role, Price | None], chosen: DepthProfile) -> float | None:
    known = [price for price in priced.values() if price is not None]
    return plan_cost(known, chosen) if len(known) == len(priced) else None


def run_estimate(
    plan: RoutePlan | None,
    runs: Sequence[Run],
    prices: PriceTable,
    task_type: str,
    depth: str,
) -> RunEstimate:
    chosen = profile(parse_depth(depth), task_type)
    similar = cost_range(similar_costs(runs, task_type, chosen.depth.value))
    if plan is None:
        return estimate_bounds(similar, None)
    priced = {route.role: route_price(plan, route.role, prices) for route in plan.routes}
    return estimate_bounds(similar, _planned(priced, chosen))
