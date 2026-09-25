from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.messages import Message, msg
from cuanta.domain.models import Tier
from cuanta.domain.pricing import PER_MILLION, Price, dollars
from cuanta.domain.routing import CostRange


class Depth(StrEnum):
    QUICK = "quick"
    NORMAL = "normal"
    DEEP = "deep"


DEPTHS = tuple(Depth)
DEFAULT_DEPTH = Depth.NORMAL
MIN_RANGE_SAMPLES = 3
PREFIX_TOKENS = 20_000
TOKENS_PER_READ = 1_500
OUTPUT_PER_CONTEXT = 3_000


@dataclass(frozen=True, slots=True)
class DepthProfile:
    depth: Depth
    tier_cap: Tier
    effort: str
    pack_tokens: int
    read_budget: int
    cost_cap_usd: float


INVESTIGATION_CAPS = {Depth.QUICK: 0.25, Depth.NORMAL: 0.60, Depth.DEEP: 1.50}
PIPELINE_CAPS = {Depth.QUICK: 0.75, Depth.NORMAL: 2.00, Depth.DEEP: 5.00}
TIER_CAPS = {Depth.QUICK: Tier.STANDARD, Depth.NORMAL: Tier.PREMIUM, Depth.DEEP: Tier.FRONTIER}
EFFORTS = {Depth.QUICK: "low", Depth.NORMAL: "medium", Depth.DEEP: "high"}
PACK_TOKENS = {Depth.QUICK: 2_000, Depth.NORMAL: 4_000, Depth.DEEP: 6_000}
READ_BUDGETS = {Depth.QUICK: 8, Depth.NORMAL: 20, Depth.DEEP: 40}


def parse_depth(text: str) -> Depth:
    try:
        return Depth(text.strip().lower())
    except ValueError:
        return DEFAULT_DEPTH


def profile(depth: Depth, task_type: str) -> DepthProfile:
    caps = INVESTIGATION_CAPS if task_type == "investigation" else PIPELINE_CAPS
    return DepthProfile(
        depth=depth,
        tier_cap=TIER_CAPS[depth],
        effort=EFFORTS[depth],
        pack_tokens=PACK_TOKENS[depth],
        read_budget=READ_BUDGETS[depth],
        cost_cap_usd=caps[depth],
    )


def read_budget_line(chosen: DepthProfile, graph_available: bool = True) -> str:
    navigation = (
        "Prefer graph queries and search over reading whole files."
        if graph_available
        else "Prefer search over reading whole files."
    )
    return (
        f"READ BUDGET ({chosen.depth.value}): open at most {chosen.read_budget} files. {navigation}"
    )


def context_cost(price: Price, chosen: DepthProfile) -> float:
    reads = chosen.read_budget * TOKENS_PER_READ
    requests = chosen.read_budget // 2 + 2
    written = PREFIX_TOKENS + reads
    read_back = requests * (PREFIX_TOKENS + reads // 2)
    total = (
        written * price.cache_write
        + read_back * price.cache_read
        + OUTPUT_PER_CONTEXT * price.output
    )
    return total / PER_MILLION


def plan_cost(prices: Sequence[Price], chosen: DepthProfile) -> float | None:
    if not prices:
        return None
    return sum(context_cost(price, chosen) for price in prices)


def estimate_message(similar: CostRange, planned: float | None) -> Message:
    if similar.samples >= MIN_RANGE_SAMPLES and similar.p50 is not None:
        return msg(
            "estimate.range",
            low=dollars(similar.p50),
            high=dollars(similar.p90),
            count=similar.samples,
        )
    if similar.samples == 1 and similar.p50 is not None:
        return msg("estimate.one", cost=dollars(similar.p50))
    if similar.samples > 1 and similar.p50 is not None:
        return msg(
            "estimate.few",
            count=similar.samples,
            low=dollars(similar.low),
            high=dollars(similar.high),
        )
    if planned is not None:
        return msg("estimate.plan", cost=dollars(planned))
    return msg("estimate.none")


def over_cap(similar: CostRange, planned: float | None, cap: float) -> bool:
    if cap <= 0:
        return False
    worst = similar.p90 if similar.p90 is not None else planned
    return worst is not None and worst > cap
