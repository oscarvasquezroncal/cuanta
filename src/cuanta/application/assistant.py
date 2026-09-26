from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from cuanta.application.instinct import TOOL_OUTPUT_KEYS, DecisionMaker, redact_context
from cuanta.domain.assistant import (
    EXAMPLES,
    Clarity,
    Gap,
    Suggestions,
    chips_from,
    content_key,
    do_not_break,
    heuristic_clarity,
    heuristic_gaps,
    nearby_tests,
    where_suggestions,
)
from cuanta.domain.costs import sum_costs
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english, msg

FIELD_LIMIT = 600
CLARITY_LOW = 0.0
CLARITY_HIGH = 2.0
GAP_KEYS: Mapping[Gap, str] = {
    Gap.EVIDENCE: "question.gap_evidence",
    Gap.PLACE: "question.gap_place",
    Gap.EXPECTED: "question.gap_expected",
    Gap.SPLIT: "question.gap_split",
}
IMPROVE_FIELDS = ("what", "why", "where", "tests", "out_of_scope")
IMPROVE_OUTPUT_TOKENS = 400
IMPROVE_BUDGET_USD = 0.10
JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def assistant_state(request: MandateRequest) -> dict[str, object]:
    return {
        "kind": "clarity",
        "type": request.type,
        "what": request.what[:FIELD_LIMIT],
        "evidence": request.why[:FIELD_LIMIT],
        "where": request.where[:FIELD_LIMIT],
        "expected": request.tests[:FIELD_LIMIT],
        "out_of_scope": request.out_of_scope[:FIELD_LIMIT],
    }


def sent_payload(request: MandateRequest, remote: bool = True) -> dict[str, object]:
    state = assistant_state(request)
    return redact_context(state, TOOL_OUTPUT_KEYS) if remote else state


class PromptAssistant:
    def __init__(self, decisions: DecisionMaker) -> None:
        self._decisions = decisions
        self._cache: dict[str, Clarity] = {}

    def preview(self, request: MandateRequest) -> str:
        remote = self._decisions.backend.remote
        return json.dumps(sent_payload(request, remote), indent=2, ensure_ascii=False)

    def check(self, request: MandateRequest) -> Clarity:
        key = content_key(request)
        if key in self._cache:
            return self._cache[key]
        base = assistant_state(request)
        prior = heuristic_gaps(request)
        cost: float | None = 0.0
        score, decided = self._decisions.score(
            english(msg("question.clarity")),
            CLARITY_LOW,
            CLARITY_HIGH,
            {**base, "heuristic_score": heuristic_clarity(request)},
        )
        cost = sum_costs((cost, decided.receipt.cost_usd))
        missing: dict[Gap, float] = {}
        for gap, question in GAP_KEYS.items():
            present_prior = prior[gap] if gap is Gap.SPLIT else 1.0 - prior[gap]
            answer, decided = self._decisions.noul(
                english(msg(question)),
                {**base, "kind": "gap", "gap": gap.value, "prior": present_prior},
            )
            cost = sum_costs((cost, decided.receipt.cost_usd))
            missing[gap] = answer.p_yes if gap is Gap.SPLIT else 1.0 - answer.p_yes
        clarity = Clarity(
            min(max(score.value, CLARITY_LOW), CLARITY_HIGH),
            chips_from(missing),
            decided.receipt.backend,
            cost,
        )
        self._cache[key] = clarity
        return clarity


def suggest(
    request: MandateRequest,
    files: Sequence[str],
    symbols: Mapping[str, str],
    neighbours: Mapping[str, frozenset[str]],
    rulebook: str | None,
) -> Suggestions:
    places = where_suggestions(request, files, symbols)
    named = [part.strip() for part in re.split(r"[,\s]+", request.where) if part.strip()]
    tests = nearby_tests([*named, *places], files, neighbours)
    return Suggestions(places, tests, do_not_break(rulebook), EXAMPLES.get(request.type))


def improve_prompt(request: MandateRequest) -> str:
    fields = {name: getattr(request, name) for name in IMPROVE_FIELDS}
    return (
        "Rewrite this coding request into crisp, concrete fields for a coding agent. "
        "Keep every fact; never invent files, errors or numbers; leave a field empty when "
        "the request does not say it. Reply with one JSON object and nothing else, with the "
        f"keys {', '.join(IMPROVE_FIELDS)}.\n\nRequest:\n"
        + json.dumps(fields, indent=2, ensure_ascii=False)
    )


def parse_improvement(request: MandateRequest, text: str) -> MandateRequest | None:
    match = JSON_BLOCK.search(text)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    updates = {
        name: str(data[name]).strip()
        for name in IMPROVE_FIELDS
        if isinstance(data.get(name), str) and str(data[name]).strip()
    }
    return replace(request, **updates) if updates else None


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: str
    after: str


def changes(before: MandateRequest, after: MandateRequest) -> tuple[FieldChange, ...]:
    return tuple(
        FieldChange(name, getattr(before, name), getattr(after, name))
        for name in IMPROVE_FIELDS
        if getattr(before, name) != getattr(after, name)
    )


@dataclass(frozen=True, slots=True)
class Improvement:
    estimate: float | None
    model: str
    proposal: MandateRequest | None = None
    diff: tuple[FieldChange, ...] = ()
    cost_usd: float | None = None
    ran: bool = False


def improvement_estimate(prompt: str, price: Callable[[int, int], float | None]) -> float | None:
    return price(len(prompt) // 4 + 1, IMPROVE_OUTPUT_TOKENS)
