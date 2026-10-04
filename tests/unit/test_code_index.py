from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.storage.sqlite_index import SqliteIndex
from cuanta.adapters.system.index_inventory import LocalIndexInventory
from cuanta.application.code_index import IndexService
from cuanta.application.mandate_flow import MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.routing import RoutePlan
from cuanta.bootstrap import Container
from cuanta.domain import index_limit
from cuanta.domain.code_index import IndexedFile, IndexStructure, index_path
from cuanta.domain.index_limit import IndexTooLarge
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.routing import RoutingPolicy
from tests.fakes import FakeRunner
from tests.real_run import real_project_tree
from tests.unit.test_index_inventory import git_index

STEPS = (("a", 5), ("b", 4), ("c", 3), ("d", 2), ("e", 1), ("f", 1))


class SpyExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, file: IndexedFile, text: str, paths: tuple[str, ...]) -> IndexStructure:
        self.calls.append(file.path)
        return IndexStructure()

    def resolve(self, path: str, module: str, paths: tuple[str, ...]) -> str:
        return ""


def stepped_tree(root: Path) -> None:
    for folder, count in STEPS:
        for number in range(count):
            target = root / folder / f"m{number}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"value = {number}\n", encoding="utf-8")
    (root / "main.py").write_text("value = 0\n", encoding="utf-8")


def test_incremental_inventory_and_separate_ledger(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
    container = Container.for_project(tmp_path)
    service = container.index_service()
    try:
        first = service.update()
        assert first.files == first.changed == 2
        assert first.coverage == 1.0 and first.updated_at
        assert all(item.content_hash and item.provenance for item in service.index.files())
        second = service.update()
        assert second.changed == second.removed == 0
        assert second.content_hash == first.content_hash
        assert second.updated_at == first.updated_at
        (tmp_path / "main.py").write_text("value = 2\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").unlink()
        third = service.update()
        assert third.changed == third.removed == 1
        assert third.content_hash != first.content_hash
        assert third.files == 1
        assert dict(third.counts)["notes"] == 0
    finally:
        service.close()
        container.close()
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()


def test_empty_and_copy_local_index(tmp_path: Path) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copy"
    original.mkdir()
    copied.mkdir()
    container = Container.for_project(original).sandbox_container(copied)
    container.refresh_index()
    assert (copied / ".cuanta" / "index.db").exists()
    assert not (original / ".cuanta").exists()
    service = container.index_service()
    try:
        assert service.status().files == 0
        assert service.status().coverage == 0.0
    finally:
        service.close()
        container.close()


def test_three_changes_in_three_hundred_files_fit_index_budgets(tmp_path: Path) -> None:
    for number in range(300):
        (tmp_path / f"module_{number}.py").write_text(f"value = {number}\n", encoding="utf-8")
    service = Container.for_project(tmp_path).index_service()
    try:
        started = time.perf_counter()
        first = service.update()
        assert time.perf_counter() - started < 10.0
        assert first.changed == 300
        for number in (0, 100, 200):
            (tmp_path / f"module_{number}.py").write_text("changed = True\n", encoding="utf-8")
        started = time.perf_counter()
        second = service.update()
        assert time.perf_counter() - started < 2.0
        assert second.changed == 3
    finally:
        service.close()


def test_index_stops_before_reading_or_parsing_above_the_limit(tmp_path: Path) -> None:
    stepped_tree(tmp_path)
    index = SqliteIndex(tmp_path / ".cuanta" / "index.db")
    spy = SpyExtractor()
    service = IndexService(
        index,
        LocalIndexInventory(tmp_path),
        lambda: "2026-10-03",
        extractor=spy,
        exclusions=("docs/private.md",),
        limit=10,
    )
    try:
        with (
            patch.object(Path, "read_bytes", side_effect=AssertionError("a file was read")),
            pytest.raises(IndexTooLarge) as caught,
        ):
            service.update()
        assert index.files() == ()
        assert spy.calls == []
    finally:
        service.close()
    failure = caught.value
    assert "17 files" in failure.message
    assert "a (5) · b (4) · c (3) · d (2) · e (1)" in failure.message
    assert "f (" not in failure.message
    assert failure.hint.endswith('[detect]\nexclude = ["docs/private.md", "a", "b"]')


def test_a_hidden_folder_git_tracks_is_left_out_of_the_lines_to_paste(tmp_path: Path) -> None:
    for number in range(8):
        target = tmp_path / ".odoo_ref" / "addons" / f"m{number}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"value = {number}\n", encoding="utf-8")
    for name in ("creator/a.py", "creator/b.py", ".github/workflows/ci.yml", "app.py"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    tracked = (".github/workflows/ci.yml", "app.py", "creator/a.py", "creator/b.py")
    (tmp_path / ".git" / "index").write_bytes(git_index(tracked))
    index = SqliteIndex(tmp_path / ".cuanta" / "index.db")
    service = IndexService(index, LocalIndexInventory(tmp_path), lambda: "2026-10-03", limit=5)
    try:
        with pytest.raises(IndexTooLarge) as caught:
            service.update()
    finally:
        service.close()
    assert caught.value.oversized.exclude == (".odoo_ref",)
    assert [name for name, _ in caught.value.oversized.largest] == [
        ".odoo_ref",
        "creator",
        ".github",
    ]


def test_the_module_ceiling_applies_when_no_limit_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stepped_tree(tmp_path)
    service = Container.for_project(tmp_path).index_service()
    try:
        assert service.update().files == 17
        monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 16)
        with pytest.raises(IndexTooLarge, match="17 files"):
            service.update()
        assert service.status().files == 17
    finally:
        service.close()


def test_the_real_run_tree_indexes_only_the_project_s_own_files(tmp_path: Path) -> None:
    expected = real_project_tree(tmp_path)
    service = Container.for_project(tmp_path).index_service()
    try:
        status = service.update()
        assert tuple(item.path for item in service.index.files()) == expected
        assert status.files == len(expected)
    finally:
        service.close()


def test_learning_after_a_run_records_an_oversized_index_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuanta.application.run_reports import RunReports
    from cuanta.domain.ledger import Run

    stepped_tree(tmp_path)
    container = Container.for_project(tmp_path)
    container.shared_ledger().add_run(
        Run("RUN", "mandate", status="ok", ended_at="2026-10-03T00:00:00Z")
    )
    reports = RunReports(container.state_workspace())
    reports.save_meta("RUN", {"changed_files": []})
    monkeypatch.setattr(index_limit, "INDEX_FILE_LIMIT", 5)
    try:
        container.learn_run("RUN")
        assert (reports.meta("RUN") or {}).get("index_learning_error") == "IndexTooLarge"
    finally:
        container.close()


def test_index_path_normalizes_slashes() -> None:
    assert index_path("src\\./module.py") == "src/module.py"


def test_both_mandate_paths_refresh_the_copy_inventory_before_launch(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    container = Container.for_project(root)
    container.runner = FakeRunner()
    container.workspace().write_text("docs/MANDATE_TEMPLATE.md", "```\n=== REQUEST ===\n```\n")
    request = MandateRequest("bug", "fix total", "wrong sum", out_of_scope="payments")
    ledger = MemoryLedger()
    flow = container.mandate_flow(ledger)
    flow.prepare(request, 0, MandateOptions(simple=True), preview=True)
    service = container.index_service()
    try:
        assert service.status().files == 1
    finally:
        service.close()
    container.workspace().write_text("cart.py", "total = 1\n")
    plan = RoutePlan(RoutingPolicy(), None, None, (), (), "heuristic")
    container.cross_engine(ledger, 0).run(request, plan, RecordingSink())
    service = container.index_service()
    try:
        assert service.status().files == 2
    finally:
        service.close()
        container.close()
