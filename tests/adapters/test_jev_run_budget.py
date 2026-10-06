from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from cuanta.adapters.instinct.jev import JevInstinct
from cuanta.bootstrap import Container
from cuanta.domain.config import Config
from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.messages import Message


def test_identical_jev_requests_are_paid_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"answers": {"q": {"noul": 0.8}}, "usage": {"cost": 0.01}})

    backend = JevInstinct(transport=httpx.MockTransport(transport))
    first, receipt = backend.noul("same?", {})
    second, cached = backend.noul("same?", {})
    assert first == second
    assert len(calls) == 1
    assert receipt.cost_usd == 0.01
    assert cached.cost_usd == 0.0


def test_jev_breaker_sends_no_request_after_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.extensions["timeout"]["read"] <= 3
        raise httpx.ReadTimeout("offline")

    backend = JevInstinct(transport=httpx.MockTransport(transport))
    for question in ("first?", "second?"):
        with pytest.raises(EnvironmentFailure):
            backend.noul(question, {})
    assert len(calls) == 1


def test_independent_jev_requests_can_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    barrier = threading.Barrier(2)

    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["state"] == "{}"
        barrier.wait(timeout=5)
        return httpx.Response(200, json={"answers": {"q": {"noul": 0.8}}})

    backend = JevInstinct(transport=httpx.MockTransport(transport))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda question: backend.noul(question, {}), ("a?", "b?")))
    assert all(answer.p_yes == 0.8 for answer, _ in outcomes)


def test_each_run_gets_a_fresh_jev_budget_and_request_cache(tmp_path: Path) -> None:
    container = Container(tmp_path, Config())
    first = container.instinct_backend("jev")
    assert isinstance(first, JevInstinct)
    assert container.instinct_backend("jev") is first
    container.decision_scope.set("R1", "same-request", False)
    before = first._scope()
    container.decision_scope.set("R2", "same-request", False)
    assert first._scope() != before
    container.close()


def test_known_jev_answers_remain_cached_after_the_budget_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    now = [100.0]
    monkeypatch.setattr("cuanta.adapters.instinct.jev.time.monotonic", lambda: now[0])
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"answers": {"q": {"noul": 0.8}}})

    backend = JevInstinct(transport=httpx.MockTransport(transport))
    first, _ = backend.noul("same?", {})
    now[0] += 7
    cached, receipt = backend.noul("same?", {})
    assert cached == first and receipt.cost_usd == 0
    with pytest.raises(EnvironmentFailure):
        backend.noul("new?", {})
    assert len(calls) == 1


def test_jev_failure_emits_a_single_structured_localizable_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    notices: list[Message] = []

    def transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    backend = JevInstinct(transport=httpx.MockTransport(transport), notice=notices.append)
    for question in ("first?", "second?"):
        with pytest.raises(EnvironmentFailure):
            backend.noul(question, {})
    assert [message.key for message in notices] == ["instinct.unavailable"]
