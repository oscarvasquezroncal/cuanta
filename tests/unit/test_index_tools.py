from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from cuanta.adapters.graph.index_ast import AstIndexExtractor
from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.adapters.system.index_inventory import LocalIndexInventory
from cuanta.application.code_index import IndexService
from cuanta.application.index_read import IndexRead
from cuanta.application.index_tools import TOOLS, IndexTools


@pytest.fixture
def tools(tmp_path: Path) -> Iterator[IndexTools]:
    (tmp_path / "cart.py").write_text("def checkout():\n    return 1\n", encoding="utf-8")
    (tmp_path / "viewer.py").write_text("from cart import checkout\n", encoding="utf-8")
    (tmp_path / "test_cart.py").write_text(
        "from cart import checkout\ndef test_total():\n    assert checkout() == 1\n",
        encoding="utf-8",
    )

    def now() -> str:
        return "2026-09-01T00:00:00Z"

    service = IndexService(
        SqliteIndex(tmp_path / ".cuanta" / "index.db"),
        LocalIndexInventory(tmp_path),
        now,
        extractor=AstIndexExtractor(),
        verify_commands=lambda: ("cuanta test",),
    )
    reader = IndexRead(service, now)
    yield IndexTools(reader)
    reader.close()


def _call(tools: IndexTools, name: str, args: Mapping[str, object]) -> dict[str, object]:
    value = tools.call(name, args)
    assert isinstance(value, dict)
    assert value["cost_usd"] == 0
    json.dumps(value)
    return cast(dict[str, object], value)


def test_descriptors_expose_seven_closed_tools() -> None:
    assert {tool["name"] for tool in TOOLS} == {
        "find",
        "card",
        "impact",
        "facts",
        "page",
        "tests_for",
        "note",
    }
    for tool in TOOLS:
        schema = cast(dict[str, object], tool["inputSchema"])
        assert schema["additionalProperties"] is False
        annotations = cast(dict[str, object], tool["annotations"])
        assert annotations["readOnlyHint"] == (tool["name"] != "note")
    json.dumps(TOOLS)


def test_find_and_card_share_index_without_a_ledger(tools: IndexTools, tmp_path: Path) -> None:
    result = _call(tools, "find", {"query": "checkout", "k": 1})
    hits = cast(list[dict[str, object]], result["hits"])
    assert len(hits) == 1 and hits[0]["reasons"]
    card = cast(dict[str, object], _call(tools, "card", {"path": "cart.py"})["card"])
    assert card["path"] == "cart.py" and "checkout" in str(card["text"])
    assert (tmp_path / ".cuanta" / "index.db").exists()
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()


def test_impact_by_symbol_and_path_match_and_tests_keep_commands(tools: IndexTools) -> None:
    by_path = _call(tools, "impact", {"path": "cart.py"})
    assert by_path == _call(tools, "impact", {"symbol": "checkout"})
    neighbours = cast(list[dict[str, object]], by_path["neighbours"])
    assert {row["path"] for row in neighbours} == {"viewer.py", "test_cart.py"}
    linked = cast(list[dict[str, object]], _call(tools, "tests_for", {"path": "cart.py"})["tests"])
    assert len(linked) == 1
    assert linked[0]["path"] == "test_cart.py" and linked[0]["text"] == "cuanta test"


def test_numbered_range_and_symbol_source_windows_match(tools: IndexTools) -> None:
    by_lines = _call(tools, "page", {"path": "cart.py", "lines": "1:2"})
    assert _call(tools, "page", {"path": "cart.py", "lines": "1-2"})["text"] == by_lines["text"]
    assert by_lines == _call(tools, "page", {"path": "cart.py", "symbol": "checkout"})
    assert by_lines["text"] == "1: def checkout():\n2:     return 1"
    assert by_lines["level"] == "L2" and by_lines["estimated_tokens"]
    assert by_lines["truncated"] is False


def test_long_symbols_and_long_lines_are_explicitly_clipped(
    tools: IndexTools, tmp_path: Path
) -> None:
    (tmp_path / "large.py").write_text(
        "def large():\n" + "    value = '" + "x" * 600 + "'\n" + "    pass\n" * 250,
        encoding="utf-8",
    )
    result = _call(tools, "page", {"path": "large.py", "symbol": "large"})
    assert result["line"] == 1 and result["end_line"] == 200
    assert result["requested_end_line"] == 252 and result["truncated"] is True
    text = str(result["text"])
    assert len(text.splitlines()) == 200 and "[line clipped]" in text
    assert "x" * 301 not in text


@pytest.mark.parametrize("anchor", ["1:2", "checkout", ""])
def test_notes_use_existing_source_hash_and_anchor(tools: IndexTools, anchor: str) -> None:
    args: dict[str, object] = {"path": "cart.py", "text": "Checkout keeps the total"}
    if anchor:
        args["anchor"] = anchor
    row = cast(dict[str, object], _call(tools, "note", args)["note"])
    assert str(row["target"]).startswith("anchor:") and row["source_hash"]
    facts = cast(list[dict[str, object]], _call(tools, "facts", {"path": "cart.py"})["facts"])
    assert facts == [row]
    assert len(tools.reader.service.index.rows("notes")) == 1


def test_changed_anchor_disappears_but_unrelated_edit_preserves_fact(
    tools: IndexTools, tmp_path: Path
) -> None:
    _call(tools, "note", {"path": "cart.py", "text": "Constant total", "anchor": "1:2"})
    (tmp_path / "cart.py").write_text(
        "def checkout():\n    return 1\nother = 3\n", encoding="utf-8"
    )
    assert len(cast(list[object], _call(tools, "facts", {"path": "cart.py"})["facts"])) == 1
    (tmp_path / "cart.py").write_text("def checkout():\n    return 99\n", encoding="utf-8")
    assert _call(tools, "facts", {"path": "cart.py"})["facts"] == []
    assert tools.reader.service.index.rows("notes")[0].stale


def test_forged_anchor_is_revalidated_before_return(tools: IndexTools) -> None:
    _call(tools, "note", {"path": "cart.py", "text": "Valid", "anchor": "1:2"})
    row = tools.reader.service.index.rows("notes")[0]
    tools.reader.service.index.put_rows(
        "notes", (replace(row, id="forged", target="anchor:wrong", text="Unproved"),)
    )
    facts = cast(list[dict[str, object]], _call(tools, "facts", {"path": "cart.py"})["facts"])
    assert [fact["text"] for fact in facts] == ["Valid"]


def test_ambiguous_symbol_requires_a_path(tools: IndexTools, tmp_path: Path) -> None:
    (tmp_path / "other.py").write_text("def checkout():\n    return 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        tools.call("impact", {"symbol": "checkout"})
    assert _call(tools, "page", {"path": "cart.py", "symbol": "checkout"})["end_line"] == 2


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("unknown", {}),
        ("find", {"query": "checkout", "extra": True}),
        ("find", {"query": ""}),
        ("find", {"query": "checkout", "k": True}),
        ("find", {"query": "checkout", "k": 31}),
        ("find", {"query": "checkout", "k": 0}),
        ("find", {"query": "checkout", "k": 1.5}),
        ("impact", {}),
        ("impact", {"path": "cart.py", "symbol": "checkout"}),
        ("card", {"path": 1}),
        ("card", {"path": "../outside.py"}),
        ("page", {"path": "cart.py"}),
        ("page", {"path": "cart.py", "lines": "1:2", "symbol": "checkout"}),
        ("page", {"path": "cart.py", "lines": "0:1"}),
        ("page", {"path": "cart.py", "lines": "2:1"}),
        ("page", {"path": "cart.py", "lines": "1:201"}),
        ("page", {"path": "cart.py", "lines": "1:3"}),
        ("page", {"path": "cart.py", "lines": "1:2", "level": "L3"}),
        ("page", {"path": "missing.py", "lines": "1:2"}),
        ("page", {"path": "cart.py", "symbol": "missing"}),
        ("note", {"path": "cart.py", "text": ""}),
        ("note", {"path": "cart.py", "text": "x" * 4001}),
        ("note", {"path": "cart.py", "text": "note", "anchor": "1:3"}),
    ],
)
def test_invalid_arguments_are_rejected(
    tools: IndexTools, name: str, args: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match=r"."):
        tools.call(name, args)


def test_unsafe_or_excluded_source_cannot_be_read(tools: IndexTools, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("KEY=private\n", encoding="utf-8")
    for path in (".env", "C:/outside.py", "/outside.py", "cart.py/../viewer.py"):
        with pytest.raises(ValueError, match=r"Index paths|not an indexed"):
            tools.call("page", {"path": path, "lines": "1:1"})


def test_source_race_fails_before_content_is_returned(
    tools: IndexTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools.reader.update()
    monkeypatch.setattr(tools.reader, "update", lambda: None)
    monkeypatch.setattr(
        tools.reader.service.inventory, "read", lambda path: "def checkout():\n    return 999\n"
    )
    with pytest.raises(ValueError, match="changed"):
        tools.call("page", {"path": "cart.py", "lines": "1:2"})


def test_indexed_path_replaced_with_a_link_cannot_escape(
    tools: IndexTools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools.reader.update()
    monkeypatch.setattr(tools.reader, "update", lambda: None)
    outside = tmp_path.parent / (tmp_path.name + "-outside.py")
    outside.write_text("private = True\n", encoding="utf-8")
    source = tmp_path / "cart.py"
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(ValueError, match="links or junctions"):
        tools.call("page", {"path": "cart.py", "lines": "1:1"})
    assert outside.read_text(encoding="utf-8") == "private = True\n"
