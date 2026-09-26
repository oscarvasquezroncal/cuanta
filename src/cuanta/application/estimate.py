from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

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
from cuanta.domain.ledger import Run
from cuanta.domain.messages import Message
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.routing import CostRange, Role, cost_range


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


def similar_costs(runs: Sequence[Run], task_type: str, depth: str) -> list[float]:
    wanted = depth or DEFAULT_DEPTH.value
    return [
        run.cost_usd
        for run in runs
        if run.kind == "mandate"
        and run.cost_usd is not None
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
    known = [price for price in priced.values() if price is not None]
    planned = plan_cost(known, chosen) if len(known) == len(priced) else None
    return Estimate(
        similar=similar,
        planned=planned,
        message=estimate_message(similar, planned),
        cap=cap,
        over=over_cap(similar, planned, cap),
        roles=roles,
        depth=chosen.depth.value,
    )
