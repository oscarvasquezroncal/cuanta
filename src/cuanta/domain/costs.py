from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

CostSource = Literal["reported", "estimated", "unknown"]
ESTIMATED: CostSource = "estimated"


@dataclass(frozen=True, slots=True)
class CostTotal:
    known_usd: float = 0.0
    known: int = 0
    missing: int = 0
    estimated: int = 0

    @property
    def lower_bound(self) -> bool:
        return self.missing > 0

    @property
    def value(self) -> float | None:
        return self.known_usd if self.known else None


def total_costs(costs: Iterable[tuple[float | None, bool]]) -> CostTotal:
    known_usd = 0.0
    known = missing = estimated = 0
    for value, is_estimate in costs:
        if value is None:
            missing += 1
            continue
        known_usd += value
        known += 1
        estimated += is_estimate
    return CostTotal(known_usd, known, missing, estimated)


def median(values: Iterable[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def sum_costs(values: Iterable[float | None]) -> float | None:
    total = 0.0
    for value in values:
        if value is None:
            return None
        total += value
    return total
