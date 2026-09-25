from __future__ import annotations

import time
from collections.abc import Sequence

from cuanta.domain.instinct import (
    Choice,
    Context,
    Noul,
    Receipt,
    Score,
    heuristic_depth,
    heuristic_intake_type,
    heuristic_risk,
    heuristic_role_tier,
    heuristic_scope,
    heuristic_triage,
    uniform_choice,
)
from cuanta.domain.messages import Message, msg


class HeuristicInstinct:
    def __init__(self, *_: object) -> None:
        return None

    @property
    def name(self) -> str:
        return "heuristic"

    @property
    def remote(self) -> bool:
        return False

    def available(self) -> tuple[bool, Message]:
        return True, msg("instinct.heuristic_ready")

    def _receipt(self, started: float) -> Receipt:
        return Receipt(self.name, int((time.perf_counter() - started) * 1000), 0.0)

    def choose(
        self, question: str, options: Sequence[str], context: Context
    ) -> tuple[Choice, Receipt]:
        started = time.perf_counter()
        kind = context.get("kind")
        if kind == "scope":
            choice = heuristic_scope(context)
        elif kind == "triage":
            choice = heuristic_triage(context)
        elif kind == "route_tier":
            choice = heuristic_role_tier(context, options)
        elif kind == "intake_type":
            choice = heuristic_intake_type(context, options)
        elif kind == "depth":
            choice = heuristic_depth(context)
        else:
            choice = uniform_choice(options)
        if choice.option not in options:
            choice = uniform_choice(options)
        return choice, self._receipt(started)

    def score(
        self, question: str, low: float, high: float, context: Context
    ) -> tuple[Score, Receipt]:
        started = time.perf_counter()
        if context.get("kind") == "risk":
            return Score(heuristic_risk(context, low, high), 0.6), self._receipt(started)
        given = context.get("heuristic_score")
        if isinstance(given, int | float) and not isinstance(given, bool):
            return Score(min(max(float(given), low), high), 0.6), self._receipt(started)
        return Score((low + high) / 2, 0.2), self._receipt(started)

    def noul(self, question: str, context: Context) -> tuple[Noul, Receipt]:
        started = time.perf_counter()
        value = context.get("prior")
        p_yes = (
            float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.5
        )
        return Noul(min(max(p_yes, 0.0), 1.0)), self._receipt(started)
