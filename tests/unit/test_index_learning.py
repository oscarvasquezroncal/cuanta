from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cuanta.application.run_reports import RunReports
from cuanta.bootstrap import Container
from cuanta.domain.ledger import LedgerEvent, Run, Snapshot
from cuanta.ports.ledger import EventQuery


def recorded(root: Path) -> tuple[Container, str]:
    (root / "cart.py").write_text("cart = 1\n", encoding="utf-8")
    container = Container.for_project(root)
    ledger = container.shared_ledger()
    ledger.add_run(
        Run(
            "RUN",
            "mandate",
            task_type="investigation",
            status="ok",
            ended_at="2026-09-27T00:00:00Z",
        )
    )
    digest = hashlib.sha256((root / "cart.py").read_bytes()).hexdigest()
    ledger.add_snapshots(
        (Snapshot("RUN", "start", "cart.py", digest), Snapshot("RUN", "end", "cart.py", digest))
    )
    reports = RunReports(container.state_workspace())
    reports.save_report("RUN", "cart.py:1 stores the cart.\n")
    reports.save_meta("RUN", {"changed_files": [], "task_type": "investigation"})
    ledger.add_events(
        (
            LedgerEvent(
                run_id="RUN",
                kind="tool_result",
                tool_name="Read",
                file_path="cart.py",
                ts="2026-09-26T23:59:59Z",
            ),
        )
    )
    return container, digest


def test_post_run_learning_imports_saved_report_once_without_source_writes(tmp_path: Path) -> None:
    container, _ = recorded(tmp_path)
    before = (tmp_path / "cart.py").read_bytes()
    container.learn_run("RUN")
    reports = RunReports(container.state_workspace())
    meta = reports.meta("RUN")
    assert meta is not None and meta["index_learning"] == {
        "findings_saved": 1,
        "stale_facts": 0,
        "notes_saved": 0,
    }
    service = container.index_service()
    first = service.index.rows("notes")
    assert len(first) == 1 and not first[0].stale
    service.close()
    container.learn_run("RUN")
    service = container.index_service()
    assert service.index.rows("notes") == first
    assert len(service.index.rows("history")) == 2
    service.close()
    assert (tmp_path / "cart.py").read_bytes() == before
    assert len(container.shared_ledger().events(EventQuery(run_id="RUN"))) == 1
    container.close()


def test_sandbox_notes_are_preserved_and_revalidated_against_original_source(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original"
    copy = tmp_path / "copy"
    original.mkdir()
    copy.mkdir()
    container, _ = recorded(original)
    (copy / "cart.py").write_text("cart = 1\n", encoding="utf-8")
    sub = Container.for_project(copy)
    service = sub.index_service()
    service.update()
    note = service.note("cart.py", "Verified source fact", 1, 1)
    service.close()
    sub.close()
    container.learn_run("RUN", copy)
    reader = container.index_reader()
    assert note.id in {row.id for row in reader.facts("cart.py")}
    reader.close()
    (original / "cart.py").write_text("cart = 2\n", encoding="utf-8")
    container.learn_run("RUN", copy)
    reader = container.index_reader()
    assert note.id not in {row.id for row in reader.facts("cart.py")}
    assert note.id in {row.id for row in reader.facts("cart.py", stale=True)}
    reader.close()
    assert (original / "cart.py").read_text(encoding="utf-8") == "cart = 2\n"
    container.close()


def test_missing_run_does_not_initialize_learning_state(tmp_path: Path) -> None:
    container = Container.for_project(tmp_path)
    container.learn_run("missing")
    assert not (tmp_path / ".cuanta/index.db").exists()
    assert not (tmp_path / ".cuanta/ledger.db").exists()
    container.close()


def test_outcome_change_updates_existing_index_history(tmp_path: Path) -> None:
    container, _ = recorded(tmp_path)
    container.learn_run("RUN")
    container.run_outcomes(container.shared_ledger()).accept("RUN")
    service = container.index_service()
    history = service.index.rows("history")
    assert history and all('"outcome": "accepted"' in row.text for row in history)
    assert len({row.id for row in history}) == len(history)
    service.close()
    container.close()


@pytest.mark.parametrize("sandbox", [False, True])
def test_pipeline_learning_counts_later_roles_and_deduplicates_combined_report(
    tmp_path: Path,
    sandbox: bool,
) -> None:
    container, digest = recorded(tmp_path)
    ledger = container.shared_ledger()
    ledger.update_run(
        Run("RUN", "cross", task_type="investigation", status="ok", ended_at="2026-09-27T00:00:00Z")
    )
    ledger.add_run(
        Run(
            "CHILD",
            "cross",
            parent_id="RUN",
            task_type="investigation",
            status="ok",
            ended_at="2026-09-27T00:00:01Z",
        )
    )
    ledger.add_snapshots((Snapshot("CHILD", "end", "cart.py", digest),))
    reports = RunReports(container.state_workspace())
    reports.save_report("CHILD", "cart.py:1 has a later-role finding.\n")
    if sandbox:
        container.state_workspace().write_text(
            ".cuanta/trials/RUN/report.md",
            "## ANALYST\ncart.py:1 stores the cart.\n## TESTER\ncart.py:1 has a later-role finding.\n",
        )
    container.learn_run("RUN")
    meta = reports.meta("RUN")
    assert meta is not None
    learning = meta["index_learning"]
    assert isinstance(learning, dict) and learning["findings_saved"] == 2
    container.learn_run("RUN")
    assert reports.meta("RUN") == meta
    container.close()
