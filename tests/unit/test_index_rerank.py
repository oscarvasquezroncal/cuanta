from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from cuanta.adapters.instinct.jev import JevInstinct
from cuanta.bootstrap import Container
from cuanta.domain.config import Config, layer_from_table, merge


def test_path_sharing_is_separate_explicit_opt_in() -> None:
    assert not Config().instinct_share_paths
    config = merge((layer_from_table({"instinct": {"share_paths": True}}),))
    assert config.instinct_share_paths


@pytest.mark.parametrize("share", [False, True])
def test_one_jev_batch_contains_only_sanitized_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    share: bool,
) -> None:
    for number in range(35):
        (tmp_path / f"checkout_{number:02}.py").write_bytes(
            b"checkout = 'SOURCE_DO_NOT_TRANSFER'\n"
        )
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        state = json.loads(body["state"])
        preferred = next(
            item.split(":", 1)[0] for item in state["candidates"] if "checkout_34.py" in item
        )
        answers = {
            key: {"score": 9 if key == preferred else 0, "confidence": 0.8}
            for key in body["questions"]
        }
        return httpx.Response(200, json={"answers": answers, "usage": {"cost": 0.001}})

    jev = JevInstinct(transport=httpx.MockTransport(handler))
    container = Container.for_project(tmp_path)
    reader = container.index_reader()
    try:
        reader.update()
        reader.service.note(
            "checkout_00.py", "Owner contact example@example.invalid. Responsible for checkout.", 1
        )
        reader.service.note("checkout_01.py", "<div>PROPRIETARY_JSX_BODY</div>", 1)
        reader.service.note("checkout_02.py", "launchSecretCall()", 1)
        baseline = reader.find("checkout")
        result = reader.find("checkout password=hiddencredential", reranker=jev, share_paths=share)
        if share:
            assert len(bodies) == 1
            questions = bodies[0]["questions"]
            assert isinstance(questions, dict) and len(questions) == 35
            assert result[0].path == "checkout_34.py"
            wire = json.dumps(bodies[0])
            assert "SOURCE_DO_NOT_TRANSFER" not in wire
            assert "PROPRIETARY_JSX_BODY" not in wire and "launchSecretCall" not in wire
            assert "example@example.invalid" not in wire and "hiddencredential" not in wire
            assert str(tmp_path) not in wire and "source_hash" not in wire and "rules" not in wire
            assert reader.rerank_receipt and reader.rerank_receipt.cost_usd == 0.001
        else:
            assert not bodies and {hit.path for hit in result} == {hit.path for hit in baseline}
    finally:
        reader.close()
        container.close()


def test_jev_small_candidate_set_does_not_send_a_request(tmp_path: Path) -> None:
    (tmp_path / "cart.py").write_bytes(b"checkout = 1\n")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    container = Container.for_project(tmp_path)
    reader = container.index_reader()
    try:
        reader.update()
        assert reader.find(
            "checkout",
            reranker=JevInstinct(transport=httpx.MockTransport(handler)),
            share_paths=True,
        )
        assert not requests and reader.rerank_reason == "fewer than 30 candidates"
    finally:
        reader.close()
        container.close()
