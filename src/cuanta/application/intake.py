from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from cuanta.application.instinct import DecisionMaker
from cuanta.domain.depth import DEPTHS
from cuanta.domain.instinct import SCOPES, Answer, Ask, Choice, Noul, Primitive, Score
from cuanta.domain.intake import (
    KINDS,
    IntakeFacts,
    depth_for_scope,
    extract,
    gap_keys,
    gap_prior,
    heuristic_type,
)
from cuanta.domain.mandate import INVESTIGATION, MandateRequest, deliverable_line
from cuanta.domain.messages import english, msg

STORY_LIMIT = 1_200
RISK_LOW = 0.0
RISK_HIGH = 2.0
READ_ONLY_THRESHOLD = 0.5
GAP_THRESHOLD = 0.5
KIND = "kind"
READ_ONLY = "read_only"
SCOPE = "scope"
RISK = "risk"
DEPTH = "depth"


@dataclass(frozen=True, slots=True)
class Understanding:
    story: str
    facts: IntakeFacts
    kind: Choice
    read_only: float
    scope: Choice
    risk: float
    depth: Choice
    gaps: tuple[tuple[str, tuple[str, ...]], ...]
    places: tuple[str, ...]
    backend: str
    cost_usd: float
    needs_confirm: bool
    fallback_error: str = ""
    fallback_from: str = ""
    intake_scope: str = ""

    @property
    def missing(self) -> tuple[str, ...]:
        return self.missing_for(self.kind.option)

    def missing_for(self, kind: str) -> tuple[str, ...]:
        return dict(self.gaps).get(kind, ())

    def answered(self, kind: str, gap: str) -> Understanding:
        gaps = tuple(
            (name, tuple(item for item in items if name != kind or item != gap))
            for name, items in self.gaps
        )
        return replace(self, gaps=gaps)

    @property
    def is_read_only(self) -> bool:
        return self.read_only >= READ_ONLY_THRESHOLD

    def request(self, kind: str = "") -> MandateRequest:
        chosen = kind or self.kind.option
        facts = self.facts
        lines = [f"- {question}" for question in facts.questions]
        if chosen == INVESTIGATION and not lines and self.story:
            lines.append(f"- {self.story}")
        lines.extend(facts.errors)
        out = "; ".join(facts.out_of_scope)
        if not out and self.is_read_only:
            out = "read-only: change nothing"
        where = ", ".join(mention for mention in facts.mentions if "/" in mention or "." in mention)
        return MandateRequest(
            type=chosen,
            what=facts.core,
            why="\n".join(lines),
            where=where,
            tests=deliverable_line("report") if chosen == INVESTIGATION else "",
            out_of_scope=out,
        )


def intake_asks(story: str, facts: IntakeFacts, guess: Choice) -> tuple[Ask, ...]:
    read_prior = 0.95 if facts.read_only else (0.8 if guess.option == INVESTIGATION else 0.1)
    asks: list[Ask] = [
        Ask(
            KIND,
            Primitive.CHOOSE,
            english(msg("question.intake_type")),
            KINDS,
            hints={"kind": "intake_type", "guess": guess.option, "guess_p": guess.probability},
        ),
        Ask(
            READ_ONLY,
            Primitive.NOUL,
            english(msg("question.intake_read_only")),
            hints={"prior": read_prior},
        ),
        Ask(
            SCOPE,
            Primitive.CHOOSE,
            english(msg("question.mandate_scope")),
            SCOPES,
            hints={"kind": "scope", "type": guess.option},
        ),
        Ask(
            RISK,
            Primitive.SCORE,
            english(msg("question.route_risk")),
            low=RISK_LOW,
            high=RISK_HIGH,
            hints={"kind": "risk", "type": guess.option},
        ),
        Ask(
            DEPTH,
            Primitive.CHOOSE,
            english(msg("question.intake_depth")),
            tuple(depth.value for depth in DEPTHS),
            hints={"kind": "depth", "type": guess.option},
        ),
    ]
    for kind in KINDS:
        for gap in gap_keys(kind):
            asks.append(
                Ask(
                    f"gap:{kind}:{gap}",
                    Primitive.NOUL,
                    english(msg(f"question.intake_gap_{gap}")),
                    hints={"prior": gap_prior(gap, story, facts)},
                )
            )
    return tuple(asks)


def _choice(answers: Mapping[str, Answer], key: str, fallback: Choice) -> Choice:
    value = answers.get(key)
    return value if isinstance(value, Choice) else fallback


def _noul(answers: Mapping[str, Answer], key: str, fallback: float) -> float:
    value = answers.get(key)
    return value.p_yes if isinstance(value, Noul) else fallback


def _score(answers: Mapping[str, Answer], key: str, fallback: float) -> float:
    value = answers.get(key)
    return value.value if isinstance(value, Score) else fallback


def missing_gaps(answers: Mapping[str, Answer], kind: str) -> tuple[str, ...]:
    return tuple(
        gap for gap in gap_keys(kind) if _noul(answers, f"gap:{kind}:{gap}", 1.0) < GAP_THRESHOLD
    )


def all_gaps(answers: Mapping[str, Answer]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple((kind, missing_gaps(answers, kind)) for kind in KINDS)


class IntakeService:
    def __init__(
        self,
        decisions: DecisionMaker,
        min_confidence: float,
        places: Callable[[IntakeFacts, MandateRequest], Sequence[str]],
    ) -> None:
        self._decisions = decisions
        self._min_confidence = min_confidence
        self._places = places

    def understand(self, story: str) -> Understanding:
        text = story.strip()
        facts = extract(text)
        guess = heuristic_type(text, facts)
        state: dict[str, object] = {
            "story": text[:STORY_LIMIT],
            "questions": list(facts.questions),
            "errors": len(facts.errors),
            "mentions": list(facts.mentions),
            "out_of_scope": list(facts.out_of_scope),
            "what": facts.core[:STORY_LIMIT],
        }
        answers, receipt = self._decisions.ask_many(intake_asks(text, facts, guess), state)
        fallback_error = self._decisions.last_fallback_error
        kind = _choice(answers, KIND, guess)
        if kind.option not in KINDS:
            kind = guess
        scope = _choice(answers, SCOPE, Choice("normal", 0.5))
        depth = _choice(answers, DEPTH, Choice(depth_for_scope(scope.option), scope.probability))
        draft = Understanding(
            story=text,
            facts=facts,
            kind=kind,
            read_only=_noul(answers, READ_ONLY, 1.0 if facts.read_only else 0.0),
            scope=scope,
            risk=_score(answers, RISK, 0.0),
            depth=depth,
            gaps=all_gaps(answers),
            places=(),
            backend=receipt.backend,
            cost_usd=receipt.cost_usd,
            needs_confirm=kind.probability < self._min_confidence,
            fallback_error=fallback_error,
            fallback_from=self._decisions.configured_backend.name if fallback_error else "",
        )
        return replace(draft, places=tuple(self._places(facts, draft.request())))
