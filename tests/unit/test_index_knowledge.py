from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.index_knowledge import LocalIndexKnowledge, _relative
from cuanta.domain.code_index import IndexedFile
from cuanta.domain.index_facts import report_facts, revalidate_fact
from cuanta.domain.ledger import (
    LedgerEvent,
    RoutingDecision,
    Run,
    SignatureRecord,
    Snapshot,
    TestRunRecord,
)
from cuanta.ports.code_index import IndexKnowledge
from cuanta.ports.ledger import EventQuery


def _write(root: Path, relative: str, text: str) -> bytes:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode()
    path.write_bytes(payload)
    return payload


def _json(root: Path, relative: str, value: dict[str, object]) -> None:
    _write(root, relative, json.dumps(value))


def _snapshot(root: Path, ledger: MemoryLedger, run_id: str, path: str, text: str) -> str:
    digest = sha256(text.encode()).hexdigest()
    _write(root, ".cuanta/blobs/" + digest, text)
    ledger.add_snapshots((Snapshot(run_id, "start", path, digest),))
    return digest


def test_reports_without_ledger_are_zero_write_and_have_no_invented_source(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", "changed = True\n")
    _write(tmp_path, ".cuanta/runs/R/report.md", "src/a.py:1 used to be false\n")
    _write(tmp_path, "docs/investigations/a.md", "src/a.py:1 used to be false\n")
    adapter: IndexKnowledge = LocalIndexKnowledge(tmp_path, tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    reports = adapter.reports()
    assert len(reports) == 1
    assert reports[0].sources == ()
    assert reports[0].provenance == "report:.cuanta/runs/R/report.md"
    assert adapter.history() == ()
    assert not (tmp_path / ".cuanta/ledger.db").exists()
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_all_run_trial_and_nested_export_reports_are_deterministic(tmp_path: Path) -> None:
    project, state = tmp_path / "project", tmp_path / "state"
    for root, relative in (
        (state, ".cuanta/runs/R/report.md"),
        (state, ".cuanta/runs/R/run-report.md"),
        (state, ".cuanta/trials/R/report.md"),
        (project, "docs/investigations/nested/first.md"),
        (state, "docs/runs/last.md"),
    ):
        _write(root, relative, relative + " src/a.py:1\n")
    adapter = LocalIndexKnowledge(project, state)
    reports = adapter.reports()
    assert len(reports) == 5
    assert reports == adapter.reports()
    assert all(report.provenance.startswith("report:") for report in reports)
    assert all(report.sources == () for report in reports)


def test_snapshot_before_blob_is_immutable_proof_for_read_only_report(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="investigation"))
    original = "first = True\nsecond = False\n"
    _snapshot(tmp_path, ledger, "R", "src/a.py", original)
    _write(tmp_path, "src/a.py", "first = False\nsecond = False\n")
    _write(tmp_path, ".cuanta/runs/R/report.md", "src/a.py:1-2 has a problem\n")
    before_runs, before_events = ledger.runs(), ledger.events(EventQuery())
    reports = LocalIndexKnowledge(tmp_path, tmp_path, ledger).reports()
    assert reports[0].sources == (("src/a.py", original),)
    assert ledger.runs() == before_runs
    assert ledger.events(EventQuery()) == before_events


@pytest.mark.parametrize("corruption", ["different", "binary", "missing", "escape"])
def test_unproved_snapshot_sources_are_absent(tmp_path: Path, corruption: str) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="investigation"))
    digest = _snapshot(tmp_path, ledger, "R", "src/a.py", "value = True\n")
    blob = tmp_path / ".cuanta/blobs" / digest
    if corruption == "missing":
        blob.unlink()
    elif corruption == "escape":
        ledger.add_snapshots((Snapshot("R", "start", "src/a.py", "../../outside"),))
    else:
        blob.write_bytes(b"\0binary" if corruption == "binary" else b"value = False\n")
    _write(tmp_path, "src/a.py", "value = True\n")
    _write(tmp_path, ".cuanta/runs/R/report.md", "src/a.py:1 finding\n")
    assert LocalIndexKnowledge(tmp_path, tmp_path, ledger).reports()[0].sources == ()


def test_trial_base_only_proves_recorded_changed_paths_without_ledger(tmp_path: Path) -> None:
    original = "original = True\n"
    digest = sha256(original.encode()).hexdigest()
    _write(tmp_path, ".cuanta/trials/R/base/src/a.py", original)
    _write(tmp_path, ".cuanta/trials/R/base/src/b.py", original)
    _write(tmp_path, ".cuanta/trials/R/report.md", "src/a.py:1 and src/b.py:1\n")
    _json(
        tmp_path,
        ".cuanta/trials/R/trial.json",
        {
            "run_id": "R",
            "changes": [{"path": "src/a.py", "before": digest, "after": digest}],
        },
    )
    report = LocalIndexKnowledge(tmp_path, tmp_path).reports()[0]
    assert report.sources == (("src/a.py", original),)
    _write(tmp_path, ".cuanta/trials/R/base/src/a.py", "modified after capture\n")
    assert LocalIndexKnowledge(tmp_path, tmp_path).reports()[0].sources == ()


def test_identical_reports_with_conflicting_historical_sources_do_not_claim_proof(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    for run_id, text in (("A", "old = True\n"), ("B", "old = False\n")):
        ledger.add_run(Run(run_id, "mandate", task_type="investigation"))
        _snapshot(tmp_path, ledger, run_id, "src/a.py", text)
        _write(tmp_path, f".cuanta/runs/{run_id}/report.md", "src/a.py:1 finding\n")
    reports = LocalIndexKnowledge(tmp_path, tmp_path, ledger).reports()
    assert len(reports) == 1
    assert reports[0].sources == ()


def test_report_and_blob_links_are_not_followed(tmp_path: Path) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    ledger = MemoryLedger()
    ledger.add_run(Run("valid", "mandate", task_type="investigation"))
    digest = sha256(b"old = True\n").hexdigest()
    _write(outside, "report.md", "src/a.py:1 finding\n")
    _write(outside, "original", "old = True\n")
    _write(project, ".cuanta/runs/valid/report.md", "src/a.py:1 valid\n")
    (project / ".cuanta/runs/linked").symlink_to(outside, target_is_directory=True)
    (project / ".cuanta/runs/valid/run-report.md").symlink_to(outside / "report.md")
    (project / ".cuanta/blobs").mkdir()
    (project / ".cuanta/blobs" / digest).symlink_to(outside / "original")
    ledger.add_snapshots((Snapshot("valid", "start", "src/a.py", digest),))
    reports = LocalIndexKnowledge(project, project, ledger).reports()
    assert len(reports) == 1
    assert reports[0].sources == ()
    assert (outside / "original").read_bytes() == b"old = True\n"
    assert (outside / "report.md").read_text() == "src/a.py:1 finding\n"


def test_linked_report_root_does_not_escape_or_fail(tmp_path: Path) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    _write(outside, "runs/R/report.md", "src/a.py:1 outside\n")
    (project / ".cuanta").symlink_to(outside, target_is_directory=True)
    adapter = LocalIndexKnowledge(project, project, MemoryLedger())
    assert adapter.reports() == ()
    assert adapter.history() == ()
    _write(project, "docs/investigations/a.md", "src/a.py:1 documented\n")
    assert len(adapter.reports()) == 1


@pytest.mark.parametrize("metadata", ["not JSON", "null", "[]", '{"run_id":"other"}'])
def test_malformed_trial_metadata_cannot_supply_source_or_copy_root(
    tmp_path: Path, metadata: str
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", mode="sandbox"))
    _write(tmp_path, ".cuanta/trials/R/trial.json", metadata)
    _write(tmp_path, ".cuanta/trials/R/report.md", "src/a.py:1 finding\n")
    adapter = LocalIndexKnowledge(tmp_path, tmp_path, ledger)
    assert adapter.reports()[0].sources == ()
    assert adapter.history()[0].action == "cite"


def test_platform_path_normalization_preserves_case_rules_and_rejects_prefix_lookalikes() -> None:
    assert _relative("C:\\Work\\repo\\src\\a.py", ("c:/work/REPO",)) == "src/a.py"
    assert _relative("C:/work/repository/src/a.py", ("C:/work/repo",)) == ""
    assert _relative("/work/REPO/src/a.py", ("/work/repo",)) == ""
    assert _relative("/work/repo/src/../../a.py", ("/work/repo",)) == ""
    assert _relative("src\\a.py", ()) == "src/a.py"


def test_history_normalizes_original_and_recorded_copy_paths_but_not_external_basename(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    copy = tmp_path / "sandbox" / "project"
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", mode="sandbox", task_type="fix", outcome="accepted"))
    _json(project, ".cuanta/trials/R/trial.json", {"run_id": "R", "copy_root": str(copy)})
    ledger.add_events(
        (
            LedgerEvent(run_id="R", tool_name="Read", file_path=str(copy / "src/a.py")),
            LedgerEvent(run_id="R", tool_name="Edit", file_path=str(project / "src/a.py")),
            LedgerEvent(run_id="R", tool_name="Read", file_path=str(tmp_path / "outside/a.py")),
            LedgerEvent(run_id="R", tool_name="Read", file_path="../outside.py"),
            LedgerEvent(run_id="R", tool_name="Read", file_path="src/../../outside.py"),
            LedgerEvent(run_id="R", tool_name="Read", file_path="src/failed.py", success=False),
        )
    )
    rows = LocalIndexKnowledge(project, project, ledger).history()
    assert {(row.path, row.action) for row in rows} == {
        ("src/a.py", "read"),
        ("src/a.py", "edit"),
        ("src/a.py", "outcome"),
    }
    _json(project, ".cuanta/trials/R/trial.json", {"run_id": "another", "copy_root": str(copy)})
    rows = LocalIndexKnowledge(project, project, ledger).history()
    assert {row.action for row in rows} == {"edit", "outcome"}


def test_pipeline_history_deduplicates_root_actions_inherits_outcome_and_retries(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(
        Run("root", "cross", task_type="feature", outcome="accepted", outcome_at="2026-01-01")
    )
    ledger.add_run(Run("child", "cross", parent_id="root", task_type="feature"))
    for run_id in ("root", "child"):
        ledger.add_events(
            (
                LedgerEvent(run_id=run_id, tool_name="Read", file_path="src/a.py", ts="2026-01-01"),
                LedgerEvent(run_id=run_id, tool_name="Read", file_path="src/a.py", ts="2026-01-02"),
            )
        )
        ledger.add_routing_decision(
            RoutingDecision(
                run_id, "feature", "senior", "normal", "claude", "model", "reason", retries=2
            )
        )
        _write(tmp_path, f".cuanta/runs/{run_id}/report.md", "src/a.py:1 finding\n")
    _snapshot(tmp_path, ledger, "root", "src/untouched.py", "untouched = True\n")
    rows = LocalIndexKnowledge(tmp_path, tmp_path, ledger).history()
    assert {row.action for row in rows} == {"read", "cite", "outcome"}
    assert len(rows) == 3
    assert all(row.path == "src/a.py" and row.run_id == "root" for row in rows)
    assert all(row.outcome == "accepted" and row.retries == 2 for row in rows)
    assert next(row for row in rows if row.action == "read").at == "2026-01-02"


def test_failure_signatures_and_actual_trial_edits_are_read_without_ledger_mutation(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="fix", outcome="rejected"))
    record = TestRunRecord(
        "T", "R", "pytest", "pytest", "failed", 0, 1, 0, 0, 1.0, "C", "2026-01-01"
    )
    signatures = (
        SignatureRecord("T", "sig", "TypeError", "private error", "test_a", "src/a.py:12", 1),
        SignatureRecord(
            "T",
            "outside",
            "TypeError",
            "private error",
            "test_b",
            str(tmp_path.parent / "outside.py") + ":1",
            1,
        ),
    )
    ledger.add_test_run(record, signatures)
    _json(
        tmp_path, ".cuanta/trials/R/trial.json", {"run_id": "R", "changes": [{"path": "src/a.py"}]}
    )
    before = ledger.runs(), ledger.events(EventQuery()), ledger.test_runs(), ledger.signatures("T")
    rows = LocalIndexKnowledge(tmp_path, tmp_path, ledger).history()
    assert {(row.path, row.action, row.signature) for row in rows} == {
        ("src/a.py", "failure", "sig"),
        ("src/a.py", "edit", ""),
        ("src/a.py", "outcome", ""),
    }
    assert before == (
        ledger.runs(),
        ledger.events(EventQuery()),
        ledger.test_runs(),
        ledger.signatures("T"),
    )


def test_child_events_use_only_the_validated_root_trial_copy_path(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    copy = tmp_path / "sandbox" / "project"
    ledger.add_run(Run("root", "cross", mode="sandbox", task_type="fix", outcome="accepted"))
    ledger.add_run(Run("child", "cross", mode="sandbox", parent_id="root"))
    _json(tmp_path, ".cuanta/trials/root/trial.json", {"run_id": "root", "copy_root": str(copy)})
    ledger.add_events(
        (LedgerEvent(run_id="child", tool_name="Edit", file_path=str(copy / "src/a.py")),)
    )
    rows = LocalIndexKnowledge(tmp_path, tmp_path, ledger).history()
    assert {(row.run_id, row.path, row.action) for row in rows} == {
        ("root", "src/a.py", "edit"),
        ("root", "src/a.py", "outcome"),
    }


def test_unapplied_trial_report_uses_verified_after_image_and_remains_stale_on_before_tree(
    tmp_path: Path,
) -> None:
    before, after = "def old():\n    pass\n", "def new():\n    return True\n"
    before_hash, after_hash = (
        sha256(before.encode()).hexdigest(),
        sha256(after.encode()).hexdigest(),
    )
    _write(tmp_path, "src/a.py", before)
    _write(tmp_path, ".cuanta/trials/R/base/src/a.py", before)
    _write(tmp_path, ".cuanta/trials/R/files/src/a.py", after)
    _write(tmp_path, ".cuanta/trials/R/report.md", "src\\a.py:1–2 now exposes new\n")
    _json(
        tmp_path,
        ".cuanta/trials/R/trial.json",
        {
            "run_id": "R",
            "changes": [{"path": "src/a.py", "before": before_hash, "after": after_hash}],
        },
    )
    report = LocalIndexKnowledge(tmp_path, tmp_path).reports()[0]
    assert report.sources == (("src/a.py", after),)
    (fact,) = report_facts(report)
    assert (fact.path, fact.line, fact.end_line) == ("src/a.py", 1, 2)
    before_file = IndexedFile("src/a.py", before_hash, "python", len(before.encode()))
    after_file = IndexedFile("src/a.py", after_hash, "python", len(after.encode()))
    assert revalidate_fact(fact, before_file, before).stale
    assert not revalidate_fact(fact, after_file, after).stale
    _write(tmp_path, ".cuanta/trials/R/files/src/a.py", "corrupted after image\n")
    assert LocalIndexKnowledge(tmp_path, tmp_path).reports()[0].sources == ()


def test_recorded_end_digest_proves_current_source_but_later_drift_does_not_use_start(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="fix"))
    before, after = "old = True\n", "new = True\n"
    _snapshot(tmp_path, ledger, "R", "src/a.py", before)
    ledger.add_snapshots((Snapshot("R", "end", "src/a.py", sha256(after.encode()).hexdigest()),))
    _write(tmp_path, "src/a.py", after)
    _write(tmp_path, ".cuanta/runs/R/report.md", "src/a.py:1 now defines new\n")
    adapter = LocalIndexKnowledge(tmp_path, tmp_path, ledger)
    assert adapter.reports()[0].sources == (("src/a.py", after),)
    _write(tmp_path, "src/a.py", before)
    assert adapter.reports()[0].sources == ()


def test_recorded_end_blob_is_used_when_current_file_has_changed(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="feature"))
    after = "def added():\n    pass\n"
    digest = sha256(after.encode()).hexdigest()
    ledger.add_snapshots((Snapshot("R", "end", "src/a.py", digest),))
    _write(tmp_path, ".cuanta/blobs/" + digest, after)
    _write(tmp_path, "src/a.py", "later = True\n")
    _write(tmp_path, ".cuanta/runs/R/report.md", "src/a.py:1-2 added\n")
    assert LocalIndexKnowledge(tmp_path, tmp_path, ledger).reports()[0].sources == (
        ("src/a.py", after),
    )


@pytest.mark.parametrize("task_type", ["fix", "feature", ""])
def test_editing_or_unknown_run_start_source_never_proves_end_report(
    tmp_path: Path,
    task_type: str,
) -> None:
    ledger = MemoryLedger()
    if task_type:
        ledger.add_run(Run("R", "mandate", task_type=task_type))
    _snapshot(tmp_path, ledger, "R", "src/a.py", "unchanged now = True\n")
    _write(tmp_path, "src/a.py", "unchanged now = True\n")
    _write(tmp_path, ".cuanta/runs/R/report.md", "src/a.py:1 supposedly added\n")
    _json(tmp_path, ".cuanta/runs/R/run.json", {"task_type": "investigation"})
    assert LocalIndexKnowledge(tmp_path, tmp_path, ledger).reports()[0].sources == ()


@pytest.mark.parametrize(
    "contradiction", ["missing-end-path", "different-end", "deleted-trial", "changed-meta"]
)
def test_readonly_start_is_not_reused_when_recorded_report_end_contradicts_it(
    tmp_path: Path,
    contradiction: str,
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="investigation"))
    before = "original = True\n"
    digest = _snapshot(tmp_path, ledger, "R", "src/a.py", before)
    _write(tmp_path, ".cuanta/runs/R/report.md", "src/a.py:1 old finding\n")
    if contradiction == "missing-end-path":
        ledger.add_snapshots((Snapshot("R", "end", "src/other.py", digest),))
    elif contradiction == "different-end":
        ledger.add_snapshots((Snapshot("R", "end", "src/a.py", "f" * 64),))
    elif contradiction == "deleted-trial":
        _json(
            tmp_path,
            ".cuanta/trials/R/trial.json",
            {
                "run_id": "R",
                "changes": [{"path": "src/a.py", "before": digest, "after": None}],
            },
        )
    else:
        _json(tmp_path, ".cuanta/runs/R/run.json", {"changed_files": ["src\\a.py"]})
    assert LocalIndexKnowledge(tmp_path, tmp_path, ledger).reports()[0].sources == ()


def test_windows_range_citations_use_the_fact_parser_full_path_for_history(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="investigation"))
    original = "first = True\nsecond = False\n"
    _snapshot(tmp_path, ledger, "R", "src/a.py", original)
    _write(tmp_path, ".cuanta/runs/R/report.md", "src\\a.py:1—2 finding\n")
    adapter = LocalIndexKnowledge(tmp_path, tmp_path, ledger)
    assert adapter.reports()[0].sources == (("src/a.py", original),)
    (row,) = adapter.history()
    assert (row.path, row.action) == ("src/a.py", "cite")


def test_trial_end_and_ledger_end_conflict_is_not_resolved_by_choosing_a_version(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="feature"))
    trial_after, end_after = "trial = True\n", "different_end = True\n"
    ledger.add_snapshots(
        (Snapshot("R", "end", "src/a.py", sha256(end_after.encode()).hexdigest()),)
    )
    _write(tmp_path, "src/a.py", end_after)
    _write(tmp_path, ".cuanta/trials/R/files/src/a.py", trial_after)
    _write(tmp_path, ".cuanta/trials/R/report.md", "src/a.py:1 finding\n")
    _json(
        tmp_path,
        ".cuanta/trials/R/trial.json",
        {
            "run_id": "R",
            "changes": [{"path": "src/a.py", "after": sha256(trial_after.encode()).hexdigest()}],
        },
    )
    assert LocalIndexKnowledge(tmp_path, tmp_path, ledger).reports()[0].sources == ()
