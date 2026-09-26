from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

CostSource = Literal["reported", "estimated", "unknown"]


def sum_costs(values: Iterable[float | None]) -> float | None:
    total = 0.0
    for value in values:
        if value is None:
            return None
        total += value
    return total
