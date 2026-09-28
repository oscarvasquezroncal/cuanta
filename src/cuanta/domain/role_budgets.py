from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.routing import Role

ROLE_FLOOR = 0.05
ROLE_FLOORS: Mapping[Role, float] = {
    Role.ANALYST: 0.12,
    Role.SENIOR: 0.20,
    Role.TESTER: 0.08,
    Role.DOCS: 0.04,
}
OPTIONAL_ROLES = frozenset({Role.DOCS})
DEFAULT_MARGIN = 0.10
MIN_MARGIN = 0.05
MAX_MARGIN = 0.25
MARGIN_SAFETY = 1.25
MIN_SAMPLES = 3
REPAIR_FRACTION = 0.15


@dataclass(frozen=True, slots=True)
class RepairBudget:
    shares: Mapping[Role, float]
    repair_usd: float
    from_docs: bool


@dataclass(frozen=True, slots=True)
class Overrun:
    cap_usd: float
    cost_usd: float

    @property
    def ratio(self) -> float:
        return max(0.0, self.cost_usd - self.cap_usd) / self.cap_usd if self.cap_usd > 0 else 0.0


def floor_fraction(role: Role) -> float:
    return ROLE_FLOORS.get(role, ROLE_FLOOR)


def allocate_budget(
    costs: Mapping[Role, float | None],
    cap: float,
    floors: Mapping[Role, float] | None = None,
) -> dict[Role, float]:
    if not math.isfinite(cap):
        raise ValueError("Role budget must be finite")
    if cap <= 0 or not costs:
        return {}
    positive = [
        value
        for value in costs.values()
        if value is not None and value > 0 and math.isfinite(value)
    ]
    fallback = sum(positive) / len(positive) if positive else 1.0
    weights = {
        role: value if value is not None and value > 0 and math.isfinite(value) else fallback
        for role, value in costs.items()
    }
    chosen = floors if floors is not None else {role: floor_fraction(role) for role in weights}
    fractions = {role: max(0.0, chosen.get(role, ROLE_FLOOR)) for role in weights}
    total_fraction = sum(fractions.values())
    if total_fraction > 1.0:
        fractions = {role: value / total_fraction for role, value in fractions.items()}
    reserved = {role: cap * fraction for role, fraction in fractions.items()}
    remainder = max(0.0, cap - sum(reserved.values()))
    total = sum(weights.values())
    shares = {role: reserved[role] + remainder * weights[role] / total for role in weights}
    last = next(reversed(shares))
    shares[last] = max(0.0, cap - sum(share for role, share in shares.items() if role != last))
    return shares


def history_weights(
    samples: Mapping[Role, Sequence[float]], roles: Iterable[Role], minimum: int = MIN_SAMPLES
) -> dict[Role, float] | None:
    weights: dict[Role, float] = {}
    for role in roles:
        values = [value for value in samples.get(role, ()) if value > 0 and math.isfinite(value)]
        if len(values) < minimum:
            return None
        weights[role] = statistics.median(values)
    return weights or None


def learned_margin(overruns: Sequence[Overrun], minimum: int = MIN_SAMPLES) -> float:
    ratios = sorted(item.ratio for item in overruns if item.cap_usd > 0)
    if len(ratios) < minimum:
        return DEFAULT_MARGIN
    index = min(len(ratios) - 1, math.ceil(0.9 * len(ratios)) - 1)
    return min(MAX_MARGIN, max(MIN_MARGIN, ratios[index] * MARGIN_SAFETY))


def soft_cap(share: float, margin: float) -> float:
    return max(0.0, share * (1.0 - min(max(margin, 0.0), MAX_MARGIN)))


def floors_usd(roles: Iterable[Role], cap: float) -> float:
    return sum(cap * floor_fraction(role) for role in roles)


def _whole_cap_floors(
    roles: Iterable[Role], floors: Mapping[Role, float] | None, scale: float
) -> dict[Role, float]:
    chosen = floors if floors is not None else ROLE_FLOORS
    return {role: chosen.get(role, ROLE_FLOOR) * scale for role in roles}


def repair_budget(
    costs: Mapping[Role, float | None],
    cap: float,
    docs: bool,
    floors: Mapping[Role, float] | None = None,
) -> RepairBudget:
    if not math.isfinite(cap):
        raise ValueError("Role budget must be finite")
    if cap <= 0 or not costs:
        return RepairBudget({}, 0.0, False)
    if docs:
        reserve = cap * REPAIR_FRACTION
        scaled = _whole_cap_floors(costs, floors, 1.0 / (1.0 - REPAIR_FRACTION))
        return RepairBudget(allocate_budget(costs, cap - reserve, scaled), reserve, False)
    funded = allocate_budget({**costs, Role.DOCS: costs.get(Role.DOCS)}, cap, floors)
    reserve = funded.pop(Role.DOCS, 0.0)
    return RepairBudget(funded, reserve, True)


def role_split(
    costs: Mapping[Role, float | None],
    cap: float,
    fix: bool,
    floors: Mapping[Role, float] | None = None,
) -> RepairBudget:
    if fix:
        return repair_budget(costs, cap, Role.DOCS in costs, floors)
    return RepairBudget(allocate_budget(costs, cap, floors), 0.0, False)
