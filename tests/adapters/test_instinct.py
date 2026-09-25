from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.instinct.jev import KEY_ENV, JevInstinct, score_levels
from cuanta.adapters.instinct.llm import LlmInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.instinct import (
    DecisionMaker,
    SignatureTriage,
    consent_ok,
    redact_context,
)
from cuanta.domain.errors import EnvironmentFailure, NotAvailable
from cuanta.domain.instinct import SCOPES
from cuanta.domain.ledger import SignatureRecord, TestRunRecord
from cuanta.domain.messages import english
from cuanta.domain.testing import Signature

BASE_ENV = "TYPESAFE_API_BASE"
Handler = Callable[[httpx.Request], httpx.Response]


def _jev(handler: Handler) -> JevInstinct:
    return JevInstinct(transport=httpx.MockTransport(handler))


def test_jev_choose_sends_documented_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "test-key")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        answer = {
            "type": "choice",
            "choice": "normal",
            "probabilities": {"normal": 0.81},
            "confidence": 0.7,
        }
        usage = {"input_tokens": 1_000_000, "output_tokens": 20}
        return httpx.Response(
            200, json={"model": "jev-1.13.0", "answers": {"q": answer}, "usage": usage}
        )

    choice, receipt = _jev(handler).choose("size?", SCOPES, {"what": "fix"})
    assert (choice.option, choice.probability) == ("normal", 0.81)
    assert receipt.backend == "jev"
    assert receipt.cost_usd == pytest.approx(0.042)
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "jev-latest"
    assert body["questions"]["q"]["type"] == "choice"
    assert set(body["questions"]["q"]["criteria"]) == set(SCOPES)


def test_jev_noul_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "k")
    ok = _jev(
        lambda request: httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.99}}})
    )
    assert ok.noul("urgent?", {})[0].p_yes == 0.99
    failing = _jev(lambda request: httpx.Response(429, json={}))
    with pytest.raises(EnvironmentFailure, match="429"):
        failing.noul("x", {})


def test_jev_score_sends_ordered_levels_and_maps_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "k")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["question"] = json.loads(request.content)["questions"]["q"]
        answer = {
            "type": "score",
            "score": 1.5,
            "confidence": 0.35,
            "legend": {"0": "low", "1": "mid", "2": "high"},
            "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5},
        }
        return httpx.Response(200, json={"answers": {"q": answer}})

    score, _ = _jev(handler).score("risk?", 0, 2, {})
    question = seen["question"]
    assert isinstance(question, dict)
    assert question["type"] == "score"
    assert len(question["criteria"]) == 3
    assert (score.value, score.confidence) == (1.5, 0.35)
    assert score.legend == ("low", "mid", "high")


def test_score_levels_stay_within_the_documented_range() -> None:
    assert score_levels(0, 2) == (0.0, 1.0, 2.0)
    assert len(score_levels(0, 100)) == 10
    assert len(score_levels(0, 0.5)) == 10
    assert score_levels(0, 10)[-1] == 10


def openrouter_answer(answer: dict[str, object], cost: float | None) -> dict[str, object]:
    usage: dict[str, object] = {"input_tokens": 296, "output_tokens": 20}
    if cost is not None:
        usage["cost"] = cost
    return {
        "id": "gen-123",
        "provider": "TypeSafe",
        "model": "typesafe/jev-1.13",
        "answers": {"q": answer},
        "usage": usage,
    }


def test_jev_through_openrouter_uses_the_sdk_base_and_reported_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, "or-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://openrouter.ai/api/")
    monkeypatch.setenv("TYPESAFE_API_BASE", "https://ignored.example")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=openrouter_answer({"type": "noul", "noul": 0.9}, 0.00002))

    noul, receipt = _jev(handler).noul("urgent?", {})
    assert noul.p_yes == 0.9
    assert receipt.cost_usd == 0.00002
    assert seen == ["https://openrouter.ai/api/v1/systemone"]


def test_openrouter_without_reported_cost_never_applies_the_typesafe_list_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, "or-key")
    monkeypatch.setenv(BASE_ENV, "https://openrouter.ai/api")
    backend = _jev(
        lambda request: httpx.Response(
            200, json=openrouter_answer({"type": "noul", "noul": 0.1}, None)
        )
    )
    assert backend.noul("x", {})[1].cost_usd == 0.0


def test_connection_check_asks_one_noul_and_ignores_the_model_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, "or-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://openrouter.ai/api")
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/v1/models"):
            catalog = [{"id": f"vendor/model-{index}"} for index in range(400)]
            return httpx.Response(200, json={"data": catalog})
        question = json.loads(request.content)["questions"]["q"]
        assert question["type"] == "noul"
        return httpx.Response(200, json=openrouter_answer({"type": "noul", "noul": 0.5}, 1e-5))

    text = english(_jev(handler).connection())
    assert asked == ["POST /api/v1/systemone"]
    assert text.startswith("Connected: typesafe/jev-1.13 via TypeSafe · ")


def test_connection_fails_when_the_question_fails_even_if_models_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, "or-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://openrouter.ai/api")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "typesafe/jev-1.13"}]})
        return httpx.Response(401, json={"error": {"message": "No auth"}})

    with pytest.raises(EnvironmentFailure, match="401"):
        _jev(handler).connection()


def test_jev_without_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    backend = JevInstinct()
    usable, said = backend.available()
    assert not usable
    assert english(said).startswith(f"{KEY_ENV} is not set")
    assert "after setx, open a new terminal" in english(said)
    with pytest.raises(NotAvailable):
        backend.choose("q", ("a",), {})


def test_llm_without_engine_is_unavailable() -> None:
    backend = LlmInstinct(None, ".")
    assert backend.available()[0] is False
    with pytest.raises(NotAvailable):
        backend.noul("q", {})


def test_remote_context_is_redacted_and_tool_output_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, "k")
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content)["state"])
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.5}}})

    maker = DecisionMaker(_jev(handler), MemoryLedger(), lambda: "now", HeuristicInstinct())
    maker.noul(
        "q",
        {
            "error": "token=abcdef123456 from dev@example.invalid in C:\\Users\\dev\\repo\\src\\app.py",
            "output": "x" * 5000,
        },
    )
    state = captured[0]
    assert "abcdef123456" not in state
    assert "dev@example.invalid" not in state
    assert "Users" not in state
    assert "ok, 5000 chars (omitted)" in state
    assert redact_context({"n": 3, "flag": True}) == {"n": 3, "flag": True}


def test_decisions_are_logged_with_latency_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "k")
    ledger = MemoryLedger()
    broken = _jev(lambda request: httpx.Response(500, json={}))
    maker = DecisionMaker(broken, ledger, lambda: "2026-01-01T00:00:00Z", HeuristicInstinct())
    choice, decided = maker.choose(
        "size?", SCOPES, {"kind": "scope", "what": "fix typo", "where": "a"}, run_id="R"
    )
    assert choice.option == "trivial"
    assert decided.receipt.backend == "heuristic"
    maker.record_outcome(decided.decision_id, "ok")
    stored = ledger.decisions(run_id="R")[0]
    assert (stored.primitive, stored.answer, stored.outcome) == ("choose", "trivial", "ok")
    assert stored.latency_ms >= 0
    assert json.loads(stored.options) == list(SCOPES)


def test_consent_rules() -> None:
    assert consent_ok(HeuristicInstinct(), ())
    assert not consent_ok(JevInstinct(), ())
    assert consent_ok(JevInstinct(), ("jev",))


def test_signature_triage_uses_history() -> None:
    ledger = MemoryLedger()
    for index, present in enumerate([True, False, True, False]):
        record = TestRunRecord(
            f"T{index}",
            "old",
            "pytest",
            "pytest",
            "red",
            0,
            1,
            0,
            0,
            0.1,
            "c",
            f"2026-01-0{index + 1}",
        )
        signatures = (
            [SignatureRecord(f"T{index}", "flaky1", "E", "E", "t", "", 1)] if present else []
        )
        ledger.add_test_run(record, signatures)
    triage = SignatureTriage(ledger, DecisionMaker(HeuristicInstinct(), ledger, lambda: "now"))
    flaky = Signature("flaky1", "E: x", "AssertionError: x", 1, "t", "")
    assert triage.features("flaky1", "new") == {"seen_before": 2, "flips": 2}
    assert triage(flaky, "new") == "flaky"
    fresh = Signature("fresh1", "E: y", "AssertionError: y", 1, "t", "")
    assert triage(fresh, "new") == "caused by this run"
    network = Signature("net1", "E", "ConnectionError: connection refused", 1, "t", "")
    assert triage(network, "new") == "environment"
    assert len(ledger.decisions()) == 3
