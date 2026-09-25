from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from cuanta.adapters.system.prices import load_prices
from cuanta.adapters.system.shell import detect_shell
from cuanta.domain.errors import EnvironmentFailure, NotAvailable
from cuanta.domain.instinct import (
    Answer,
    Ask,
    Choice,
    Context,
    Noul,
    Primitive,
    Receipt,
    Score,
)
from cuanta.domain.messages import Message, msg
from cuanta.domain.pricing import PER_MILLION
from cuanta.domain.shells import env_hint

DEFAULT_BASE = "https://api.typesafe.ai"
BASE_ENVS = ("TYPESAFE_BASE_URL", "TYPESAFE_API_BASE")
OPENROUTER_HOST = "openrouter.ai"
QUESTION_PATH = "/v1/systemone"
PING_QUESTION = "Is this a connection check?"
MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"
TIMEOUT_S = 20.0
MIN_LEVELS = 2
MAX_LEVELS = 10


def score_levels(low: float, high: float) -> tuple[float, ...]:
    span = high - low
    count = int(span) + 1 if span == int(span) else MAX_LEVELS
    count = max(MIN_LEVELS, min(MAX_LEVELS, count))
    step = span / (count - 1)
    return tuple(low + index * step for index in range(count))


def spend(data: Mapping[str, Any], direct: bool) -> float:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return 0.0
    reported = usage.get("cost")
    if isinstance(reported, int | float) and not isinstance(reported, bool):
        return float(reported)
    if not direct:
        return 0.0
    model = str(data.get("model") or MODEL)
    prices = load_prices()
    price = prices.lookup(model) or prices.lookup(MODEL)
    if price is None:
        return 0.0
    tokens_in = usage.get("input_tokens")
    tokens_out = usage.get("output_tokens")
    total = (tokens_in if isinstance(tokens_in, int) else 0) * price.input
    total += (tokens_out if isinstance(tokens_out, int) else 0) * price.output
    return total / PER_MILLION


class JevInstinct:
    def __init__(self, *_: object, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    @property
    def name(self) -> str:
        return "jev"

    @property
    def remote(self) -> bool:
        return True

    def _key(self) -> str:
        return os.environ.get(KEY_ENV, "").strip()

    @property
    def base(self) -> str:
        chosen = next((os.environ[name].strip() for name in BASE_ENVS if os.environ.get(name)), "")
        return (chosen or DEFAULT_BASE).rstrip("/")

    @property
    def direct(self) -> bool:
        return OPENROUTER_HOST not in self.base

    def available(self) -> tuple[bool, Message]:
        if not self._key():
            return False, msg("instinct.key_missing", env=KEY_ENV)
        return True, msg("instinct.endpoint", endpoint=self.base + QUESTION_PATH, model=MODEL)

    def setup_warning(self) -> Message | None:
        key = self._key()
        if OPENROUTER_HOST in self.base and key.startswith("apikey_"):
            return msg("instinct.key_openrouter_mismatch")
        if "typesafe.ai" in self.base and key.startswith("sk-or-"):
            return msg("instinct.key_typesafe_mismatch")
        return None

    def _require_key(self) -> str:
        key = self._key()
        if not key:
            raise NotAvailable(
                f"jev needs {KEY_ENV}",
                f"{env_hint(KEY_ENV, '<your key>', detect_shell())}, "
                "or run: cuanta instinct use heuristic",
            )
        return key

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def ping(self) -> tuple[str, str, int]:
        data, _, receipt = self._post({"type": "noul", "instructions": PING_QUESTION}, {})
        provider = str(data.get("provider") or ("TypeSafe" if self.direct else "OpenRouter"))
        return str(data.get("model") or MODEL), provider, receipt.latency_ms

    def connection(self) -> Message:
        model, provider, latency = self.ping()
        return msg("instinct.connected", model=model, provider=provider, latency=latency)

    def _ask(self, question: Mapping[str, Any], context: Context) -> tuple[dict[str, Any], Receipt]:
        _, answer, receipt = self._post(question, context)
        return answer, receipt

    def _post(
        self, question: Mapping[str, Any], context: Context
    ) -> tuple[dict[str, Any], dict[str, Any], Receipt]:
        data, answers, receipt = self._post_many({"q": question}, context)
        return data, answers["q"], receipt

    def _post_many(
        self, questions: Mapping[str, Mapping[str, Any]], context: Context
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], Receipt]:
        key = self._require_key()
        body = {
            "state": json.dumps(dict(context), default=str),
            "model": MODEL,
            "questions": {name: dict(question) for name, question in questions.items()},
        }
        started = time.perf_counter()
        try:
            with httpx.Client(transport=self._transport, timeout=TIMEOUT_S) as client:
                response = client.post(
                    self.base + QUESTION_PATH, json=body, headers=self._headers(key)
                )
        except httpx.HTTPError as error:
            raise EnvironmentFailure(f"jev unreachable: {error}") from error
        latency = int((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            raise EnvironmentFailure(f"jev answered HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as error:
            raise EnvironmentFailure("jev returned invalid JSON") from error
        answers = data.get("answers") if isinstance(data, dict) else None
        found: dict[str, dict[str, Any]] = {}
        for name in questions:
            answer = answers.get(name) if isinstance(answers, dict) else None
            if not isinstance(answer, dict):
                raise EnvironmentFailure(f"jev response had no answer for question {name}")
            found[name] = answer
        return data, found, Receipt(self.name, latency, spend(data, self.direct))

    def ask_many(self, asks: Sequence[Ask], context: Context) -> tuple[dict[str, Answer], Receipt]:
        questions = {ask.key: question_payload(ask) for ask in asks}
        _, answers, receipt = self._post_many(questions, context)
        parsed = {ask.key: parse_answer(ask, answers[ask.key]) for ask in asks}
        return parsed, receipt

    def choose(
        self, question: str, options: Sequence[str], context: Context
    ) -> tuple[Choice, Receipt]:
        ask = Ask("q", Primitive.CHOOSE, question, tuple(options))
        answer, receipt = self._ask(question_payload(ask), context)
        return parse_choice(ask, answer), receipt

    def score(
        self, question: str, low: float, high: float, context: Context
    ) -> tuple[Score, Receipt]:
        ask = Ask("q", Primitive.SCORE, question, low=low, high=high)
        answer, receipt = self._ask(question_payload(ask), context)
        return parse_score(ask, answer), receipt

    def noul(self, question: str, context: Context) -> tuple[Noul, Receipt]:
        ask = Ask("q", Primitive.NOUL, question)
        answer, receipt = self._ask(question_payload(ask), context)
        return parse_noul(answer), receipt


def question_payload(ask: Ask) -> dict[str, Any]:
    if ask.primitive is Primitive.CHOOSE:
        return {
            "type": "choice",
            "instructions": ask.question,
            "criteria": {option: option for option in ask.options},
        }
    if ask.primitive is Primitive.SCORE:
        levels = score_levels(ask.low, ask.high)
        criteria = [f"{level:g} on a scale from {ask.low:g} to {ask.high:g}" for level in levels]
        return {"type": "score", "instructions": ask.question, "criteria": criteria}
    return {"type": "noul", "instructions": ask.question}


def parse_choice(ask: Ask, answer: Mapping[str, Any]) -> Choice:
    option = answer.get("choice")
    if not isinstance(option, str) or option not in ask.options:
        raise EnvironmentFailure(f"jev chose an unknown option: {option}")
    probabilities = answer.get("probabilities")
    probability = answer.get("confidence")
    if isinstance(probabilities, dict) and isinstance(probabilities.get(option), int | float):
        probability = probabilities[option]
    value = float(probability) if isinstance(probability, int | float) else 0.0
    return Choice(option, value)


def parse_score(ask: Ask, answer: Mapping[str, Any]) -> Score:
    levels = score_levels(ask.low, ask.high)
    raw = answer.get("score")
    if not isinstance(raw, int | float):
        raise EnvironmentFailure("jev score answer missing")
    step = (ask.high - ask.low) / (len(levels) - 1)
    value = min(ask.high, max(ask.low, ask.low + float(raw) * step))
    confidence = answer.get("confidence")
    legend = answer.get("legend")
    described = (
        tuple(str(legend[key]) for key in sorted(legend, key=int) if str(key).isdigit())
        if isinstance(legend, dict)
        else ()
    )
    certainty = float(confidence) if isinstance(confidence, int | float) else 0.0
    return Score(value, certainty, described)


def parse_noul(answer: Mapping[str, Any]) -> Noul:
    value = answer.get("noul")
    if not isinstance(value, int | float):
        raise EnvironmentFailure("jev noul answer missing")
    return Noul(float(value))


def parse_answer(ask: Ask, answer: Mapping[str, Any]) -> Answer:
    if ask.primitive is Primitive.CHOOSE:
        return parse_choice(ask, answer)
    if ask.primitive is Primitive.SCORE:
        return parse_score(ask, answer)
    return parse_noul(answer)
