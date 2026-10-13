from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from cuanta.bootstrap import Container
from cuanta.cli import hooks
from cuanta.domain.index_first import (
    IndexCall,
    index_first,
    index_ratio,
    query_key,
    redundancy_prior,
    redundant_decision,
)
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.messages import english
from cuanta.tui.i18n import Catalog


def test_a_search_before_any_index_query_is_refused_with_the_call_to_make() -> None:
    grep = index_first("Grep", {"pattern": "def total"}, "", 0)
    assert grep.permission == "deny"
    assert 'mcp__cuanta__find with query "def total"' in grep.reason
    read = index_first("Read", {"file_path": "src/cart.py"}, "src/cart.py", 0)
    assert 'mcp__cuanta__card with path "src/cart.py"' in read.reason
    assert index_first("Grep", {"pattern": "x"}, "", 2).permission == ""
    assert index_first("Edit", {"file_path": "src/cart.py"}, "src/cart.py", 0).permission == ""


def test_a_read_the_index_already_paged_or_a_repeated_query_looks_redundant() -> None:
    recent = (
        IndexCall("find", query=query_key("def  total")),
        IndexCall("page", "src/cart.py", 10, 40),
    )
    inside = {"file_path": "src/cart.py", "offset": 12, "limit": 10}
    assert redundancy_prior("Read", inside, "src/cart.py", recent) == 0.8
    outside = {"file_path": "src/cart.py", "offset": 50, "limit": 10}
    assert redundancy_prior("Read", outside, "src/cart.py", recent) == 0.0
    assert redundancy_prior("Read", {"file_path": "src/cart.py"}, "src/cart.py", recent) == 0.0
    assert redundancy_prior("Grep", {"pattern": "DEF TOTAL"}, "", recent) == 0.7
    assert redundant_decision("Read", "src/cart.py", 0.8).permission == "deny"
    assert redundant_decision("Read", "src/cart.py", 0.6).permission == ""


def test_the_report_names_the_index_and_raw_read_counts_in_both_languages() -> None:
    shown = index_ratio(12, 3, 4)
    assert english(shown) == "index first: 12 index calls, 3 Grep, 4 Read"
    assert Catalog("es").message(shown) == "índice primero: 12 consultas al índice, 3 Grep, 4 Read"


def specific(output: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], output["hookSpecificOutput"])


def index_event(name: str, path: str = "", lines: str = "", query: str = "") -> LedgerEvent:
    raw = {key: value for key, value in (("lines", lines), ("query", query)) if value}
    return LedgerEvent(
        run_id="RUN",
        source="cuanta_mcp",
        kind="index_call",
        tool_name="mcp__cuanta__" + name,
        tool_use_id=f"{name}-{path}-{lines}-{query}",
        file_path=path,
        raw=json.dumps(raw),
    )


def test_the_index_first_hook_reads_the_run_and_refuses_or_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUANTA_RUN_ID", "RUN")
    source = tmp_path / "src" / "cart.py"
    source.parent.mkdir()
    source.write_text("line\n" * 50, encoding="utf-8")
    container = Container.for_project(tmp_path)
    try:
        ledger = container.ledger()
        ledger.add_run(Run("RUN", "mandate"))
        grep: dict[str, object] = {
            "tool_name": "Grep",
            "tool_input": {"pattern": "total", "path": "src"},
            "session_id": "S",
            "tool_use_id": "g1",
        }
        refused = specific(hooks.hook_output(grep, tmp_path, "pre", index=True))
        assert refused["permissionDecision"] == "deny"
        assert "Index first" in str(refused["permissionDecisionReason"])
        assert hooks.hook_output(grep, tmp_path, "pre") == {}
        ledger.add_events((index_event("page", "src/cart.py", "1-40"),))
        assert hooks.hook_output(grep, tmp_path, "pre", index=True) == {}
        read: dict[str, object] = {
            "tool_name": "Read",
            "tool_input": {"file_path": "src/cart.py", "offset": 5, "limit": 10},
            "session_id": "S",
            "tool_use_id": "r1",
        }
        again = specific(hooks.hook_output(read, tmp_path, "pre", index=True))
        assert again["permissionDecision"] == "deny"
        assert "Redundant Read" in str(again["permissionDecisionReason"])
    finally:
        container.close()


def test_the_hook_flag_is_read_beside_the_line_limit() -> None:
    assert hooks.line_limit(["index"]) == 400
    assert hooks.line_limit(["250", "index"]) == 250
    assert hooks.line_limit(["nope"]) is None
