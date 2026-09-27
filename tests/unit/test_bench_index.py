from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.bench import Attempt, BenchExecutor, BenchRunner, metrics_from_json
from cuanta.application.engine_run import LaunchSpec
from cuanta.bootstrap import Container
from cuanta.domain.bench import (
    BenchTask,
    Condition,
    bench_boundaries,
    plan_runs,
    report_markdown,
    summarize,
)
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.config import Config, layer_from_table
from cuanta.domain.index_tools import INDEX_CONTRACT, INDEX_TOOLS
from cuanta.domain.mandate import MandateRequest
from tests.fakes import FakeRunner
from tests.unit.test_bench import TASK, Sandbox, _attempt, _meta, _metrics


@pytest.mark.parametrize("kind", ["bug", "feature", "refactor", "investigation"])
@pytest.mark.parametrize("index", ["on", "off"])
def test_index_mode_propagates_for_every_mandate_type(kind: str, index: str) -> None:
    task = replace(TASK, request=replace(TASK.request, type=kind))
    seen: list[tuple[str, str]] = []

    def attempt(
        task: BenchTask, condition: Condition, root: str, cap: float, session: str, index: str
    ) -> Attempt:
        seen.append((task.request.type, index))
        return replace(_attempt(0.1), index=index)

    planned = plan_runs([task], [Condition.CUANTA], 1, 5, index=index)
    item = planned[0]
    metrics = BenchExecutor(Sandbox(), attempt, lambda: 0.0)(
        task, item.condition, item.rep, 1.0, item.session, item.index
    )
    assert seen == [(kind, index)] and metrics.index == index


def test_index_mode_flows_from_plan_to_executor_and_persisted_report(tmp_path: Path) -> None:
    seen: list[str] = []

    def attempt(
        task: BenchTask, condition: Condition, root: str, cap: float, session: str, index: str
    ) -> Attempt:
        seen.append(index)
        return replace(
            _attempt(0.1),
            index=index,
            exploration_tokens_estimate=32,
            raw_reads=2,
            index_calls=1,
            out_of_plan_edits=("extra.py",),
        )

    planned = plan_runs([TASK], [Condition.CUANTA], 1, 5, index="off")
    assert planned[0].index == "off"
    sandbox = Sandbox()
    executor = BenchExecutor(sandbox, attempt, lambda: 0.0)
    metrics = executor(TASK, Condition.CUANTA, 1, 1.0, index=planned[0].index)
    assert seen == ["off"]
    assert metrics.index == "off"
    assert (metrics.raw_reads, metrics.index_calls, metrics.exploration_tokens_estimate) == (
        2,
        1,
        32,
    )
    runner = BenchRunner(executor, LocalWorkspace(tmp_path))
    meta = replace(_meta(), index="off")
    runner.save(meta, [metrics])
    saved = runner.load()
    assert saved is not None and saved.meta.index == "off" and saved.metrics == (metrics,)
    report = runner.report(saved)
    assert "Exploration and change boundaries" in report
    assert "extra.py" in report and "index off" in report


def test_guard_violation_rejects_an_otherwise_accepted_bench_attempt() -> None:
    class Accepting(Sandbox):
        def accept(self, task: BenchTask, root: str) -> tuple[bool, str]:
            self.accepted.append(root)
            return True, "passed"

    done = replace(_attempt(0.1), guard_violations=("protected.py",))
    sandbox = Accepting()
    metrics = BenchExecutor(sandbox, lambda *_: done, lambda: 0.0)(TASK, Condition.CUANTA, 1, 1.0)
    assert not metrics.accepted and not metrics.capped
    assert metrics.guard_violations == ("protected.py",)
    assert "protected.py" in metrics.error
    assert len(sandbox.accepted) == 1 and len(sandbox.discarded) == 1


def test_bench_boundaries_detect_removed_added_and_mode_only_protected_changes() -> None:
    plan = ChangePlan(edit=(EditTarget("cart.py", 1.0),), guard=("protected/**",))
    before = {"cart.py": "old:1", "removed.py": "old:1", "protected/a.py": "same:1"}
    after = {"cart.py": "new:1", "added.py": "new:1", "protected/a.py": "same:2"}
    outside, protected = bench_boundaries(plan, before, after)
    assert outside == ("added.py", "protected/a.py", "removed.py")
    assert protected == ("protected/a.py",)


def test_baseline_disables_owned_index_even_if_selected_on() -> None:
    seen: list[str] = []

    def attempt(*args: object) -> Attempt:
        assert isinstance(args[-1], str)
        seen.append(args[-1])
        return _attempt(0.1)

    metrics = BenchExecutor(Sandbox(), attempt, lambda: 0.0)(TASK, Condition.BASELINE, 1, 1.0)
    assert seen == ["off"] and metrics.index == "off"


def test_old_bench_json_defaults_index_and_keeps_metrics_available() -> None:
    old = metrics_from_json({"task": "old", "condition": "cuanta", "rep": 1})
    assert old.index == "on"
    assert old.exploration_tokens_estimate == old.raw_reads == old.index_calls == 0
    assert not old.guard_violations and not old.out_of_plan_edits


def test_summary_separates_index_modes_and_report_keeps_acceptance() -> None:
    runs = (
        replace(_metrics(Condition.CUANTA, True, 100, 0.1), index="on"),
        replace(_metrics(Condition.CUANTA, False, 200, 0.2), index="off"),
    )
    rows = summarize(runs)
    assert [(row.index, row.accepted) for row in rows] == [("on", 1), ("off", 0)]
    text = report_markdown(_meta(), rows, runs)
    assert "cuanta · index off" in text and "Guard violations" in text


def test_automatic_index_off_keeps_safety_plan_without_opening_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "cart.py").write_text("TOTAL = 1\n", encoding="utf-8")
    (tmp_path / "protected.py").write_text("VALUE = 1\n", encoding="utf-8")
    container = Container(
        tmp_path, Config(index_enabled=False, index_tools=True), runner=FakeRunner()
    )

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Automatic index off cannot open an index")

    monkeypatch.setattr(container, "index_service", forbidden)
    container.refresh_index()
    container.learn_run("unknown")
    plan = container.change_plan(
        MandateRequest(type="bug", what="Fix TOTAL", where="cart.py", out_of_scope="protected.py")
    )
    assert plan.guard == ("protected.py",)
    assert "cart.py" in {target.path for target in plan.edit}
    investigation = container.change_plan(
        MandateRequest(type="investigation", what="Audit cart.py")
    )
    assert investigation.read_only
    flow = container.mandate_flow(MemoryLedger())
    cross = container.cross_engine(MemoryLedger(), 1.0, 0)
    assert flow._context_pack is None and cross._context_pack is None
    engine = container.engine("claude")
    assert engine is not None
    request = container.launcher(engine, MemoryLedger(), telemetry=False).request(
        LaunchSpec("mandate", "request", str(tmp_path), (), read_only=True), "RUN", "TRACE", None
    )
    assert not set(request.allowed_tools) & set(INDEX_TOOLS)
    assert INDEX_CONTRACT not in request.append_system_prompt
    assert not (tmp_path / ".cuanta" / "index.db").exists()


def test_explicit_index_pack_stays_available_when_automatic_index_is_off(tmp_path: Path) -> None:
    (tmp_path / "cart.py").write_text("TOTAL = 1\n", encoding="utf-8")
    container = Container(tmp_path, Config(index_enabled=False), runner=FakeRunner())
    pack = container.context_pack(MandateRequest(type="bug", what="Fix total", where="cart.py"))
    assert pack.tokens > 0 and (tmp_path / ".cuanta" / "index.db").is_file()
    assert Config().index_enabled is True and Config().index_tools is False
    assert layer_from_table({"runs": {"index_enabled": False}})["index_enabled"] is False
