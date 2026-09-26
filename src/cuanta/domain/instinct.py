from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

SCOPES = ("trivial", "normal", "complex")
TRIAGE = ("caused by this run", "pre-existing", "flaky", "environment")


class Primitive(StrEnum):
    CHOOSE = "choose"
    SCORE = "score"
    NOUL = "noul"


@dataclass(frozen=True, slots=True)
class Choice:
    option: str
    probability: float


@dataclass(frozen=True, slots=True)
class Score:
    value: float
    confidence: float
    legend: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Noul:
    p_yes: float


@dataclass(frozen=True, slots=True)
class Receipt:
    backend: str
    latency_ms: int
    cost_usd: float | None


Context = Mapping[str, object]
Answer = Choice | Score | Noul


@dataclass(frozen=True, slots=True)
class Ask:
    key: str
    primitive: Primitive
    question: str
    options: tuple[str, ...] = ()
    low: float = 0.0
    high: float = 1.0
    hints: Mapping[str, object] = field(default_factory=dict)


def merged(context: Context, ask: Ask) -> dict[str, object]:
    combined = dict(context)
    combined.update(ask.hints)
    return combined


def answer_text(answer: Answer) -> tuple[str, float]:
    if isinstance(answer, Choice):
        return answer.option, answer.probability
    if isinstance(answer, Score):
        return f"{answer.value:.3f}", answer.confidence
    return f"{answer.p_yes:.3f}", answer.p_yes


def split_receipt(receipt: Receipt, parts: int) -> Receipt:
    share = receipt.cost_usd / parts if parts and receipt.cost_usd is not None else receipt.cost_usd
    return Receipt(receipt.backend, receipt.latency_ms, share)


def _text(context: Context, key: str) -> str:
    value = context.get(key)
    return value if isinstance(value, str) else ""


def _int(context: Context, key: str) -> int:
    value = context.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _optional_float(context: Context, key: str) -> float | None:
    value = context.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


LOW_CLARITY = 0.6
SHORT_REQUEST = 80


COMPLEX_WORDS = (
    "migrate",
    "redesign",
    "architecture",
    "across",
    "every",
    "contract",
    "subsystem",
    "rewrite",
    "new module",
    "unclear",
)
TRIVIAL_WORDS = ("typo", "rename", "comment", "wording", "one-line", "one line", "bump")


def heuristic_scope(context: Context) -> Choice:
    what = _text(context, "what").lower()
    kind = _text(context, "type").lower()
    where = _text(context, "where").lower()
    signatures = _int(context, "signatures")
    clarity = _optional_float(context, "clarity")
    breadth = sum(2 for word in COMPLEX_WORDS if word in what)
    breadth += 1 if len(what) > 240 else 0
    breadth += min(signatures - 1, 3) if signatures > 1 else 0
    breadth += 1 if "," in where or " and " in where else 0
    points = breadth - sum(2 for word in TRIVIAL_WORDS if word in what)
    points += 1 if kind == "refactor" else 0
    if kind == "investigation" and breadth < 3:
        option = "trivial" if len(what) < SHORT_REQUEST and not where.strip() else "normal"
        return Choice(option, 0.65)
    if points <= -1:
        return Choice("trivial", min(0.55 + 0.1 * -points, 0.9))
    if points >= 3 and (clarity is None or clarity >= LOW_CLARITY):
        return Choice("complex", min(0.55 + 0.08 * points, 0.9))
    return Choice("normal", 0.6)


def heuristic_triage(context: Context) -> Choice:
    history = _int(context, "seen_before")
    flips = _int(context, "flips")
    error = _text(context, "error").lower()
    environment_markers = (
        "connection refused",
        "timeout",
        "permission denied",
        "no such file",
        "not found",
        "econnrefused",
        "network",
        "dns",
    )
    if any(marker in error for marker in environment_markers):
        return Choice("environment", 0.6)
    if flips >= 2:
        return Choice("flaky", 0.65)
    if history > 0:
        return Choice("pre-existing", 0.7)
    return Choice("caused by this run", 0.6)


def uniform_choice(options: Sequence[str]) -> Choice:
    if not options:
        raise ValueError("choose needs at least one option")
    return Choice(options[0], 1.0 / len(options))


def scope_hint_line(choice: Choice, backend: str) -> str:
    return f"SCOPE HINT (cuanta instinct · {backend}): {choice.option} · p={choice.probability:.2f}"


TIERS = ("economy", "standard", "premium", "frontier")
RAISE_ON_COMPLEX = frozenset({"analyst", "senior", "docs"})
SCOPE_ONLY_CONFIDENCE = 0.55
LOWER_ON_TRIVIAL = frozenset({"orchestrator", "analyst", "senior", "tester"})
WIDE_BLAST_RADIUS = 20


def _step(tier: str, delta: int, options: Sequence[str]) -> str:
    ranked = [name for name in TIERS if name in options]
    if tier not in ranked:
        return ranked[0] if ranked else tier
    index = min(max(ranked.index(tier) + delta, 0), len(ranked) - 1)
    return ranked[index]


def heuristic_role_tier(context: Context, options: Sequence[str]) -> Choice:
    role = _text(context, "role")
    default = _text(context, "default") or "standard"
    scope = _text(context, "scope")
    radius = _int(context, "blast_radius")
    tests = _text(context, "tests")
    delta = 0
    if scope == "complex" and role in RAISE_ON_COMPLEX:
        delta += 1
    if scope == "trivial" and role in LOWER_ON_TRIVIAL:
        delta -= 1
    evidence = radius > WIDE_BLAST_RADIUS
    if evidence and role == "analyst":
        delta += 1
    if tests == "red" and role == "tester" and delta < 0:
        delta = 0
    tier = _step(default, delta, options)
    if delta == 0:
        return Choice(tier, 0.75)
    return Choice(tier, 0.7 if evidence else SCOPE_ONLY_CONFIDENCE)


def heuristic_risk(context: Context, low: float, high: float) -> float:
    radius = _int(context, "blast_radius")
    points = 0.0
    points += 0.5 if radius > 10 else 0.0
    points += 0.5 if radius > 30 else 0.0
    points += 0.5 if _text(context, "tests") == "red" else 0.0
    points += 0.5 if _text(context, "type") in {"refactor", "feature"} else 0.0
    points += 0.5 if _text(context, "scope") == "complex" else 0.0
    return min(max(low + points, low), high)


def heuristic_intake_type(context: Context, options: Sequence[str]) -> Choice:
    guess = _text(context, "guess")
    probability = _optional_float(context, "guess_p")
    if guess in options:
        return Choice(guess, probability if probability is not None else 0.5)
    return uniform_choice(options)


def heuristic_depth(context: Context) -> Choice:
    scope = heuristic_scope(context)
    option = {"trivial": "quick", "complex": "deep"}.get(scope.option, "normal")
    return Choice(option, scope.probability)
