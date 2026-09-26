from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from cuanta.domain.engine import EngineRequest, RunResult
from cuanta.domain.errors import EnvironmentFailure, NotAvailable
from cuanta.domain.instinct import Choice, Context, Noul, Receipt, Score
from cuanta.domain.messages import Message, msg
from cuanta.ports.engine import Engine

MODEL = "haiku"
BUDGET_USD = 0.05


def _extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise EnvironmentFailure("llm answer had no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as error:
        raise EnvironmentFailure("llm answer was not valid JSON") from error
    if not isinstance(data, dict):
        raise EnvironmentFailure("llm answer was not a JSON object")
    return data


class LlmInstinct:
    def __init__(self, engine: Engine | None, cwd: str) -> None:
        self._engine = engine
        self._cwd = cwd

    @property
    def name(self) -> str:
        return "llm"

    @property
    def remote(self) -> bool:
        return True

    def available(self) -> tuple[bool, Message]:
        if self._engine is None or not self._engine.available():
            return False, msg("instinct.claude_missing")
        return True, msg("instinct.llm_ready", model=MODEL)

    def _ask(self, instruction: str, context: Context) -> tuple[dict[str, Any], Receipt]:
        if self._engine is None or not self._engine.available():
            raise NotAvailable("llm instinct needs claude", "cuanta instinct use heuristic")
        prompt = (
            f"{instruction}\nContext (JSON): {json.dumps(dict(context), default=str)}\n"
            "Answer with one JSON object only. No prose."
        )
        started = time.perf_counter()
        results: list[RunResult] = []
        outcome = self._engine.run(
            EngineRequest(
                prompt=prompt, cwd=self._cwd, env={}, model=MODEL, max_budget_usd=BUDGET_USD
            ),
            lambda event: results.append(event) if isinstance(event, RunResult) else None,
        )
        latency = int((time.perf_counter() - started) * 1000)
        if outcome.result is None or not outcome.ok:
            raise EnvironmentFailure("llm instinct run failed")
        return _extract_json(outcome.result.text), Receipt(self.name, latency, outcome.cost_usd)

    def choose(
        self, question: str, options: Sequence[str], context: Context
    ) -> tuple[Choice, Receipt]:
        data, receipt = self._ask(
            f"{question}\nPick exactly one of {list(options)}. "
            'Shape: {"option": str, "probability": 0..1}',
            context,
        )
        option = data.get("option")
        probability = data.get("probability")
        if not isinstance(option, str) or option not in options:
            raise EnvironmentFailure(f"llm chose an unknown option: {option}")
        value = float(probability) if isinstance(probability, int | float) else 0.5
        return Choice(option, min(max(value, 0.0), 1.0)), receipt

    def score(
        self, question: str, low: float, high: float, context: Context
    ) -> tuple[Score, Receipt]:
        data, receipt = self._ask(
            f"{question}\nScore between {low} and {high}. "
            'Shape: {"value": number, "confidence": 0..1}',
            context,
        )
        value, confidence = data.get("value"), data.get("confidence")
        if not isinstance(value, int | float):
            raise EnvironmentFailure("llm score missing")
        bounded = min(max(float(value), low), high)
        certainty = float(confidence) if isinstance(confidence, int | float) else 0.5
        return Score(bounded, certainty), receipt

    def noul(self, question: str, context: Context) -> tuple[Noul, Receipt]:
        data, receipt = self._ask(f'{question}\nShape: {{"p_yes": 0..1}}', context)
        value = data.get("p_yes")
        if not isinstance(value, int | float):
            raise EnvironmentFailure("llm noul missing")
        return Noul(min(max(float(value), 0.0), 1.0)), receipt
