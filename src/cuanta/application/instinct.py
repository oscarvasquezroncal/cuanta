from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.costs import sum_costs
from cuanta.domain.instinct import (
    TRIAGE,
    Answer,
    Ask,
    Choice,
    Context,
    Noul,
    Primitive,
    Receipt,
    Score,
    answer_text,
    merged,
    split_receipt,
)
from cuanta.domain.ledger import Decision
from cuanta.domain.messages import english, msg
from cuanta.domain.redaction import redact_for_remote
from cuanta.domain.testing import Signature
from cuanta.ports.instinct import BatchInstinct, Instinct
from cuanta.ports.ledger import Ledger


def redact_context(
    context: Context, tool_output_keys: frozenset[str] = frozenset()
) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key, value in context.items():
        if isinstance(value, str):
            if key in tool_output_keys:
                cleaned[key] = f"ok, {len(value)} chars (omitted)"
            else:
                cleaned[key] = redact_for_remote(value)
        elif isinstance(value, int | float | bool) or value is None:
            cleaned[key] = value
        elif isinstance(value, list | tuple):
            cleaned[key] = [redact_for_remote(str(item)) for item in value]
        else:
            cleaned[key] = redact_for_remote(str(value))
    return cleaned


TOOL_OUTPUT_KEYS = frozenset({"output", "log", "tool_output", "stdout", "stderr"})


@dataclass
class DecisionScope:
    run_id: str = ""
    request_hash: str = ""
    preview: bool = False

    def set(self, run_id: str, request_hash: str, preview: bool) -> None:
        self.run_id = run_id
        self.request_hash = request_hash
        self.preview = preview


@dataclass(frozen=True, slots=True)
class Decided:
    decision_id: int
    receipt: Receipt


class DecisionMaker:
    def __init__(
        self,
        backend: Instinct,
        ledger: Ledger,
        clock_iso: Callable[[], str],
        fallback: Instinct | None = None,
        scope: DecisionScope | None = None,
        disabled_reason: str = "",
    ) -> None:
        self._scope = scope or DecisionScope()
        self._backend = backend
        self._ledger = ledger
        self._clock_iso = clock_iso
        self._fallback = fallback
        self._disabled_reason = disabled_reason
        self.last_fallback_error = ""

    @property
    def backend(self) -> Instinct:
        if self._disabled_reason and self._fallback is not None:
            return self._fallback
        return self._backend

    @property
    def configured_backend(self) -> Instinct:
        return self._backend

    def _context(self, context: Context) -> Context:
        if self._backend.remote and not self._disabled_reason:
            return redact_context(context, TOOL_OUTPUT_KEYS)
        return context

    def _log(
        self,
        run_id: str,
        primitive: Primitive,
        question: str,
        options: Sequence[str],
        answer: str,
        confidence: float,
        receipt: Receipt,
        fallback_error: str = "",
    ) -> int:
        scope = self._scope
        preview = scope.preview and not run_id
        if preview and scope.request_hash and not fallback_error:
            existing = self._ledger.preview_decision(scope.request_hash, question)
            if existing is not None:
                return existing
        return self._ledger.add_decision(
            Decision(
                run_id=run_id or scope.run_id,
                preview=preview,
                request_hash=scope.request_hash,
                backend=receipt.backend,
                primitive=primitive.value,
                question=question,
                options=json.dumps(list(options)),
                answer=answer,
                confidence=confidence,
                latency_ms=receipt.latency_ms,
                cost_usd=receipt.cost_usd,
                created_at=self._clock_iso(),
                fallback_error=fallback_error,
                fallback_from=self._backend.name if fallback_error else "",
            )
        )

    def _call[T](self, action: Callable[[Instinct], T]) -> tuple[T, str]:
        if self._disabled_reason and self._fallback is not None:
            return action(self._fallback), self._disabled_reason
        try:
            return action(self._backend), ""
        except Exception as error:
            if self._fallback is None:
                raise
            return action(self._fallback), str(error).strip() or type(error).__name__

    def choose(
        self, question: str, options: Sequence[str], context: Context, run_id: str = ""
    ) -> tuple[Choice, Decided]:
        prepared = self._context(context)
        (choice, receipt), fallback_error = self._call(
            lambda backend: backend.choose(question, options, prepared)
        )
        self.last_fallback_error = fallback_error
        identifier = self._log(
            run_id,
            Primitive.CHOOSE,
            question,
            options,
            choice.option,
            choice.probability,
            receipt,
            fallback_error,
        )
        return choice, Decided(identifier, receipt)

    def score(
        self, question: str, low: float, high: float, context: Context, run_id: str = ""
    ) -> tuple[Score, Decided]:
        prepared = self._context(context)
        (score, receipt), fallback_error = self._call(
            lambda backend: backend.score(question, low, high, prepared)
        )
        self.last_fallback_error = fallback_error
        identifier = self._log(
            run_id,
            Primitive.SCORE,
            question,
            [str(low), str(high)],
            f"{score.value:.3f}",
            score.confidence,
            receipt,
            fallback_error,
        )
        return score, Decided(identifier, receipt)

    def noul(self, question: str, context: Context, run_id: str = "") -> tuple[Noul, Decided]:
        prepared = self._context(context)
        (answer, receipt), fallback_error = self._call(
            lambda backend: backend.noul(question, prepared)
        )
        self.last_fallback_error = fallback_error
        identifier = self._log(
            run_id,
            Primitive.NOUL,
            question,
            ["yes", "no"],
            f"{answer.p_yes:.3f}",
            answer.p_yes,
            receipt,
            fallback_error,
        )
        return answer, Decided(identifier, receipt)

    def ask_many(
        self, asks: Sequence[Ask], context: Context, run_id: str = ""
    ) -> tuple[dict[str, Answer], Receipt]:
        prepared = self._context(context)
        (answers, receipt), fallback_error = self._call(
            lambda backend: self._batch(backend, asks, prepared)
        )
        self.last_fallback_error = fallback_error
        share = split_receipt(receipt, len(asks))
        for ask in asks:
            text, confidence = answer_text(answers[ask.key])
            options = ask.options or (
                ("yes", "no") if ask.primitive is Primitive.NOUL else (str(ask.low), str(ask.high))
            )
            self._log(
                run_id,
                ask.primitive,
                ask.question,
                options,
                text,
                confidence,
                share,
                fallback_error,
            )
        return answers, receipt

    def _batch(
        self, backend: Instinct, asks: Sequence[Ask], context: Context
    ) -> tuple[dict[str, Answer], Receipt]:
        if isinstance(backend, BatchInstinct):
            return backend.ask_many(asks, context)
        answers: dict[str, Answer] = {}
        latency = 0
        cost: float | None = 0.0
        for ask in asks:
            answer, receipt = single_answer(backend, ask, merged(context, ask))
            answers[ask.key] = answer
            latency += receipt.latency_ms
            cost = sum_costs((cost, receipt.cost_usd))
        return answers, Receipt(backend.name, latency, cost)

    def record_outcome(self, decision_id: int, outcome: str) -> None:
        self._ledger.set_decision_outcome(decision_id, outcome)


def single_answer(backend: Instinct, ask: Ask, context: Context) -> tuple[Answer, Receipt]:
    if ask.primitive is Primitive.CHOOSE:
        return backend.choose(ask.question, ask.options, context)
    if ask.primitive is Primitive.SCORE:
        return backend.score(ask.question, ask.low, ask.high, context)
    return backend.noul(ask.question, context)


def consent_ok(backend: Instinct, consented: Sequence[str]) -> bool:
    return not backend.remote or backend.name in consented


def probe_questions() -> list[tuple[Primitive, str, Mapping[str, object], Sequence[str]]]:
    return [
        (
            Primitive.CHOOSE,
            english(msg("question.change_scope")),
            {"kind": "scope", "type": "bug", "what": "fix typo in README", "where": "README.md"},
            ("trivial", "normal", "complex"),
        ),
        (
            Primitive.NOUL,
            english(msg("question.network")),
            {"error": "ConnectionError: refused", "prior": 0.7},
            (),
        ),
        (
            Primitive.SCORE,
            english(msg("question.rename_risk", files=14)),
            {"files": 14},
            (),
        ),
    ]


HISTORY_WINDOW = 50


class SignatureTriage:
    def __init__(self, ledger: Ledger, decisions: DecisionMaker) -> None:
        self._ledger = ledger
        self._decisions = decisions

    def features(self, signature_id: str, run_id: str) -> dict[str, object]:
        history = self._ledger.test_runs(limit=HISTORY_WINDOW)
        containing = {record.id for record in self._ledger.signature_history(signature_id)}
        seen_before = sum(
            1
            for record in history
            if record.id in containing and (not run_id or record.run_id != run_id)
        )
        presence = [record.id in containing for record in reversed(history)]
        flips = sum(1 for earlier, later in itertools.pairwise(presence) if earlier and not later)
        return {"seen_before": seen_before, "flips": flips}

    def __call__(self, signature: Signature, run_id: str) -> str:
        context: dict[str, object] = {"kind": "triage", "error": signature.verbatim}
        context.update(self.features(signature.id, run_id))
        choice, _ = self._decisions.choose(
            english(msg("question.triage", signature=signature.id)), TRIAGE, context, run_id=run_id
        )
        return choice.option
