from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.application.context_pack import IndexContextPack
from cuanta.bootstrap import Container
from cuanta.domain.anchors import AnchorState, anchor_notes
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import english
from cuanta.domain.pack import ContextPack, Layer
from tests.fakes import copy_repo
from tests.real_run import phased_mandate


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


def _backend_packs(
    tmp_path: Path,
    request: MandateRequest,
    depth: str = "deep",
    roles: tuple[str, ...] = ("",),
) -> tuple[ChangePlan, tuple[ContextPack, ...]]:
    container = Container.for_project(copy_repo("python_backend", tmp_path))
    try:
        plan = container.change_plan(request)
        return plan, tuple(container.context_pack(request, depth, role, plan) for role in roles)
    finally:
        container.close()


def _mandate(why: str) -> MandateRequest:
    return MandateRequest(
        "feature",
        "Implementar el mandato adjunto",
        why=why,
        tests="pytest en verde",
        out_of_scope="el frontend",
    )


def test_anchors_only_in_the_evidence_come_first_as_cards_and_ranges(tmp_path: Path) -> None:
    _, (pack,) = _backend_packs(tmp_path, _mandate(phased_mandate()), roles=("orchestrator",))
    window = next(
        item for item in pack.items if item.key == "anchor:app/services/interpreter.py:287-289"
    )
    assert (window.layer, window.line, window.end_line) == (Layer.L2, 284, 292)
    assert "ANCHOR_287" in window.text and "287: " in window.text
    keys = {item.key for item in pack.items}
    assert "anchor:app/routers/consult.py:67" in keys
    assert "window:app/services/interpreter.py" not in keys
    assert [item.key for item in pack.items if item.layer is Layer.L1][:2] == [
        "anchor:app/routers/consult.py",
        "anchor:app/services/interpreter.py",
    ]
    assert (
        "[L2 anchor:app/services/interpreter.py:287-289 app/services/interpreter.py:284-292]"
        in pack.excerpts
    )
    assert pack.tokens <= pack.budget == 6000


def test_unresolved_anchors_are_reported_and_a_long_evidence_still_leaves_room_for_them(
    tmp_path: Path,
) -> None:
    why = phased_mandate() + "\n" + "relleno " * 6000
    _, (pack,) = _backend_packs(tmp_path, _mandate(why))
    assert {check.anchor.label: check.state for check in pack.anchors} == {
        "app/services/interpreter.py:287-289": AnchorState.PACKED,
        "app/routers/consult.py:67": AnchorState.PACKED,
        "app/models/legacy.py:12": AnchorState.MISSING,
        "app/routers/consult.py:900": AnchorState.OUT_OF_RANGE,
    }
    keys = {item.key for item in pack.items}
    assert {
        "anchor:app/services/interpreter.py",
        "anchor:app/services/interpreter.py:287-289",
        "anchor:app/routers/consult.py",
        "anchor:app/routers/consult.py:67",
    } <= keys
    request = next(item for item in pack.items if item.key == "request")
    assert len(request.text.encode("utf-8")) <= pack.budget
    assert f"[{len(why):,} characters in total]" in request.text
    assert [english(message) for message in anchor_notes(pack.anchors, pack.budget)] == [
        "File:line references not found among the indexed files: app/models/legacy.py:12",
        "File:line references past the last line of their file: app/routers/consult.py:900",
    ]


def test_the_senior_gets_ranges_of_its_anchored_edit_files_and_guarded_anchors_stay_cards(
    tmp_path: Path,
) -> None:
    request = MandateRequest(
        "bug",
        "Corregir app/services/interpreter.py:287-289",
        why="El router app/routers/consult.py:67 toma solo la primera respuesta.",
        out_of_scope="app/routers/consult.py",
    )
    plan, (senior, orchestrator) = _backend_packs(
        tmp_path, request, "normal", ("senior", "orchestrator")
    )
    edits = {item.path for item in plan.edit}
    assert "app/services/interpreter.py" in edits and "app/routers/consult.py" in plan.guard
    senior_keys = {item.key for item in senior.items}
    assert "anchor:app/services/interpreter.py:287-289" in senior_keys
    assert all(item.path in edits for item in senior.items if item.path)
    assert not any(key.startswith("window:") for key in senior_keys)
    keys = {item.key for item in orchestrator.items}
    assert "anchor:app/routers/consult.py" in keys
    assert "anchor:app/routers/consult.py:67" not in keys
    states = {check.anchor.label: check.state for check in orchestrator.anchors}
    assert states == {
        "app/services/interpreter.py:287-289": AnchorState.PACKED,
        "app/routers/consult.py:67": AnchorState.PROTECTED,
    }
    assert [english(message) for message in anchor_notes(orchestrator.anchors, 4000)] == [
        "Referenced files the change plan protects (context only, not editable): "
        "app/routers/consult.py:67"
    ]


def test_an_anchored_range_longer_than_the_depth_window_is_reported_as_packed_in_part(
    tmp_path: Path,
) -> None:
    request = MandateRequest(
        "bug",
        "Corregir app/services/interpreter.py:100-200",
        why="El bucle corta la lista.",
        out_of_scope="docs",
    )
    _, (pack,) = _backend_packs(tmp_path, request, "quick", ("orchestrator",))
    item = next(
        item for item in pack.items if item.key == "anchor:app/services/interpreter.py:100-200"
    )
    assert (item.line, item.end_line) == (97, 108)
    assert [(check.anchor.label, check.state) for check in pack.anchors] == [
        ("app/services/interpreter.py:100-200", AnchorState.PARTIAL)
    ]
    assert [english(message) for message in anchor_notes(pack.anchors, pack.budget)] == [
        "Referenced ranges longer than the pack window, packed in part (range → packed lines): "
        "app/services/interpreter.py:100-200 → 100-108"
    ]
    _, (deep,) = _backend_packs(tmp_path / "deep", request, "deep", ("orchestrator",))
    assert [check.state for check in deep.anchors] == [AnchorState.PARTIAL]
    whole = MandateRequest(
        "bug", "Corregir app/services/interpreter.py:100-104", "El bucle.", out_of_scope="docs"
    )
    _, (fits,) = _backend_packs(tmp_path / "fits", whole, "quick", ("orchestrator",))
    assert [check.state for check in fits.anchors] == [AnchorState.PACKED]
    assert anchor_notes(fits.anchors, fits.budget) == ()
