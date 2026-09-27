from __future__ import annotations

import time
from pathlib import Path

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.mandate_flow import MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.routing import RoutePlan
from cuanta.bootstrap import Container
from cuanta.domain.code_index import index_path
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.routing import RoutingPolicy
from tests.fakes import FakeRunner


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
