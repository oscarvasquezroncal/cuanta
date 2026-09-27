from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cuanta.domain.depth import MIN_RANGE_SAMPLES
from cuanta.domain.routing import CostRange

EstimateSource = Literal["history", "plan", "none"]
HISTORY: EstimateSource = "history"
PLAN: EstimateSource = "plan"
NO_ESTIMATE: EstimateSource = "none"


@dataclass(frozen=True, slots=True)
class RunEstimate:
    source: EstimateSource = NO_ESTIMATE
    low: float | None = None
    high: float | None = None
    samples: int = 0


def estimate_bounds(similar: CostRange, planned: float | None) -> RunEstimate:
    if similar.samples >= MIN_RANGE_SAMPLES and similar.p50 is not None:
        return RunEstimate(HISTORY, similar.p50, similar.p90, similar.samples)
    if similar.samples == 1 and similar.p50 is not None:
        return RunEstimate(HISTORY, similar.p50, similar.p50, 1)
    if similar.samples > 1 and similar.p50 is not None:
        return RunEstimate(HISTORY, similar.low, similar.high, similar.samples)
    if planned is not None:
        return RunEstimate(PLAN, planned, planned, 0)
    return RunEstimate()


def estimate_error(low: float | None, high: float | None, actual: float | None) -> float | None:
    if low is None or actual is None:
        return None
    top = low if high is None else high
    if low <= actual <= top:
        return 0.0
    if actual > top:
        return (actual - top) / top if top > 0 else None
    return (actual - low) / low if low > 0 else None
