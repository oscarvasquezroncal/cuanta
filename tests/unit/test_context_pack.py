from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.application.context_pack import IndexContextPack
from cuanta.bootstrap import Container
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.pack import Layer


def _project(tmp_path: Path) -> Container:
    (tmp_path / "cart.py").write_text(
        "def checkout():\n    return 1\n" + "padding = 0\n" * 100, encoding="utf-8"
    )
    (tmp_path / "renderer.py").write_text("def checkout_view():\n    return 2\n", encoding="utf-8")
    return Container.for_project(tmp_path)


@pytest.mark.parametrize(
    ("depth", "budget", "window"), [("quick", 2000, 12), ("normal", 4000, 24), ("deep", 6000, 40)]
)
def test_context_budget_windows_and_stable_prefix(
    tmp_path: Path, depth: str, budget: int, window: int
) -> None:
    container = _project(tmp_path)
    request = MandateRequest("feature", "Fix cart.py checkout", out_of_scope="renderer.py")
    first = container.context_pack(request, depth)
    second = container.context_pack(request, depth)
    assert first is second
    assert first.tokens <= first.budget == budget
    assert first.stable_prefix.encode() == second.stable_prefix.encode()
    assert (
        first.text.index(first.stable_prefix)
        < first.text.index(first.excerpts)
        < first.text.index(first.volatile)
    )
    assert all(
        item.end_line - item.line + 1 <= window for item in first.items if item.layer == Layer.L2
    )
    assert "1: def checkout()" in first.excerpts
    assert "renderer.py" in first.stable_prefix
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()
    container.close()


def test_note_changes_cache_without_inventory_change_and_anchor_change_removes_fact(
    tmp_path: Path,
) -> None:
    container = _project(tmp_path)
    reader = container.index_reader()
    try:
        reader.update()
        compiler = IndexContextPack(reader)
        request = MandateRequest("feature", "cart.py checkout")
        before = compiler.compile(request)
        inventory_hash = reader.service.status().content_hash
        reader.service.note("cart.py", "Keeps checkout total anchored", 1, 2)
        fresh = compiler.compile(request)
        assert reader.service.status().content_hash == inventory_hash
        assert fresh.cache_key != before.cache_key
        assert "Keeps checkout total anchored" in fresh.stable_prefix
        (tmp_path / "cart.py").write_text("def checkout():\n    return 99\n", encoding="utf-8")
        reader.update()
        stale = compiler.compile(request)
        assert "Keeps checkout total anchored" not in stale.text
        assert stale.cache_key != fresh.cache_key
    finally:
        reader.close()
        container.close()


def test_senior_tester_get_only_edit_cards_and_valid_facts(tmp_path: Path) -> None:
    container = _project(tmp_path)
    reader = container.index_reader()
    try:
        reader.update()
        note = reader.service.note("cart.py", "Useful cart fact", 1, 2)
        reader.service.index.put_rows(
            "notes",
            (replace(note, id="invalid", text="Invalid forged fact", target="anchor:wrong"),),
        )
        reader.service.note("renderer.py", "Write protected renderer", 1, 2)
        plan = ChangePlan(
            (EditTarget("cart.py", 1, "explicit"), EditTarget("renderer.py", 1, "guard conflict")),
            ("renderer.py",),
            ("renderer.py",),
            (),
            False,
            1,
        )
        compiler = IndexContextPack(reader)
        for role in ("senior", "tester"):
            pack = compiler.compile(MandateRequest("feature", "checkout"), "normal", role, plan)
            assert "Useful cart fact" in pack.stable_prefix
            assert "Invalid forged fact" not in pack.text
            assert "Write protected renderer" not in pack.text
            assert all(item.path != "renderer.py" for item in pack.items)
            assert not pack.excerpts
        override = replace(plan, edit=(), read=("cart.py",))
        assert (
            compiler.compile(
                MandateRequest("feature", "checkout"), "normal", "senior", override
            ).cache_key
            != pack.cache_key
        )
    finally:
        reader.close()
        container.close()


def test_source_race_is_excluded_before_any_prompt_text(tmp_path: Path) -> None:
    container = _project(tmp_path)
    reader = container.index_reader()
    try:
        reader.update()
        compiler = IndexContextPack(reader)
        request = MandateRequest("feature", "cart.py checkout")
        original = compiler.compile(request)
        assert "return 1" in original.excerpts
        (tmp_path / "cart.py").write_text("danger = 'new unindexed text'\n", encoding="utf-8")
        pack = compiler.compile(request)
        assert pack is not original
        assert all(item.path != "cart.py" for item in pack.items)
        assert "new unindexed text" not in pack.text
        (tmp_path / "cart.py").write_text(
            "def checkout():\n    return 1\n" + "padding = 0\n" * 100, encoding="utf-8"
        )
        restored = compiler.compile(request)
        assert "return 1" in restored.excerpts
    finally:
        reader.close()
        container.close()


def test_investigation_keeps_fresh_observational_facts_and_windows_without_write_items(
    tmp_path: Path,
) -> None:
    container = _project(tmp_path)
    reader = container.index_reader()
    try:
        reader.update()
        reader.service.note("cart.py", "Checkout returns the fixed total", 1, 2)
        reader.service.note("renderer.py", "Write protected renderer", 1, 2)
        pack = IndexContextPack(reader).compile(
            MandateRequest(
                "investigation", "cart.py checkout renderer.py", out_of_scope="renderer.py"
            )
        )
        assert "Checkout returns the fixed total" in pack.stable_prefix
        assert "1: def checkout()" in pack.excerpts
        assert "Write protected renderer" not in pack.text
        assert "Readonly context" in pack.stable_prefix
        assert all(not item.write for item in pack.items)
    finally:
        reader.close()
        container.close()
