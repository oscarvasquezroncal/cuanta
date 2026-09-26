from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.detection import VerifyTier

LOOP_OUT_OF_SCOPE = (
    "anything unrelated to the failing signatures; never delete, skip or weaken a test"
)

SANCTIONED = re.compile(r"unattended[^\n]{0,120}sanctioned", re.IGNORECASE)
REFUSED = re.compile(
    r"no unattended loop|not sanctioned|get a sensor first|no sensor", re.IGNORECASE
)


class StopReason(StrEnum):
    GREEN = "green"
    PERSISTENT = "persistent signature"
    MAX_ITERATIONS = "max iterations"
    BUDGET = "budget reached"
    COST_UNKNOWN = "cost unknown"
    ENGINE_ERROR = "engine error"


@dataclass(frozen=True, slots=True)
class LoopGate:
    allowed: bool
    missing: str


def loop_gate(tier: VerifyTier, evidence: str, loop_text: str | None) -> LoopGate:
    if tier is not VerifyTier.STRONG:
        return LoopGate(False, f"VERIFY_TIER={tier} ({evidence}); the loop needs strong")
    if loop_text is None:
        return LoopGate(False, "docs/LOOP.md is missing; run cuanta init")
    if REFUSED.search(loop_text) or not SANCTIONED.search(loop_text):
        return LoopGate(False, "docs/LOOP.md does not sanction an unattended loop")
    return LoopGate(True, "")


@dataclass(frozen=True, slots=True)
class Iteration:
    number: int
    test_status: str
    signatures: int
    mandate_run: str
    mandate_ok: bool
    cost_usd: float | None


def next_stop(
    test_status: str,
    iterations_done: int,
    max_iterations: int,
    spent_usd: float | None,
    budget_usd: float,
) -> StopReason | None:
    if test_status == "green":
        return StopReason.GREEN
    if test_status == "persistent_failure":
        return StopReason.PERSISTENT
    if budget_usd > 0:
        if spent_usd is None:
            return StopReason.COST_UNKNOWN
        if spent_usd >= budget_usd:
            return StopReason.BUDGET
    if iterations_done >= max_iterations:
        return StopReason.MAX_ITERATIONS
    return None
