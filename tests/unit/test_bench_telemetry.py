from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.bench import Attempt, BenchExecutor, BenchRunner, metrics_from_json
from cuanta.domain.anatomy import (
    AgentAnatomy,
    AnatomyReport,
    Phase,
    PhaseSummary,
    PhaseTotals,
    UsagePhase,
)
from cuanta.domain.answer_check import AnswerSpec
from cuanta.domain.bench import Condition, report_markdown, summarize
from cuanta.domain.read_efficiency import INVESTIGATION_FORMULA, ReadEfficiency
from tests.unit.test_bench import TASK, Sandbox, _attempt, _meta, _metrics

START = PhaseTotals(fresh_input=10, output=2, requests=1, cost_usd=0.1)
HANDOFF = PhaseTotals(cache_write=20, output=3, requests=1, cost_usd=0.2)
TOTALS = PhaseTotals(fresh_input=10, cache_write=20, output=5, requests=2, cost_usd=0.3)
PHASES = (PhaseSummary(Phase.START, START), PhaseSummary(Phase.HANDOFF, HANDOFF))
ANATOMY = AnatomyReport(
    totals=TOTALS,
    phases=PHASES,
    agents=(AgentAnatomy("main", PHASES, TOTALS),),
    events=(
        UsagePhase(1, "r1", "s1", "main", "2026-01-01T00:00:00Z", Phase.START, 12, 0.1, START),
    ),
)
EFFICIENCY = ReadEfficiency(
    value=0.5,
    read_files=("a.py", "b.py"),
    cited_files=("a.py",),
    useful_files=("a.py",),
    formula=INVESTIGATION_FORMULA,
    available=True,
    why="",
)


def test_new_nested_metrics_round_trip_without_storing_delivered_answer(tmp_path: Path) -> None:
    attempt = replace(
        _attempt(0.3),
        answer="private delivered text",
        shape="pipeline",
        pack="off",
        depth="deep",
        anatomy=ANATOMY,
        read_efficiency=EFFICIENCY,
    )
    metrics = BenchExecutor(Sandbox(), lambda *_: attempt, lambda: 0.0)(
        TASK, Condition.CUANTA, 1, 1.0
    )
    runner = BenchRunner(lambda *_: metrics, LocalWorkspace(tmp_path))
    meta = replace(_meta(), shape="pipeline", pack="off", depth="deep")
    runner.save(meta, [metrics])
    loaded = runner.load()
    assert loaded is not None and loaded.meta == meta and loaded.metrics == (metrics,)
    assert loaded.metrics[0].anatomy.events[0].phase is Phase.START
    raw = (tmp_path / loaded.folder / "results.json").read_text(encoding="utf-8")
    assert "private delivered text" not in raw and '"answer"' not in raw
    assert loaded.metrics[0].anatomy.totals.total == 35


def test_missing_historical_telemetry_stays_unknown_and_preserves_old_options() -> None:
    old = asdict(_metrics(Condition.CUANTA, True, 10, 0.1))
    for name in ("anatomy", "read_efficiency", "shape", "pack", "depth"):
        old.pop(name)
    restored = metrics_from_json(json.loads(json.dumps(old)))
    assert restored.anatomy == AnatomyReport()
    assert restored.read_efficiency == ReadEfficiency()
    assert (restored.shape, restored.pack, restored.depth) == ("", "on", "")


def test_malformed_new_metrics_fail_closed_without_invalid_phases_or_ratios() -> None:
    raw = json.loads(json.dumps(asdict(_metrics(Condition.CUANTA, True, 10, 0.1))))
    raw.update(
        shape="unsupported",
        pack="bad",
        depth="bad",
        anatomy={
            "totals": {"requests": True, "fresh_input": -2},
            "phases": [{"phase": "unknown"}],
            "events": [{"phase": 3}],
        },
        read_efficiency={"value": 2.0, "available": True, "read_files": [False, "a.py"]},
    )
    restored = metrics_from_json(raw)
    assert restored.anatomy.totals.requests == 0 and restored.anatomy.totals.fresh_input == 0
    assert not restored.anatomy.phases and not restored.anatomy.events
    assert restored.read_efficiency.value is None and not restored.read_efficiency.available
    assert restored.read_efficiency.read_files == ("a.py",)
    assert (restored.shape, restored.pack, restored.depth) == ("", "on", "")


@pytest.mark.parametrize("condition", tuple(Condition))
def test_investigation_uses_delivered_answer_and_rejects_observed_source_mutation(
    condition: Condition,
) -> None:
    class AnswerSandbox(Sandbox):
        def accept(self, task: object, root: str, report: str | None = None) -> tuple[bool, str]:
            self.accepted.append(report or "missing")
            return True, ""

    task = replace(
        TASK,
        request=replace(TASK.request, type="investigation"),
        answer=AnswerSpec(("truth",), (r"a.py:1",)),
    )
    sandbox = AnswerSandbox()
    attempt = replace(_attempt(0.1), answer="truth a.py:1", out_of_plan_edits=("a.py",))
    result = BenchExecutor(sandbox, lambda *_: attempt, lambda: 0.0)(task, condition, 1, 1.0)
    assert sandbox.accepted == ["truth a.py:1"]
    assert not result.accepted and "Investigation changed source paths: a.py" in result.error
    assert len(sandbox.discarded) == 1


def test_report_uses_covered_runs_without_converting_missing_telemetry_to_zero() -> None:
    known = replace(
        _metrics(Condition.CUANTA, True, 35, 0.3), anatomy=ANATOMY, read_efficiency=EFFICIENCY
    )
    unknown = _metrics(Condition.CUANTA, False, 900, None, 2)
    rows = [known, unknown]
    text = report_markdown(_meta(), summarize(rows), rows)
    assert "| cuanta | 1/2 | 2 | 12 | 0 | 0 | 23 | $0.30 |" in text
    assert "| cuanta | 1/2 | 1 | 2 | 50.0% | cited files read / files read |" in text
    assert "observed heuristics, not causal token attribution" in text
    assert "Preloaded packs are not observed reads" in text


def test_report_and_summaries_keep_actual_option_groups_separate() -> None:
    single = replace(
        _metrics(Condition.CUANTA, True, 35, 0.3),
        shape="single",
        pack="off",
        depth="quick",
        anatomy=ANATOMY,
        read_efficiency=EFFICIENCY,
    )
    pipeline = replace(single, shape="pipeline", pack="on", depth="normal", rep=2)
    summary = summarize([single, pipeline])
    assert len(summary) == 2 and all(row.runs == 1 for row in summary)
    assert {(row.shape, row.pack, row.depth) for row in summary} == {
        ("single", "off", "quick"),
        ("pipeline", "on", "normal"),
    }
    text = report_markdown(_meta(), summary, [single, pipeline])
    assert "| single | off | quick |" in text and "| pipeline | on | normal |" in text


def test_empty_observed_reads_reports_unknown_ratio() -> None:
    metrics = _metrics(Condition.BASELINE, True, 10, 0.1)
    text = report_markdown(_meta(), summarize([metrics]), [metrics])
    assert "| baseline | 0/1 | n/a | n/a | n/a |" in text


def test_attempt_answer_is_ephemeral_and_defaults_preserve_code_bench() -> None:
    attempt = Attempt("r", "success", 0.1, 1, 2, 3, 4, 0)
    assert attempt.answer is None and attempt.anatomy == AnatomyReport()
    assert attempt.read_efficiency == ReadEfficiency()
