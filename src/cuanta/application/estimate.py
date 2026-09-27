from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from math import isfinite

from cuanta.application.routing import RoutePlan
from cuanta.domain.costs import median
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
from cuanta.domain.estimates import CALIBRATED, RunEstimate, estimate_bounds
from cuanta.domain.ledger import Run
from cuanta.domain.messages import Message, msg
from cuanta.domain.pricing import Price, PriceTable, dollars
from cuanta.domain.real_costs import Attempt, attempts
from cuanta.domain.role_budgets import allocate_budget
from cuanta.domain.routing import CostRange, Role, cost_range

Estimator = Callable[[RoutePlan | None, str, str], RunEstimate]
ShapeEstimator = Callable[[RoutePlan | None, str, str, str], RunEstimate]


@dataclass(frozen=True, slots=True)
class RoleCost:
    role: Role
    low: float | None
    high: float | None
    share: float = 0.0


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


def _shape(item: Attempt, shapes: Mapping[str, str] | None) -> str:
    if item.run.kind == "cross":
        return "pipeline"
    return shapes.get(item.run.id, "") if shapes is not None else ""


def _matching(
    runs: Sequence[Run], depth: str, shape: str, shapes: Mapping[str, str] | None
) -> tuple[Attempt, ...]:
    wanted = depth or DEFAULT_DEPTH.value
    return tuple(
        item
        for item in attempts(runs)
        if (item.run.depth or DEFAULT_DEPTH.value) == wanted
        and (not shape or _shape(item, shapes) == shape)
        and item.cost is not None
        and isfinite(item.cost)
        and item.cost >= 0
    )


def similar_costs(
    runs: Sequence[Run],
    task_type: str,
    depth: str,
    shape: str = "",
    shapes: Mapping[str, str] | None = None,
) -> list[float]:
    return [
        item.cost
        for item in _matching(runs, depth, shape, shapes)
        if item.run.task_type == task_type and item.cost is not None
    ]


def calibrated_bounds(
    similar: CostRange,
    planned: float | None,
    runs: Sequence[Run],
    depth: str,
    shape: str,
    shapes: Mapping[str, str] | None,
) -> RunEstimate:
    bounds = estimate_bounds(similar, planned)
    if bounds.source != "plan" or not shape:
        return bounds
    ratios = []
    for item in _matching(runs, depth, shape, shapes):
        run = item.run
        baseline = run.estimate_high or run.estimate_low
        if (
            run.estimate_source == "plan"
            and run.status == "ok"
            and baseline is not None
            and baseline > 0
            and isfinite(baseline)
            and item.cost is not None
            and item.cost > 0
        ):
            ratios.append(item.cost / baseline)
    factor = median(ratios)
    if factor is None or not isfinite(factor) or factor <= 0 or planned is None:
        return bounds
    adjusted = planned * factor
    if not isfinite(adjusted):
        return bounds
    return RunEstimate(CALIBRATED, adjusted, adjusted, len(ratios), factor)


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
    shape: str = "",
    shapes: Mapping[str, str] | None = None,
) -> Estimate:
    chosen = profile(parse_depth(depth), task_type)
    similar = cost_range(similar_costs(runs, task_type, chosen.depth.value, shape, shapes))
    priced = {route.role: route_price(plan, route.role, prices) for route in plan.routes}
    roles = tuple(role_cost(price, chosen, role) for role, price in priced.items())
    planned = _planned(priced, chosen)
    bounds = calibrated_bounds(similar, planned, runs, chosen.depth.value, shape, shapes)
    budgets = allocate_budget(
        {
            cost.role: cost.high
            for cost in roles
            if cost.role is not Role.ORCHESTRATOR
            and (route := plan.route(cost.role)) is not None
            and route.model is not None
        },
        cap,
    )
    roles = tuple(replace(cost, share=budgets.get(cost.role, 0.0)) for cost in roles)
    displayed = bounds.high if bounds.source == CALIBRATED else planned
    message = (
        msg(
            "estimate.calibrated",
            cost=dollars(displayed),
            factor=f"{bounds.factor:.2f}",
            count=bounds.samples,
        )
        if bounds.factor is not None
        else estimate_message(similar, displayed)
    )
    return Estimate(
        similar=similar,
        planned=planned,
        message=message,
        cap=cap,
        over=over_cap(similar, displayed, cap),
        roles=roles,
        depth=chosen.depth.value,
        bounds=bounds,
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
    shape: str = "",
    shapes: Mapping[str, str] | None = None,
) -> RunEstimate:
    chosen = profile(parse_depth(depth), task_type)
    similar = cost_range(similar_costs(runs, task_type, chosen.depth.value, shape, shapes))
    if plan is None:
        return estimate_bounds(similar, None)
    priced = {route.role: route_price(plan, route.role, prices) for route in plan.routes}
    return calibrated_bounds(
        similar, _planned(priced, chosen), runs, chosen.depth.value, shape, shapes
    )
