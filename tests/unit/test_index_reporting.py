from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.index_reporting import run_index_metrics
from cuanta.application.results import ResultQuery
from cuanta.application.run_reports import RunReports
from cuanta.application.spectrum import Selection, SpectrumQuery
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.spectrum import analyze


def test_analysis_exposes_index_without_adding_tool_bytes_to_api_totals() -> None:
    events = [
        LedgerEvent(kind="api_request", input_tokens=120, output_tokens=5, cost_usd=0.01),
        LedgerEvent(kind="tool_result", tool_name="Read", tool_result_bytes=400),
        LedgerEvent(
            kind="index_call",
            source="cuanta_mcp",
            tool_name="mcp__cuanta__page",
            tool_result_bytes=800,
        ),
    ]
    report = analyze("run", events)
    assert report.index.index_hit_rate == 0.5
    assert report.index.exploration_tokens_estimate == 300
    assert report.totals.total == 125
    assert report.totals.cost_usd == 0.01


def test_shared_snapshot_counts_are_not_summed_and_parent_guard_manifest_wins() -> None:
    runs = [Run("parent", "cross"), Run("role", "cross", parent_id="parent")]
    meta: dict[str, dict[str, object]] = {
        "parent": {
            "index_learning": {"findings_saved": 2, "stale_facts": 3},
            "guard_violations": [],
            "out_of_plan_edits": ["extra.py"],
        },
        "role": {
            "index_learning": {"findings_saved": 2, "stale_facts": 3},
            "guard_violations": ["guard.py"],
            "out_of_plan_edits": ["role.py"],
        },
    }
    metrics = run_index_metrics([], runs * 2, meta.get)
    assert metrics.findings_saved == 2
    assert metrics.stale_facts == 3
    assert metrics.guard_violations == ()
    assert metrics.out_of_plan_edits == ("extra.py",)


def test_missing_parent_metadata_uses_child_reports_and_invalid_counts_are_zero() -> None:
    runs = [
        Run("parent", "cross"),
        Run("one", "cross", parent_id="parent"),
        Run("two", "cross", parent_id="parent"),
    ]
    meta: dict[str, dict[str, object]] = {
        "one": {
            "index_learning": {"findings_saved": 2, "stale_facts": -1},
            "guard_violations": ["guard.py"],
        },
        "two": {
            "index_learning": {"findings_saved": True, "stale_facts": "4"},
            "out_of_plan_edits": ["extra.py"],
        },
    }
    metrics = run_index_metrics([], runs, meta.get)
    assert metrics.findings_saved == 2
    assert metrics.stale_facts == 0
    assert metrics.guard_violations == ("guard.py",)
    assert metrics.out_of_plan_edits == ("extra.py",)


def test_queries_aggregate_cross_role_exploration_with_unique_events_and_keep_usage_semantics(
    tmp_path: Path,
) -> None:
    ledger = MemoryLedger()
    parent = Run("parent", "cross", started_at="2026-01-01T00:00:00Z")
    child = Run("child", "cross", parent_id="parent", started_at="2026-01-01T00:00:01Z")
    ledger.add_run(parent)
    ledger.add_run(child)
    ledger.add_events(
        [
            LedgerEvent(
                run_id="parent",
                kind="api_request",
                input_tokens=100,
                output_tokens=5,
                cost_usd=0.01,
            ),
            LedgerEvent(
                run_id="parent",
                kind="tool_result",
                tool_name="Read",
                tool_use_id="read:1",
                tool_result_bytes=400,
            ),
            LedgerEvent(
                run_id="child", kind="api_request", input_tokens=200, output_tokens=5, cost_usd=0.02
            ),
            LedgerEvent(
                run_id="child",
                kind="index_call",
                source="cuanta_mcp",
                tool_name="mcp__cuanta__page",
                tool_use_id="nonce:1",
                tool_result_bytes=800,
            ),
        ]
    )
    reports = RunReports(LocalWorkspace(tmp_path))
    reports.save_meta(
        "parent",
        {
            "index_learning": {"findings_saved": 4, "stale_facts": 2},
            "guard_violations": ["guard.py"],
            "out_of_plan_edits": ["extra.py"],
        },
    )
    reports.save_meta("child", {"index_learning": {"findings_saved": 4, "stale_facts": 2}})
    result = ResultQuery(LocalWorkspace(tmp_path), ledger, date.today).load("parent")
    spectrum = SpectrumQuery(ledger, metadata=reports.meta).run(Selection(run="parent"))
    assert result is not None
    assert result.index == spectrum.report.index
    assert result.index.index_calls == 1
    assert result.index.raw_reads == 1
    assert result.index.exploration_tokens_estimate == 300
    assert result.index.findings_saved == 4
    assert result.index.stale_facts == 2
    assert spectrum.report.totals.total == 105
    assert result.anatomy == spectrum.report.anatomy
    assert result.anatomy.totals.total == 310
    assert result.anatomy.totals.cost_usd == pytest.approx(0.03)
    assert len(spectrum.events) == 2


def test_session_selection_can_load_metadata_without_a_selected_run() -> None:
    ledger = MemoryLedger()
    ledger.add_run(Run("run", "mandate"))
    ledger.add_events(
        [
            LedgerEvent(
                run_id="run",
                session_id="session",
                kind="tool_result",
                tool_name="Read",
                tool_result_bytes=400,
            )
        ]
    )
    meta: dict[str, dict[str, object]] = {"run": {"index_learning": {"findings_saved": 1}}}
    result = SpectrumQuery(ledger, metadata=meta.get).run(Selection(session="session"))
    assert result.report.index.findings_saved == 1
    assert result.report.index.raw_reads == 1
