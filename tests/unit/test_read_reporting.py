from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.read_reporting import ReadReporting
from cuanta.application.results import ResultQuery
from cuanta.application.run_reports import RunReports
from cuanta.application.spectrum import Selection, SpectrumQuery
from cuanta.domain.ledger import LedgerEvent, Run, Snapshot
from cuanta.domain.read_efficiency import INVESTIGATION_FORMULA
from cuanta.domain.sandbox import SANDBOX_MODE


def observed(ledger: MemoryLedger, run: str, paths: tuple[str, ...]) -> None:
    ledger.add_events(
        [
            LedgerEvent(run_id=run, kind="api_request", input_tokens=100, cost_usd=0.1),
            *(
                LedgerEvent(
                    run_id=run,
                    kind="tool_result",
                    tool_name="Read",
                    file_path=path,
                    tool_result_bytes=40,
                    success=True,
                )
                for path in paths
            ),
        ]
    )


def selection(tmp_path: Path, ledger: MemoryLedger, run_id: str) -> SpectrumQuery:
    workspace = LocalWorkspace(tmp_path)
    reports = RunReports(workspace)
    reads = ReadReporting(workspace, ledger)
    return SpectrumQuery(ledger, metadata=reports.meta, reports=reports.report, roots=reads.roots)


def test_result_and_spectrum_compute_the_same_observed_investigation_ratio(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="investigation", status="ok"))
    observed(ledger, "R", ("src/a.py", "src/c.py"))
    ledger.add_events(
        [
            LedgerEvent(
                run_id="R",
                kind="index_call",
                source="cuanta_mcp",
                tool_name="mcp__cuanta__page",
                file_path="src/b.py",
                success=True,
            )
        ]
    )
    workspace = LocalWorkspace(tmp_path)
    reports = RunReports(workspace)
    reports.save_meta("R", {"shape": "single"})
    reports.save_report("R", "src/a.py:4 and src/a.py:8")
    result = ResultQuery(workspace, ledger, date.today).load("R")
    spectrum = selection(tmp_path, ledger, "R").run(Selection(run="R"))
    assert result is not None
    assert result.read_efficiency == spectrum.report.read_efficiency
    assert result.read_efficiency.value == pytest.approx(1 / 3)
    assert spectrum.report.utilization.label == "file utilization v2"
    assert spectrum.report.utilization.formula == INVESTIGATION_FORMULA
    assert spectrum.report.utilization.read_files == 3
    assert spectrum.report.utilization.useful_files == 1


def test_pipeline_missing_report_keeps_the_original_token_utilization(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="bug"))
    observed(ledger, "R", ("src/a.py",))
    ledger.add_snapshots(
        (Snapshot("R", "start", "src/a.py", "before"), Snapshot("R", "end", "src/a.py", "after"))
    )
    RunReports(LocalWorkspace(tmp_path)).save_meta("R", {"shape": "pipeline"})
    report = selection(tmp_path, ledger, "R").run(Selection(run="R")).report
    assert report.utilization.label == "heuristic v1"
    assert report.utilization.value == pytest.approx(0.1)
    assert report.utilization.formula == "useful tool tokens / total API tokens"
    result = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today).load("R")
    assert result is not None
    assert (
        result.read_efficiency.edited_files == report.read_efficiency.edited_files == ("src/a.py",)
    )


def test_explicit_single_missing_report_is_unknown_file_utilization(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate", task_type="investigation"))
    observed(ledger, "R", ("src/a.py",))
    reports = RunReports(LocalWorkspace(tmp_path))
    reports.save_meta("R", {"shape": "single"})
    report = selection(tmp_path, ledger, "R").run(Selection(run="R")).report
    assert report.utilization.label == "file utilization v2"
    assert report.utilization.value is None
    assert report.read_efficiency.why == "missing_report"
    reports.save_report("R", "")
    report = selection(tmp_path, ledger, "R").run(Selection(run="R")).report
    assert report.utilization.value == 0


def test_historical_metadata_only_investigation_uses_the_citation_formula(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("R", "mandate"))
    observed(ledger, "R", ("src/a.py",))
    workspace = LocalWorkspace(tmp_path)
    reports = RunReports(workspace)
    reports.save_meta(
        "R", {"shape": "single", "task_type": "investigation", "changed_files": ["src/a.py"]}
    )
    reports.save_report("R", "No citations in this answer.")
    result = ResultQuery(workspace, ledger, date.today).load("R")
    report = selection(tmp_path, ledger, "R").run(Selection(run="R")).report
    assert result is not None
    assert result.read_efficiency == report.read_efficiency
    assert report.utilization.value == 0
    assert report.utilization.formula == INVESTIGATION_FORMULA


def test_cross_roles_are_included_once_without_changing_legacy_usage_scope(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("P", "cross", task_type="investigation", started_at="2026-01-01T00:00:00Z"))
    ledger.add_run(Run("C", "cross", parent_id="P", started_at="2026-01-01T00:00:01Z"))
    observed(ledger, "P", ("src/a.py",))
    observed(ledger, "C", ("src/b.py",))
    workspace = LocalWorkspace(tmp_path)
    reports = RunReports(workspace)
    reports.save_meta("P", {"shape": "pipeline"})
    reports.save_report("P", "src/b.py:8")
    report = selection(tmp_path, ledger, "P").run(Selection(run="P")).report
    result = ResultQuery(workspace, ledger, date.today).load("P")
    assert result is not None
    assert result.read_efficiency == report.read_efficiency
    assert report.read_efficiency.value == 0.5
    assert report.utilization.label == "file utilization v2"
    assert report.totals.total == 100


def test_internal_child_citations_do_not_improve_the_final_answer_efficiency(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("P", "cross", task_type="investigation", started_at="2026-01-01T00:00:00Z"))
    ledger.add_run(Run("C", "cross", parent_id="P", started_at="2026-01-01T00:00:01Z"))
    observed(ledger, "C", ("src/a.py",))
    workspace = LocalWorkspace(tmp_path)
    reports = RunReports(workspace)
    reports.save_meta("P", {"shape": "pipeline"})
    reports.save_report("P", "No citations in the delivered answer.")
    reports.save_report("C", "Internal scratch finding src/a.py:7")
    result = ResultQuery(workspace, ledger, date.today).load("P")
    report = selection(tmp_path, ledger, "P").run(Selection(run="P")).report
    assert result is not None
    assert result.read_efficiency == report.read_efficiency
    assert report.read_efficiency.read_files == ("src/a.py",)
    assert report.read_efficiency.cited_files == ()
    assert report.utilization.value == 0


@pytest.mark.parametrize("owner", ["R", "P"])
def test_saved_removed_copy_aliases_normalize_child_source_reads(
    tmp_path: Path, owner: str
) -> None:
    ledger = MemoryLedger()
    parent = Run("P", "cross", mode=SANDBOX_MODE)
    run = Run("R", "cross", parent_id="P", mode=SANDBOX_MODE, task_type="investigation")
    ledger.add_run(parent)
    ledger.add_run(run)
    observed(ledger, "R", ("C:/sandbox/deleted/src/a.py",))
    workspace = LocalWorkspace(tmp_path)
    workspace.write_text(
        f".cuanta/trials/{owner}/trial.json",
        json.dumps({"run_id": owner, "copy_root": "C:/sandbox/deleted", "changes": []}),
    )
    reports = RunReports(workspace)
    reports.save_meta("R", {"shape": "single"})
    reports.save_report("R", "src/a.py:8")
    result = ResultQuery(workspace, ledger, date.today).load("R")
    assert result is not None
    assert result.read_efficiency.value == 1
    assert result.read_efficiency.read_files == ("src/a.py",)
    assert "C:/sandbox/deleted" in ReadReporting(workspace, ledger).roots(run)


@pytest.mark.parametrize(
    ("copy", "identity"),
    [
        ("C:/sandbox/../external", "R"),
        ("C:/sandbox/copy\0", "R"),
        ("C:/sandbox/copy", "other"),
        ("relative/copy", "R"),
    ],
)
def test_invalid_or_unrelated_copy_aliases_are_not_trusted(
    tmp_path: Path, copy: str, identity: str
) -> None:
    ledger = MemoryLedger()
    run = Run("R", "mandate", mode=SANDBOX_MODE)
    ledger.add_run(run)
    workspace = LocalWorkspace(tmp_path)
    workspace.write_text(
        ".cuanta/trials/R/trial.json",
        json.dumps({"run_id": identity, "copy_root": copy, "changes": []}),
    )
    assert ReadReporting(workspace, ledger).roots(run) == (str(workspace.root),)
    assert ReadReporting(workspace, ledger).roots(replace(run, mode="")) == (str(workspace.root),)
