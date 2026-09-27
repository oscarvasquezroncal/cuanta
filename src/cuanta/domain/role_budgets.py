from __future__ import annotations

import math
from collections.abc import Mapping

from cuanta.domain.routing import Role

ROLE_FLOOR = 0.05


def allocate_budget(costs: Mapping[Role, float | None], cap: float) -> dict[Role, float]:
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
    floor = cap * min(ROLE_FLOOR, 1 / len(weights))
    remainder = max(0.0, cap - floor * len(weights))
    total = sum(weights.values())
    shares = {role: floor + remainder * weight / total for role, weight in weights.items()}
    last = next(reversed(shares))
    shares[last] = max(0.0, cap - sum(share for role, share in shares.items() if role != last))
    return shares
