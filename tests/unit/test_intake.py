from __future__ import annotations

import json

import httpx
import pytest

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.instinct.jev import JevInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.instinct import DecisionMaker
from cuanta.application.intake import IntakeService, intake_asks
from cuanta.domain.instinct import Choice
from cuanta.domain.intake import (
    IntakeFacts,
    extract,
    extract_errors,
    extract_mentions,
    extract_out_of_scope,
    extract_questions,
    heuristic_type,
    match_places,
)
from cuanta.domain.mandate import MandateRequest

STORY = (
    "Quiero entender para qué es esta landing, cómo funciona el carrito y si el hero afecta "
    "el rendimiento. No cambies nada."
)
REAL_STORY = (
    "Quiero una auditoría de esta landing: para qué sirve, cómo funciona el carrito "
    "y el checkout, si el hero de física afecta el rendimiento, y qué riesgos de SEO "
    "o accesibilidad tiene. No cambies nada."
)
TRACE = """The checkout crashes when the cart is empty.
Traceback (most recent call last):
  File "src/shop/cart.py", line 12, in total
KeyError: 'price'
Why does it fail? Don't touch the payments module."""
NOW = "2026-09-24T12:00:00Z"


def places(facts: IntakeFacts, request: MandateRequest) -> tuple[str, ...]:
    return ("src/components/Hero.tsx",)


def test_the_landing_story_is_a_read_only_investigation_with_three_questions() -> None:
    facts = extract(STORY)
    assert facts.questions == (
        "¿Para qué es esta landing?",
        "¿Cómo funciona el carrito?",
        "¿El hero afecta el rendimiento?",
    )
    assert facts.read_only
    assert facts.out_of_scope == ("No cambies nada",)
    assert facts.errors == ()
    assert heuristic_type(STORY, facts).option == "investigation"


def test_the_real_landing_story_yields_four_specific_questions() -> None:
    assert extract_questions(REAL_STORY) == (
        "¿Para qué sirve esta landing?",
        "¿Cómo funciona el carrito y el checkout?",
        "¿El hero de física afecta el rendimiento?",
        "¿Qué riesgos de SEO o accesibilidad tiene?",
    )


def test_english_errors_questions_and_scope_are_extracted() -> None:
    facts = extract(TRACE)
    assert "Traceback (most recent call last):" in facts.errors
    assert "KeyError: 'price'" in facts.errors
    assert 'File "src/shop/cart.py", line 12, in total' in facts.errors
    assert facts.questions == ("Why does it fail?",)
    assert facts.out_of_scope == ("Don't touch the payments module",)
    assert not facts.read_only
    assert heuristic_type(TRACE, facts).option == "bug"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Dónde se calcula el total?\n¿Quién llama a Checkout?", 2),
        ("How does the router pick a page? And why is it slow?", 2),
        ("Explain how the cache works and whether it expires", 2),
        ("Necesito saber por qué falla el build", 1),
        ("Add a CSV export to the orders page.", 0),
    ],
)
def test_questions_in_spanish_and_english(text: str, expected: int) -> None:
    assert len(extract_questions(text)) == expected


@pytest.mark.parametrize(
    ("story", "expected"),
    [
        ("Quiero entender cómo funciona el carrito y qué falla en checkout.", 2),
        ("Necesito saber dónde se carga el hero y por qué tarda.", 2),
        ("Auditar para qué sirve la landing y si carga lento.", 2),
        ("Revisar cómo se calcula el total; cuándo se aplica el impuesto.", 2),
        ("Analizar qué ruta abre el pago y cuáles fallan.", 2),
        ("Investigar si el caché funciona y qué guarda.", 2),
        ("Una auditoría de esta landing: para qué sirve, cómo carga.", 2),
        ("Revisión de checkout: qué falla; dónde se inicia.", 2),
        ("Análisis de la búsqueda: cómo filtra y si ordena.", 2),
        ("I want to understand how the cart loads and why checkout stalls.", 2),
        ("Review what the router selects and whether it caches.", 2),
        ("Analyze which file owns search; where errors surface.", 2),
        ("Investigate if checkout retries and how failures surface.", 2),
        ("An audit of checkout: what it charges and when it runs.", 2),
        ("A review of the cart, how it loads and if it expires.", 2),
        ("Analysis of search: why it is slow and which index it uses.", 2),
        (STORY, 3),
    ],
)
def test_question_extraction_phrasing_variants(story: str, expected: int) -> None:
    assert len(extract_questions(story)) == expected


@pytest.mark.parametrize(
    ("text", "read_only"),
    [
        ("Revisa el login. No cambies nada.", True),
        ("Look at the router, read-only please.", True),
        ("Solo lectura: explica el flujo.", True),
        ("Fix the total. Don't change the public API.", False),
        ("Arregla el carrito sin tocar los estilos.", False),
    ],
)
def test_out_of_scope_phrases(text: str, read_only: bool) -> None:
    assert extract_out_of_scope(text)
    assert extract(text).read_only is read_only


def test_file_component_and_route_mentions() -> None:
    text = "El Hero en src/components/Hero.tsx va lento en /checkout; mira `useCart` y lib/"
    mentions = extract_mentions(text)
    assert "src/components/Hero.tsx" in mentions
    assert "/checkout" in mentions
    assert "useCart" in mentions
    assert "lib" in mentions


def test_errors_ignore_plain_prose() -> None:
    assert extract_errors("the error message is confusing") == ()
    assert extract_errors("error TS2322: Type 'x' is not assignable") != ()


def test_places_match_mentions_and_words_against_the_index() -> None:
    facts = extract("The hero is slow; check CartStore and src/app/page.tsx")
    files = ("src/components/Hero.tsx", "src/app/page.tsx", "src/store/cart.ts")
    symbols = {"CartStore": "src/store/cart.ts"}
    assert match_places(facts, files, symbols) == (
        "src/app/page.tsx",
        "src/store/cart.ts",
        "src/components/Hero.tsx",
    )


def test_an_unclear_story_has_low_confidence() -> None:
    text = "The landing page, the hero section and the footer."
    choice = heuristic_type(text, extract(text))
    assert choice.probability < 0.6


def test_the_heuristic_backend_understands_offline() -> None:
    ledger = MemoryLedger()
    decisions = DecisionMaker(HeuristicInstinct(), ledger, lambda: NOW)
    understood = IntakeService(decisions, 0.6, places).understand(STORY)
    assert understood.kind == Choice("investigation", understood.kind.probability)
    assert not understood.needs_confirm
    assert understood.is_read_only
    assert understood.places == ("src/components/Hero.tsx",)
    assert understood.missing_for("investigation") == ("deliverable",)
    assert understood.answered("investigation", "deliverable").missing == ()
    request = understood.request()
    assert request.type == "investigation"
    assert request.out_of_scope == "No cambies nada"
    assert "- ¿Cómo funciona el carrito?" in request.why
    assert request.tests.startswith("Deliverable:")
    assert understood.cost_usd == 0.0
    assert len(ledger.decisions()) == len(intake_asks(STORY, extract(STORY), understood.kind))


def test_investigation_without_extracted_questions_uses_the_story() -> None:
    story = "Audit checkout behavior."
    decisions = DecisionMaker(HeuristicInstinct(), MemoryLedger(), lambda: NOW)
    understood = IntakeService(decisions, 0.6, places).understand(story)
    assert understood.kind.option == "investigation"
    assert understood.facts.questions == ()
    assert understood.request().why == f"- {story}"


def test_real_story_intake_does_not_need_jev_for_its_questions() -> None:
    decisions = DecisionMaker(HeuristicInstinct(), MemoryLedger(), lambda: NOW)
    understood = IntakeService(decisions, 0.6, places).understand(REAL_STORY)
    assert understood.backend == "heuristic"
    assert understood.kind.option == "investigation"
    assert understood.request().why.splitlines() == [
        "- ¿Para qué sirve esta landing?",
        "- ¿Cómo funciona el carrito y el checkout?",
        "- ¿El hero de física afecta el rendimiento?",
        "- ¿Qué riesgos de SEO o accesibilidad tiene?",
    ]


def jev_answer(question: dict[str, object]) -> dict[str, object]:
    kind = question["type"]
    if kind == "choice":
        criteria = question["criteria"]
        assert isinstance(criteria, dict)
        options = list(criteria)
        pick = "investigation" if "investigation" in options else options[1]
        return {"choice": pick, "probabilities": {pick: 0.9}}
    if kind == "score":
        return {"score": 1, "confidence": 0.8}
    return {"noul": 0.2}


def test_intake_questions_go_to_jev_in_one_batched_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        answers = {key: jev_answer(value) for key, value in body["questions"].items()}
        return httpx.Response(200, json={"answers": answers, "usage": {"cost": 0.0012}})

    jev = JevInstinct(transport=httpx.MockTransport(handler))
    ledger = MemoryLedger()
    decisions = DecisionMaker(jev, ledger, lambda: NOW, fallback=HeuristicInstinct())
    understood = IntakeService(decisions, 0.6, places).understand(STORY)
    assert len(bodies) == 1
    questions = bodies[0]["questions"]
    assert isinstance(questions, dict)
    assert {"kind", "read_only", "scope", "risk", "depth"} <= set(questions)
    assert "gap:investigation:deliverable" in questions
    state = json.loads(str(bodies[0]["state"]))
    assert "hints" not in state
    assert understood.backend == "jev"
    assert understood.kind.option == "investigation"
    assert understood.cost_usd == pytest.approx(0.0012)
    logged = ledger.decisions()
    assert len(logged) == len(questions)
    assert sum(item.cost_usd for item in logged) == pytest.approx(0.0012)


def test_a_failing_jev_falls_back_to_the_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    jev = JevInstinct(transport=httpx.MockTransport(handler))
    ledger = MemoryLedger()
    decisions = DecisionMaker(jev, ledger, lambda: NOW, fallback=HeuristicInstinct())
    understood = IntakeService(decisions, 0.6, places).understand(STORY)
    assert understood.backend == "heuristic"
    assert understood.kind.option == "investigation"
    assert understood.fallback_from == "jev"
    assert understood.fallback_error == "jev answered HTTP 503"
    assert {item.fallback_error for item in ledger.decisions()} == {"jev answered HTTP 503"}
    assert {item.fallback_from for item in ledger.decisions()} == {"jev"}
