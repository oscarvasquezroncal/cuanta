from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.bench import (
    Attempt,
    BenchExecutor,
    BenchRunner,
    metrics_from_json,
    planned_proof,
    stored_run,
)
from cuanta.application.progress import RecordingSink
from cuanta.domain.bench import BenchMeta, BenchTask, Condition, PlannedRun, ProofRecord, RunMetrics
from cuanta.domain.bench_proof import NOT_LAUNCHED, Comparison, plan_proof
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.progress import Note

TASK = BenchTask("t1", ("proof",), "bugfix", MandateRequest(type="bug", what="w"), "p", {"a": "b"})


class Sandbox:
    def __init__(self, fail_prepare: bool = False) -> None:
        self.fail_prepare = fail_prepare
        self.prepared: list[tuple[str, bool]] = []
        self.accepted: list[str] = []
        self.discarded: list[str] = []

    def prepare(self, task: BenchTask, with_kit: bool, label: str) -> str:
        if self.fail_prepare:
            raise DomainFailure("no fixture")
        self.prepared.append((label, with_kit))
        return f"/tmp/{label}"

    def accept(self, task: BenchTask, root: str, report: str | None = None) -> tuple[bool, str]:
        self.accepted.append(root)
        return True, ""

    def discard(self, root: str) -> None:
        self.discarded.append(root)


def attempt(cost: float, proof: ProofRecord | None = None) -> Attempt:
    return Attempt("run", "success", cost, 10, 20, 30, 40, 0, proof=proof)


def meta(budget: float = 10.0, per_run: float = 0.5) -> BenchMeta:
    return BenchMeta(
        "b1",
        "proof",
        1,
        3,
        "claude",
        "2.1",
        "haiku",
        "2026-09-29T10:00:00",
        budget,
        per_run,
        ("t1",),
    )


def test_a_queued_pair_runs_back_to_back_in_one_copy() -> None:
    sandbox = Sandbox()
    seen: list[tuple[str, str, float]] = []

    def arm_attempt(task: BenchTask, run: PlannedRun, root: str) -> Attempt:
        seen.append((run.arm, root, run.cap_usd))
        return attempt(0.05 * len(seen))

    pair = plan_proof([TASK], Comparison.WARM_QUEUE, 1, 1, {"queue-second": 0.2}, 0.3)
    executor = BenchExecutor(sandbox, lambda *_: attempt(0.0), lambda: 0.0, arm_attempt)
    first, second = executor.group(TASK, pair)
    assert sandbox.prepared == [("t1-queue-first-1-on", True)]
    assert seen == [
        ("queue-first", "/tmp/t1-queue-first-1-on", 0.3),
        ("queue-second", "/tmp/t1-queue-first-1-on", 0.2),
    ]
    assert sandbox.accepted == ["/tmp/t1-queue-first-1-on"] * 2
    assert sandbox.discarded == ["/tmp/t1-queue-first-1-on"]
    assert first.proof == planned_proof(pair[0])
    assert second.proof is not None and second.proof.position == 2
    assert (first.cost_usd, second.cost_usd) == (0.05, 0.1)
    assert first.condition is Condition.CUANTA and first.arm == "queue-first"


def test_a_failed_first_mandate_skips_its_pair_without_a_launch() -> None:
    sandbox = Sandbox()
    calls: list[str] = []

    def arm_attempt(task: BenchTask, run: PlannedRun, root: str) -> Attempt:
        calls.append(run.arm)
        raise DomainFailure("claude not found")

    pair = plan_proof([TASK], Comparison.WARM_QUEUE, 1, 1, {}, 0.3)
    executor = BenchExecutor(sandbox, lambda *_: attempt(0.0), lambda: 0.0, arm_attempt)
    first, second = executor.group(TASK, pair)
    assert calls == ["queue-first"]
    assert first.error == "claude not found"
    assert first.proof is not None and first.proof.end_reason == ""
    assert second.error == "not run: an earlier run failed: claude not found"
    assert second.proof is not None and second.proof.arm == "queue-second"
    assert second.proof.end_reason == NOT_LAUNCHED
    assert sandbox.discarded == ["/tmp/t1-queue-first-1-on"]
    unprepared = BenchExecutor(
        Sandbox(fail_prepare=True), lambda *_: attempt(0.0), lambda: 0.0, arm_attempt
    )
    assert unprepared.group(TASK, pair[:1]) == (
        replace(
            first,
            run_id="",
            wall_s=0.0,
            error="no fixture",
            proof=replace(first.proof, end_reason=NOT_LAUNCHED),
        ),
    )
    with pytest.raises(RuntimeError, match="arm attempt"):
        BenchExecutor(Sandbox(), lambda *_: attempt(0.0), lambda: 0.0).group(TASK, pair)


def test_an_attempts_own_proof_record_is_kept() -> None:
    measured = ProofRecord("finish", "tight-cap", 0.1, "error_max_budget_usd", finish_sent=True)
    planned = plan_proof([TASK], Comparison.FINISH, 1, 1, {}, 0.1)
    executor = BenchExecutor(
        Sandbox(), lambda *_: attempt(0.0), lambda: 0.0, lambda *_: attempt(0.12, measured)
    )
    (result,) = executor.group(TASK, planned)
    assert result.proof == measured and result.capped and not result.accepted


def test_a_run_that_answered_past_its_cap_is_scored() -> None:
    answered = ProofRecord("finish", "tight-cap", 0.1, "error_max_budget_usd", answered=True)
    planned = plan_proof([TASK], Comparison.FINISH, 1, 1, {}, 0.1)
    sandbox = Sandbox()
    executor = BenchExecutor(
        sandbox, lambda *_: attempt(0.0), lambda: 0.0, lambda *_: attempt(0.12, answered)
    )
    (result,) = executor.group(TASK, planned)
    assert not result.capped and result.accepted
    assert sandbox.accepted == ["/tmp/t1-tight-cap-1-on"]


def legacy(*_: object) -> RunMetrics:
    raise AssertionError("the legacy executor ran")


def grouped(calls: list[tuple[str, ...]], root: Path) -> BenchRunner:
    def execute_group(task: BenchTask, runs: Sequence[PlannedRun]) -> tuple[RunMetrics, ...]:
        calls.append(tuple(run.arm for run in runs))
        return tuple(
            RunMetrics(
                task.name,
                run.condition,
                run.rep,
                f"r{run.order}",
                True,
                False,
                1,
                2,
                3,
                4,
                run.cap_usd / 2,
                1.0,
                0,
                0,
                proof=replace(planned_proof(run), first_cache_read=900, fixed_prefix=1_000),
            )
            for run in runs
        )

    return BenchRunner(legacy, LocalWorkspace(root), execute_group)


def test_the_proof_runner_reserves_a_whole_group_before_it_starts(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    runner = grouped(calls, tmp_path)
    planned = plan_proof([TASK], Comparison.WARM_QUEUE, 2, 1, {}, 0.3)
    sink = RecordingSink()
    result = runner.run_proof(meta(budget=0.8), [TASK], planned, sink)
    assert calls == [("queue-first", "queue-second")]
    assert result.stopped_early and len(result.metrics) == 2
    assert any(isinstance(event, Note) for event in sink.events)
    stored = json.loads((tmp_path / ".cuanta" / "bench" / "b1" / "results.json").read_text())
    assert stored["runs"][0]["proof"]["arm"] == "queue-first"
    loaded = runner.load()
    assert loaded is not None and loaded.metrics == result.metrics
    text = runner.report(loaded)
    assert "## Targets" in text and "| warm-queue | met |" in text
    with pytest.raises(RuntimeError, match="group executor"):
        BenchRunner(legacy, LocalWorkspace(tmp_path)).run_proof(meta(), [TASK], planned, sink)


def measured(runs: Sequence[PlannedRun], costs: dict[int, float | None]) -> tuple[RunMetrics, ...]:
    return tuple(
        RunMetrics(
            TASK.name,
            run.condition,
            run.rep,
            f"r{run.order}",
            True,
            False,
            1,
            2,
            3,
            4,
            costs[run.order],
            1.0,
            0,
            0,
            proof=planned_proof(run),
        )
        for run in runs
    )


def scout_runs() -> list[PlannedRun]:
    arms = (("scout", 1), ("pipeline", 1), ("scout", 2), ("pipeline", 2))
    return [
        PlannedRun(order, "t1", Condition.ROUTED, rep, arm=arm, cap_usd=0.5, group=order)
        for order, (arm, rep) in enumerate(arms, 1)
    ]


def test_a_comparison_that_started_runs_every_arm_past_an_overshoot(tmp_path: Path) -> None:
    planned = scout_runs()
    calls: list[int] = []

    def execute_group(task: BenchTask, runs: Sequence[PlannedRun]) -> tuple[RunMetrics, ...]:
        calls.extend(run.order for run in runs)
        return measured(runs, {1: 0.8, 2: 0.4, 3: 0.4, 4: 0.4})

    sink = RecordingSink()
    runner = BenchRunner(legacy, LocalWorkspace(tmp_path), execute_group)
    result = runner.run_proof(meta(budget=1.2), [TASK], planned, sink)
    assert calls == [1, 2]
    assert result.stopped_early and len(result.metrics) == 2
    assert any(isinstance(event, Note) for event in sink.events)


def test_runs_that_never_launched_do_not_stop_the_bench(tmp_path: Path) -> None:
    planned = plan_proof([TASK], Comparison.FINISH, 2, 1, {}, 0.3)
    unprepared = BenchExecutor(
        Sandbox(fail_prepare=True), lambda *_: attempt(0.0), lambda: 0.0, lambda *_: attempt(0.0)
    )
    calls: list[int] = []

    def execute_group(task: BenchTask, runs: Sequence[PlannedRun]) -> tuple[RunMetrics, ...]:
        calls.extend(run.order for run in runs)
        if len(calls) == 1:
            return unprepared.group(task, runs)
        return measured(runs, {run.order: 0.1 for run in runs})

    runner = BenchRunner(legacy, LocalWorkspace(tmp_path), execute_group)
    result = runner.run_proof(meta(budget=1.0), [TASK], planned, RecordingSink())
    assert calls == [1, 2] and not result.stopped_early
    assert result.metrics[0].cost_usd is None and result.metrics[1].cost_usd == 0.1


def test_a_partial_cost_stops_the_proof_runner_before_the_next_group(tmp_path: Path) -> None:
    planned = scout_runs()
    calls: list[int] = []

    def execute_group(task: BenchTask, runs: Sequence[PlannedRun]) -> tuple[RunMetrics, ...]:
        calls.extend(run.order for run in runs)
        found = measured(runs, {run.order: 0.1 for run in runs})
        return tuple(replace(item, partial=True) for item in found)

    sink = RecordingSink()
    runner = BenchRunner(legacy, LocalWorkspace(tmp_path), execute_group)
    result = runner.run_proof(meta(budget=5.0), [TASK], planned, sink)
    assert calls == [1] and result.stopped_early
    keys = [
        event.message.key
        for event in sink.events
        if isinstance(event, Note) and event.message is not None
    ]
    assert keys == ["bench.cost_partial"]


def test_only_a_partial_run_stores_its_partial_flag() -> None:
    plain = RunMetrics("t1", Condition.CUANTA, 1, "r", True, False, 1, 2, 3, 4, 0.2, 1.0, 0, 0)
    assert "partial" not in stored_run(plain)
    cut = replace(plain, partial=True)
    assert stored_run(cut)["partial"] is True
    assert metrics_from_json(json.loads(json.dumps(stored_run(cut)))) == cut


def test_runs_without_a_proof_store_the_legacy_document() -> None:
    plain = RunMetrics("t1", Condition.CUANTA, 1, "r", True, False, 1, 2, 3, 4, 0.2, 1.0, 0, 0)
    data = stored_run(plain)
    assert "proof" not in data
    assert metrics_from_json(data) == plain
    proved = replace(plain, proof=ProofRecord("scout", "scout", 0.8, scout_mode="native"))
    assert metrics_from_json(json.loads(json.dumps(stored_run(proved)))) == proved
    broken = {**stored_run(plain), "proof": "nonsense"}
    assert metrics_from_json(broken).proof is None
    with pytest.raises(ValueError, match="not defined"):
        planned_proof(PlannedRun(1, "t1", Condition.CUANTA, 1, arm="nope"))
