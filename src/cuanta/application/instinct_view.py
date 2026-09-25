from __future__ import annotations

import json
from dataclasses import dataclass

from cuanta.application.instinct import (
    TOOL_OUTPUT_KEYS,
    DecisionMaker,
    probe_questions,
    redact_context,
)
from cuanta.domain.instinct import Primitive
from cuanta.domain.ledger import Decision
from cuanta.domain.messages import Message, keyed, msg, option_message, question_message

BACKENDS = ("heuristic", "jev", "llm")
PROBE_RUN = "probe"


@dataclass(frozen=True, slots=True)
class BackendStatus:
    name: str
    available: bool
    detail: Message
    remote: bool
    consented: bool
    current: bool


@dataclass(frozen=True, slots=True)
class ProbeRow:
    primitive: str
    question: str
    answer: Message
    latency_ms: int
    cost_usd: float
    backend: str


def probe(maker: DecisionMaker) -> list[ProbeRow]:
    rows: list[ProbeRow] = []
    for primitive, question, context, options in probe_questions():
        if primitive is Primitive.CHOOSE:
            choice, decided = maker.choose(question, options, context, run_id=PROBE_RUN)
            option = option_message(choice.option)
            answer = msg("probe.choice", option=option, p=f"{choice.probability:.2f}")
        elif primitive is Primitive.NOUL:
            noul, decided = maker.noul(question, context, run_id=PROBE_RUN)
            answer = msg("probe.noul", p=f"{noul.p_yes:.2f}")
        else:
            score, decided = maker.score(question, 0, 10, context, run_id=PROBE_RUN)
            answer = msg(
                "probe.score", value=f"{score.value:.1f}", confidence=f"{score.confidence:.2f}"
            )
        receipt = decided.receipt
        rows.append(
            ProbeRow(
                primitive.value,
                question,
                answer,
                receipt.latency_ms,
                receipt.cost_usd,
                receipt.backend,
            )
        )
    return rows


SENTENCE_SUBJECTS = {
    "question.mandate_scope": "sentence.scope",
    "question.change_scope": "sentence.scope",
    "question.route_risk": "sentence.risk",
    "question.triage": "sentence.triage",
    "question.network": "sentence.network",
    "question.rename_risk": "sentence.risk",
}
WEEK_DAYS = 7


@dataclass(frozen=True, slots=True)
class JevCard:
    key_present: bool
    endpoint: str
    model: str
    latency_ms: int | None
    spend_week: float
    decisions_week: int
    status: Message | None = None
    ok: bool | None = None


def sentence(decision: Decision) -> Message:
    parsed = question_message(decision.question)
    if parsed is not None and parsed.key == "question.route_tier":
        role = parsed.values(lambda message: message.key).get("role", "")
        subject = msg("sentence.tier", role=keyed("role", role))
    elif parsed is not None and parsed.key in SENTENCE_SUBJECTS:
        subject = msg(SENTENCE_SUBJECTS[parsed.key])
    else:
        subject = msg("sentence.other", question=decision.question[:60])
    answer = option_message(decision.answer)
    if decision.primitive == Primitive.SCORE.value:
        answer = decision.answer
    return msg(
        "sentence.decision",
        subject=subject,
        answer=answer,
        confidence=f"{decision.confidence:.0%}",
    )


def week_spend(decisions: tuple[Decision, ...], backend: str, since: str) -> tuple[float, int]:
    recent = [item for item in decisions if item.backend == backend and item.created_at >= since]
    return sum(item.cost_usd for item in recent), len(recent)


def preview_state(what: str, where: str, task_type: str) -> str:
    state: dict[str, object] = {
        "kind": "scope",
        "type": task_type,
        "what": what,
        "where": where,
        "summary": what[:200],
        "blast_radius": 0,
        "tests": "unknown",
    }
    return json.dumps(redact_context(state, TOOL_OUTPUT_KEYS), indent=2, ensure_ascii=False)
