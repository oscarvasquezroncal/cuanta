from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.application.code_index import IndexService
from cuanta.application.index_summaries import SummaryResult
from cuanta.bootstrap import Container
from tests.fakes import FakeRunner
from tests.support import invoke


def test_default_read_commands_are_local_and_exclude_stale_facts(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    (tmp_path / "test_cart.py").write_bytes(b"from cart import total\n")
    container = Container.for_project(tmp_path)
    reader = container.index_reader()
    try:
        reader.update()
        reader.service.note("cart.py", "Owns cart totals", 1)
    finally:
        reader.close()
        container.close()
    arguments = ["--json", "--project", str(tmp_path)]
    found = invoke([*arguments, "find", "cart"])
    assert found.exit_code == 0, found.stdout
    assert json.loads(found.stdout)["hits"][0]["reasons"]
    card = invoke([*arguments, "card", "cart.py"])
    assert card.exit_code == 0, card.stdout
    assert "Owns cart totals" in json.loads(card.stdout)["text"]
    impact = invoke([*arguments, "impact", "cart.py"])
    assert impact.exit_code == 0 and json.loads(impact.stdout)["impact"][0][0] == "test_cart.py"
    facts = invoke([*arguments, "facts", "cart.py"])
    assert facts.exit_code == 0 and len(json.loads(facts.stdout)["facts"]) == 1
    (tmp_path / "cart.py").write_bytes(b"total = 2\n")
    assert json.loads(invoke([*arguments, "facts", "cart.py"]).stdout)["facts"] == []
    stale = invoke([*arguments, "facts", "cart.py", "--stale"])
    assert json.loads(stale.stdout)["facts"][0]["stale"]
    assert not fake_runner.calls


@pytest.mark.parametrize("command", ["card", "impact"])
def test_read_commands_reject_paths_outside_index(tmp_path: Path, command: str) -> None:
    result = invoke(["--json", "--project", str(tmp_path), command, "../outside.py"])
    assert result.exit_code != 0


def test_rerank_without_share_paths_does_not_open_ledger(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    result = invoke(["--json", "--project", str(tmp_path), "find", "cart", "--rerank"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["rerank"] == "disabled"
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()
    assert not fake_runner.calls


def test_summary_cli_previews_estimate_and_requires_explicit_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "cart.py").write_bytes(b"total = 1\n")
    confirmations: list[bool] = []

    def summaries(_container: Container, _service: IndexService, confirmed: bool) -> SummaryResult:
        confirmations.append(confirmed)
        return SummaryResult("fake-economy", 0.01, ("cart.py",), 1, confirmed, cap_usd=0.025)

    monkeypatch.setattr(Container, "index_summaries", summaries)
    arguments = ["--json", "--project", str(tmp_path), "index", "--summaries"]
    preview = invoke(arguments)
    assert preview.exit_code == 0, preview.stdout
    assert json.loads(preview.stdout)["summaries"]["estimate_usd"] == 0.01
    assert not json.loads(preview.stdout)["summaries"]["ran"]
    approved = invoke([*arguments, "--yes"])
    assert approved.exit_code == 0, approved.stdout
    assert json.loads(approved.stdout)["summaries"]["ran"]
    assert confirmations == [False, True]
