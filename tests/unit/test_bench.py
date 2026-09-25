from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.bench import Attempt, BenchExecutor, BenchRunner
from cuanta.application.progress import RecordingSink
from cuanta.domain.bench import (
    CONDITIONS,
    README_END,
    README_START,
    BenchMeta,
    BenchTask,
    Condition,
    RunMetrics,
    bar_chart,
    command_args,
    plan_runs,
    readme_section,
    report_markdown,
    rerun_count,
    sessions_for,
    splice,
    summarize,
    was_capped,
)
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.progress import Note

TASK = BenchTask("t1", ("mini",), "bugfix", MandateRequest(type="bug", what="w"), "p", {"a": "b"})
OTHER = replace(TASK, name="t2")


def _metrics(
    condition: Condition, accepted: bool, tokens: int, cost: float | None, rep: int = 1
) -> RunMetrics:
    return RunMetrics("t1", condition, rep, "r", accepted, False, tokens, 0, 0, 0, cost, 2.0, 0, 0)


def _meta(budget: float = 10.0, per_run: float = 3.0) -> BenchMeta:
    return BenchMeta(
        "b1",
        "mini",
        2,
        5,
        "claude",
        "2.1",
        "sonnet",
        "2026-09-24T10:00:00",
        budget,
        per_run,
        ("t1", "t2"),
    )


def test_plan_runs_is_seeded_and_covers_every_combination() -> None:
    first = plan_runs([TASK, OTHER], CONDITIONS, 2, 11)
    assert first == plan_runs([TASK, OTHER], CONDITIONS, 2, 11)
    assert len(first) == 12
    assert [item.order for item in first] == list(range(1, 13))
    combos = {(item.task, item.condition, item.rep) for item in first}
    assert len(combos) == 12
    assert first != plan_runs([TASK, OTHER], CONDITIONS, 2, 12)


def test_summarize_reports_medians_ranges_and_success() -> None:
    rows = summarize(
        [
            _metrics(Condition.BASELINE, True, 100, 0.5),
            _metrics(Condition.BASELINE, True, 300, 1.5, 2),
            _metrics(Condition.BASELINE, False, 900, None, 3),
            _metrics(Condition.CUANTA, False, 50, 0.1),
        ]
    )
    baseline, cuanta = rows
    assert baseline.runs == 3
    assert baseline.accepted == 2
    assert baseline.tokens_per_accepted.low == 100
    assert baseline.tokens_per_accepted.high == 300
    assert baseline.cost.median is not None
    assert baseline.spent_usd == 2.0
    assert cuanta.tokens_per_accepted.median is None
    assert cuanta.success == 0.0


def test_command_args_substitutes_python_and_hidden_files() -> None:
    args = command_args("{python} -m pytest -q {hidden}", "C:/py.exe", ("a.py", "b.py"))
    assert args == ("C:/py.exe", "-m", "pytest", "-q", "a.py", "b.py")


def test_rerun_count_and_capping() -> None:
    assert rerun_count(["ls", "uv run pytest -q", "cuanta test", "cat x"]) == 1
    assert rerun_count(["ls"]) == 0
    assert was_capped("error_max_budget_usd", 0.2, 3.0)
    assert was_capped("success", 3.1, 3.0)
    assert not was_capped("success", None, 3.0)
    assert not was_capped("success", 9.0, 0.0)


def test_chart_and_readme_section() -> None:
    svg = bar_chart("T <1>", [("baseline", 10.0, 5.0, 20.0), ("cuanta", None, None, None)])
    assert svg.startswith("<svg")
    assert "T &lt;1&gt;" in svg
    assert "no accepted runs" in svg
    summary = summarize([_metrics(Condition.BASELINE, True, 100, 0.5)])
    section = readme_section(_meta(), summary, 0.5, "docs/bench")
    appended = splice("# x\n", section)
    assert appended.startswith("# x\n\n## Benchmark\n")
    replaced = splice(appended, section.replace("1/1", "0/1"))
    assert replaced.count(README_START) == 1
    assert replaced.count(README_END) == 1
    assert "0/1" in replaced


def test_runner_stops_at_the_bench_budget_and_round_trips(tmp_path: Path) -> None:
    calls: list[tuple[str, Condition, int, float]] = []

    def execute(
        task: BenchTask, condition: Condition, rep: int, cap: float, session: str
    ) -> RunMetrics:
        calls.append((task.name, condition, rep, cap))
        return replace(_metrics(condition, True, 10, 3.0, rep), task=task.name)

    runner = BenchRunner(execute, LocalWorkspace(tmp_path))
    sink = RecordingSink()
    result = runner.run(_meta(budget=7.0), [TASK, OTHER], CONDITIONS, sink)
    assert len(calls) == 2
    assert result.stopped_early is True
    assert all(cap == 3.0 for *_, cap in calls)
    assert any(isinstance(event, Note) for event in sink.events)
    loaded = runner.load()
    assert loaded is not None
    assert loaded.meta == result.meta
    assert loaded.metrics == result.metrics
    text = runner.report(loaded)
    assert "$6.00 spent" in text
    assert (tmp_path / ".cuanta" / "bench" / "b1" / "cost.svg").is_file()
    assert runner.load("missing") is None


class Sandbox:
    def __init__(self, fail_prepare: bool = False) -> None:
        self.fail_prepare = fail_prepare
        self.accepted: list[str] = []
        self.discarded: list[str] = []

    def prepare(self, task: BenchTask, with_kit: bool, label: str) -> str:
        if self.fail_prepare:
            raise DomainFailure("no fixture")
        return f"/tmp/{label}/{with_kit}"

    def accept(self, task: BenchTask, root: str) -> tuple[bool, str]:
        self.accepted.append(root)
        return False, "1 failed"

    def discard(self, root: str) -> None:
        self.discarded.append(root)


def _attempt(cost: float | None, subtype: str = "success") -> Attempt:
    return Attempt("run1", subtype, cost, 10, 20, 30, 40, 5, ("pytest", "pytest -x"), ())


def test_executor_skips_acceptance_for_capped_runs() -> None:
    sandbox = Sandbox()
    ticks = iter([1.0, 4.5])
    executor = BenchExecutor(sandbox, lambda *_: _attempt(3.2), lambda: next(ticks))
    metrics = executor(TASK, Condition.CUANTA, 1, 3.0)
    assert metrics.capped is True
    assert metrics.accepted is False
    assert metrics.wall_s == 3.5
    assert metrics.retries == 1
    assert metrics.total_tokens == 100
    assert sandbox.accepted == []
    assert sandbox.discarded == ["/tmp/t1-cuanta-1/True"]


def test_executor_reports_rejections_and_errors() -> None:
    sandbox = Sandbox()
    executor = BenchExecutor(sandbox, lambda *_: _attempt(0.4), lambda: 0.0)
    rejected = executor(TASK, Condition.BASELINE, 2, 3.0)
    assert rejected.accepted is False
    assert rejected.error == "1 failed"
    assert sandbox.accepted == ["/tmp/t1-baseline-2/False"]

    def boom(*_: object) -> Attempt:
        raise DomainFailure("claude not found")

    failing = BenchExecutor(sandbox, boom, lambda: 0.0)
    assert failing(TASK, Condition.ROUTED, 1, 3.0).error == "claude not found"
    unprepared = BenchExecutor(Sandbox(fail_prepare=True), boom, lambda: 0.0)
    assert unprepared(TASK, Condition.ROUTED, 1, 3.0).error == "no fixture"


def test_both_sessions_double_the_plan_and_label_the_summary() -> None:
    planned = plan_runs([TASK], [Condition.BASELINE], 1, 3, sessions_for("both"))
    assert sorted(item.session for item in planned) == ["full", "lean"]
    metrics = [
        replace(_metrics(Condition.BASELINE, True, 100, 0.2), context_tokens=6_000),
        replace(
            _metrics(Condition.BASELINE, True, 100, 0.4),
            session="full",
            context_tokens=53_000,
        ),
    ]
    rows = summarize(metrics)
    assert [row.label for row in rows] == ["baseline", "baseline · full"]
    assert rows[1].context.median == 53_000
    report = report_markdown(replace(_meta(), session="both"), rows, metrics)
    assert "Session profile **both**" in report
    assert "| baseline · full | 1 |" in report
    assert "| t1 | baseline | full | 1 | yes |" in report
