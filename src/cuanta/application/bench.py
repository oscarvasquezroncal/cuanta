from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from itertools import groupby
from math import isfinite

from cuanta.domain.anatomy import (
    ANATOMY_HEURISTIC,
    AgentAnatomy,
    AnatomyReport,
    Phase,
    PhaseSummary,
    PhaseTotals,
    UsagePhase,
)
from cuanta.domain.bench import (
    LEAN_SESSION,
    BenchMeta,
    BenchTask,
    Condition,
    PlannedRun,
    ProofRecord,
    RunMetrics,
    charts,
    plan_runs,
    readme_section,
    report_markdown,
    rerun_count,
    sessions_for,
    splice,
    summarize,
    was_capped,
)
from cuanta.domain.bench_proof import (
    NOT_LAUNCHED,
    arm_named,
    comparison_caps,
    comparison_key,
    ended_by_cap,
    launched_cost,
    proof_markdown,
)
from cuanta.domain.costs import sum_costs
from cuanta.domain.errors import CuantaError
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status, finished, note, started
from cuanta.domain.read_efficiency import CODE_FORMULA, ReadEfficiency
from cuanta.ports.bench import BenchSandbox
from cuanta.ports.progress import ProgressSink
from cuanta.ports.workspace import Workspace

BENCH_DIR = ".cuanta/bench"
LATEST = f"{BENCH_DIR}/latest.txt"
DOCS_DIR = "docs/bench"
README = "README.md"


@dataclass(frozen=True, slots=True)
class BenchResult:
    meta: BenchMeta
    metrics: tuple[RunMetrics, ...]
    stopped_early: bool
    folder: str


@dataclass(frozen=True, slots=True)
class Attempt:
    run_id: str
    subtype: str
    cost_usd: float | None
    fresh_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    test_output_tokens: int
    commands: tuple[str, ...] = ()
    models: tuple[tuple[str, str, str], ...] = ()
    context_tokens: int = 0
    loaded: tuple[int, int, int] = (0, 0, 0)
    index: str = "on"
    exploration_tokens_estimate: int = 0
    raw_reads: int = 0
    index_calls: int = 0
    out_of_plan_edits: tuple[str, ...] = ()
    guard_violations: tuple[str, ...] = ()
    answer: str | None = None
    shape: str = ""
    pack: str = "on"
    depth: str = ""
    anatomy: AnatomyReport = field(default_factory=AnatomyReport)
    read_efficiency: ReadEfficiency = field(default_factory=ReadEfficiency)
    proof: ProofRecord | None = None


def _empty(
    task: BenchTask,
    condition: Condition,
    rep: int,
    session: str,
    index: str,
    proof: ProofRecord | None = None,
) -> RunMetrics:
    return RunMetrics(
        task.name,
        condition,
        rep,
        "",
        False,
        False,
        0,
        0,
        0,
        0,
        None,
        0.0,
        0,
        0,
        session=session,
        index=index,
        proof=proof,
    )


def planned_proof(run: PlannedRun) -> ProofRecord:
    arm = arm_named(run.arm)
    if arm is None:
        raise ValueError(f"bench arm {run.arm!r} is not defined")
    return ProofRecord(
        arm.comparison.value, arm.name, run.cap_usd, group=run.group, position=arm.position
    )


class BenchExecutor:
    def __init__(
        self,
        sandbox: BenchSandbox,
        attempt: Callable[[BenchTask, Condition, str, float, str, str], Attempt],
        monotonic: Callable[[], float],
        arm_attempt: Callable[[BenchTask, PlannedRun, str], Attempt] | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._attempt = attempt
        self._monotonic = monotonic
        self._arm_attempt = arm_attempt

    def __call__(
        self,
        task: BenchTask,
        condition: Condition,
        rep: int,
        cap: float,
        session: str = LEAN_SESSION,
        index: str = "on",
    ) -> RunMetrics:
        selected = "off" if condition is Condition.BASELINE else index
        label = f"{task.name}-{condition.value}-{rep}-{selected}"
        empty = _empty(task, condition, rep, session, selected)
        try:
            root = self._sandbox.prepare(task, condition is not Condition.BASELINE, label)
        except CuantaError as error:
            return replace(empty, error=error.message)
        began = self._monotonic()
        try:
            try:
                done = self._attempt(task, condition, root, cap, session, selected)
            except CuantaError as error:
                return replace(empty, wall_s=self._monotonic() - began, error=error.message)
            wall = self._monotonic() - began
            return self._score(task, condition, rep, done, root, cap, wall, session, selected)
        finally:
            self._sandbox.discard(root)

    def group(self, task: BenchTask, runs: Sequence[PlannedRun]) -> tuple[RunMetrics, ...]:
        if self._arm_attempt is None:
            raise RuntimeError("bench arms need an arm attempt; none is wired")
        first = runs[0]
        label = f"{task.name}-{first.arm}-{first.rep}-{first.index}"
        records = [planned_proof(run) for run in runs]
        empties = [
            _empty(task, run.condition, run.rep, run.session, run.index, record)
            for run, record in zip(runs, records, strict=True)
        ]
        try:
            root = self._sandbox.prepare(task, True, label)
        except CuantaError as error:
            return tuple(_unlaunched(empty, error.message) for empty in empties)
        found: list[RunMetrics] = []
        failed = ""
        try:
            for run, record, empty in zip(runs, records, empties, strict=True):
                if failed:
                    found.append(_unlaunched(empty, f"not run: an earlier run failed: {failed}"))
                    continue
                began = self._monotonic()
                try:
                    done = self._arm_attempt(task, run, root)
                except CuantaError as error:
                    failed = error.message
                    found.append(
                        replace(empty, wall_s=self._monotonic() - began, error=error.message)
                    )
                    continue
                wall = self._monotonic() - began
                scored = replace(done, proof=done.proof if done.proof is not None else record)
                capped = ended_by_cap(
                    scored.proof, was_capped(done.subtype, done.cost_usd, run.cap_usd)
                )
                found.append(
                    self._score(
                        task,
                        run.condition,
                        run.rep,
                        scored,
                        root,
                        run.cap_usd,
                        wall,
                        run.session,
                        run.index,
                        capped,
                    )
                )
        finally:
            self._sandbox.discard(root)
        return tuple(found)

    def _score(
        self,
        task: BenchTask,
        condition: Condition,
        rep: int,
        done: Attempt,
        root: str,
        cap: float,
        wall: float,
        session: str,
        selected: str,
        cut: bool | None = None,
    ) -> RunMetrics:
        capped = was_capped(done.subtype, done.cost_usd, cap) if cut is None else cut
        if capped:
            accepted, tail = False, ""
        elif task.answer is not None:
            accepted, tail = self._sandbox.accept(task, root, done.answer)
        else:
            accepted, tail = self._sandbox.accept(task, root)
        if task.request.type == "investigation" and done.out_of_plan_edits:
            accepted = False
            tail = "Investigation changed source paths: " + ", ".join(done.out_of_plan_edits)
        if done.guard_violations:
            accepted = False
            tail = "Protected paths changed: " + ", ".join(done.guard_violations)
        return RunMetrics(
            task=task.name,
            condition=condition,
            rep=rep,
            run_id=done.run_id,
            accepted=accepted,
            capped=capped,
            fresh_tokens=done.fresh_tokens,
            cache_read_tokens=done.cache_read_tokens,
            cache_write_tokens=done.cache_write_tokens,
            output_tokens=done.output_tokens,
            cost_usd=done.cost_usd,
            wall_s=wall,
            test_output_tokens=done.test_output_tokens,
            retries=rerun_count(done.commands),
            models=done.models,
            error="" if accepted or capped else tail[-400:],
            session=session,
            context_tokens=done.context_tokens,
            loaded=done.loaded,
            index=selected,
            exploration_tokens_estimate=done.exploration_tokens_estimate,
            raw_reads=done.raw_reads,
            index_calls=done.index_calls,
            out_of_plan_edits=done.out_of_plan_edits,
            guard_violations=done.guard_violations,
            shape=done.shape,
            pack=done.pack,
            depth=done.depth,
            anatomy=done.anatomy,
            read_efficiency=done.read_efficiency,
            proof=done.proof,
        )


def _unlaunched(empty: RunMetrics, error: str) -> RunMetrics:
    proof = replace(empty.proof, end_reason=NOT_LAUNCHED) if empty.proof is not None else None
    return replace(empty, error=error, proof=proof)


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _proof(value: object) -> ProofRecord | None:
    if not isinstance(value, dict):
        return None
    data = _mapping(value)
    dispatched = data.get("scout_dispatched")
    return ProofRecord(
        comparison=str(data.get("comparison") or ""),
        arm=str(data.get("arm") or ""),
        cap_usd=_float(data.get("cap_usd")) or 0.0,
        end_reason=str(data.get("end_reason") or ""),
        leak_tokens=_optional_int(data.get("leak_tokens")),
        blocked_reads=_optional_int(data.get("blocked_reads")),
        blocked_tokens=_optional_int(data.get("blocked_tokens")),
        scout_mode=str(data.get("scout_mode") or ""),
        scout_dispatched=dispatched if isinstance(dispatched, bool) else None,
        first_cache_read=_optional_int(data.get("first_cache_read")),
        fixed_prefix=_optional_int(data.get("fixed_prefix")),
        finish_sent=data.get("finish_sent") is True,
        finish_spent_usd=_float(data.get("finish_spent_usd")),
        group=_int(data.get("group")),
        position=_int(data.get("position")),
        rule_tokens=_optional_int(data.get("rule_tokens")),
        answered=answered if isinstance(answered := data.get("answered"), bool) else None,
    )


def stored_run(item: RunMetrics) -> dict[str, object]:
    data = asdict(item)
    if item.proof is None:
        del data["proof"]
    return data


def metrics_from_json(item: dict[str, object]) -> RunMetrics:
    models = item.get("models")
    rows = (
        tuple(
            (str(row[0]), str(row[1]), str(row[2]))
            for row in models
            if isinstance(row, list) and len(row) == 3
        )
        if isinstance(models, list)
        else ()
    )
    return RunMetrics(
        task=str(item.get("task", "")),
        condition=Condition(str(item.get("condition", Condition.BASELINE.value))),
        rep=_int(item.get("rep")),
        run_id=str(item.get("run_id", "")),
        accepted=item.get("accepted") is True,
        capped=item.get("capped") is True,
        fresh_tokens=_int(item.get("fresh_tokens")),
        cache_read_tokens=_int(item.get("cache_read_tokens")),
        cache_write_tokens=_int(item.get("cache_write_tokens")),
        output_tokens=_int(item.get("output_tokens")),
        cost_usd=_float(item.get("cost_usd")),
        wall_s=_float(item.get("wall_s")) or 0.0,
        test_output_tokens=_int(item.get("test_output_tokens")),
        retries=_int(item.get("retries")),
        models=rows,
        error=str(item.get("error", "")),
        session=str(item.get("session") or LEAN_SESSION),
        context_tokens=_int(item.get("context_tokens")),
        loaded=_loaded(item.get("loaded")),
        index=str(item.get("index") or "on"),
        exploration_tokens_estimate=_int(item.get("exploration_tokens_estimate")),
        raw_reads=_int(item.get("raw_reads")),
        index_calls=_int(item.get("index_calls")),
        out_of_plan_edits=_paths(item.get("out_of_plan_edits")),
        guard_violations=_paths(item.get("guard_violations")),
        shape=_choice(item.get("shape"), ("", "single", "pipeline"), ""),
        pack=_choice(item.get("pack"), ("on", "off"), "on"),
        depth=_choice(item.get("depth"), ("", "quick", "normal", "deep"), ""),
        anatomy=_anatomy(item.get("anatomy")),
        read_efficiency=_read_efficiency(item.get("read_efficiency")),
        proof=_proof(item.get("proof")),
    )


def _paths(value: object) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _loaded(value: object) -> tuple[int, int, int]:
    if isinstance(value, list) and len(value) == 3:
        return (_int(value[0]), _int(value[1]), _int(value[2]))
    return (0, 0, 0)


def _choice(value: object, allowed: tuple[str, ...], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _mapping(value: object) -> dict[str, object]:
    return (
        {key: item for key, item in value.items() if isinstance(key, str)}
        if isinstance(value, dict)
        else {}
    )


def _sequence(value: object) -> list[dict[str, object]]:
    return (
        [_mapping(item) for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _phase(value: object) -> Phase | None:
    return Phase(value) if isinstance(value, str) and value in tuple(Phase) else None


def _phase_totals(value: object) -> PhaseTotals:
    item = _mapping(value)
    return PhaseTotals(
        fresh_input=max(0, _int(item.get("fresh_input"))),
        cache_read=max(0, _int(item.get("cache_read"))),
        cache_write=max(0, _int(item.get("cache_write"))),
        output=max(0, _int(item.get("output"))),
        reasoning=max(0, _int(item.get("reasoning"))),
        cost_usd=_known_cost(item.get("cost_usd")),
        requests=max(0, _int(item.get("requests"))),
    )


def _known_cost(value: object) -> float | None:
    cost = _float(value)
    return cost if cost is not None and isfinite(cost) and cost >= 0 else None


def _phase_summaries(value: object) -> tuple[PhaseSummary, ...]:
    return tuple(
        PhaseSummary(phase, _phase_totals(item.get("totals")))
        for item in _sequence(value)
        if (phase := _phase(item.get("phase"))) is not None
    )


def _anatomy(value: object) -> AnatomyReport:
    if not isinstance(value, dict):
        return AnatomyReport()
    data = _mapping(value)
    return AnatomyReport(
        totals=_phase_totals(data.get("totals")),
        phases=_phase_summaries(data.get("phases")),
        agents=tuple(
            AgentAnatomy(
                str(item.get("agent", "")),
                _phase_summaries(item.get("phases")),
                _phase_totals(item.get("totals")),
            )
            for item in _sequence(data.get("agents"))
        ),
        events=tuple(
            UsagePhase(
                event_id=_int(item.get("event_id")),
                run_id=str(item.get("run_id", "")),
                session_id=str(item.get("session_id", "")),
                agent=str(item.get("agent", "")),
                ts=str(item.get("ts", "")),
                phase=phase,
                tokens=max(0, _int(item.get("tokens"))),
                cost_usd=_known_cost(item.get("cost_usd")),
                totals=_phase_totals(item.get("totals")),
            )
            for item in _sequence(data.get("events"))
            if (phase := _phase(item.get("phase"))) is not None
        ),
        heuristic=str(data.get("heuristic") or ANATOMY_HEURISTIC),
    )


def _read_efficiency(value: object) -> ReadEfficiency:
    if not isinstance(value, dict):
        return ReadEfficiency()
    data = _mapping(value)
    ratio = _float(data.get("value"))
    valid = ratio is not None and isfinite(ratio) and 0 <= ratio <= 1
    return ReadEfficiency(
        value=ratio if valid else None,
        read_files=_paths(data.get("read_files")),
        cited_files=_paths(data.get("cited_files")),
        edited_files=_paths(data.get("edited_files")),
        useful_files=_paths(data.get("useful_files")),
        formula=str(data.get("formula") or CODE_FORMULA),
        label=str(data.get("label") or "file utilization v2"),
        available=data.get("available") is True and valid,
        why=str(data.get("why") or ""),
    )


def meta_from_json(item: dict[str, object]) -> BenchMeta:
    tasks = item.get("tasks")
    return BenchMeta(
        bench_id=str(item.get("bench_id", "")),
        suite=str(item.get("suite", "")),
        reps=_int(item.get("reps")),
        seed=_int(item.get("seed")),
        engine=str(item.get("engine", "")),
        engine_version=str(item.get("engine_version", "")),
        model=str(item.get("model", "")),
        started_at=str(item.get("started_at", "")),
        budget_usd=_float(item.get("budget_usd")) or 0.0,
        per_run_usd=_float(item.get("per_run_usd")) or 0.0,
        tasks=tuple(str(name) for name in tasks) if isinstance(tasks, list) else (),
        session=str(item.get("session") or LEAN_SESSION),
        index=str(item.get("index") or "on"),
        shape=_choice(item.get("shape"), ("", "single", "pipeline"), ""),
        pack=_choice(item.get("pack"), ("on", "off"), "on"),
        depth=_choice(item.get("depth"), ("", "quick", "normal", "deep"), ""),
    )


def load_bench(workspace: Workspace, bench_id: str = "") -> BenchResult | None:
    chosen = bench_id or (workspace.read_text(LATEST) or "").strip()
    if not chosen:
        return None
    folder = f"{BENCH_DIR}/{chosen}"
    text = workspace.read_text(f"{folder}/results.json")
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("meta"), dict):
        return None
    runs = tuple(metrics_from_json(item) for item in data.get("runs", []) if isinstance(item, dict))
    return BenchResult(meta_from_json(data["meta"]), runs, False, folder)


class BenchRunner:
    def __init__(
        self,
        execute: Callable[[BenchTask, Condition, int, float, str, str], RunMetrics],
        workspace: Workspace,
        execute_group: (
            Callable[[BenchTask, Sequence[PlannedRun]], tuple[RunMetrics, ...]] | None
        ) = None,
    ) -> None:
        self._execute = execute
        self._workspace = workspace
        self._execute_group = execute_group

    def folder(self, bench_id: str) -> str:
        return f"{BENCH_DIR}/{bench_id}"

    def run(
        self,
        meta: BenchMeta,
        tasks: Sequence[BenchTask],
        conditions: Sequence[Condition],
        progress: ProgressSink,
    ) -> BenchResult:
        by_name = {task.name: task for task in tasks}
        planned: tuple[PlannedRun, ...] = plan_runs(
            tasks, conditions, meta.reps, meta.seed, sessions_for(meta.session), meta.index
        )
        metrics: list[RunMetrics] = []
        spent: float | None = 0.0
        stopped = False
        for item in planned:
            if meta.budget_usd > 0 and spent is None:
                progress.publish(note(Status.WARN, msg("bench.cost_unknown")))
                stopped = True
                break
            remaining = meta.budget_usd - spent if spent is not None else 0.0
            if meta.budget_usd > 0 and remaining < meta.per_run_usd:
                progress.publish(note(Status.WARN, msg("bench.budget", spent=f"{spent:.2f}")))
                stopped = True
                break
            key = f"bench-{item.order}"
            progress.publish(
                started(
                    key,
                    msg(
                        "bench.run",
                        order=item.order,
                        total=len(planned),
                        task=item.task,
                        condition=f"{item.condition.value} · {item.session}",
                        rep=item.rep,
                    ),
                )
            )
            result = self._execute(
                by_name[item.task],
                item.condition,
                item.rep,
                meta.per_run_usd,
                item.session,
                item.index,
            )
            metrics.append(result)
            spent = sum_costs((spent, result.cost_usd))
            verdict = "bench.accepted" if result.accepted else "bench.rejected"
            progress.publish(
                finished(key, Status.OK if result.accepted else Status.FAIL, msg(verdict))
            )
            self.save(meta, metrics)
        return BenchResult(meta, tuple(metrics), stopped, self.folder(meta.bench_id))

    def run_proof(
        self,
        meta: BenchMeta,
        tasks: Sequence[BenchTask],
        planned: Sequence[PlannedRun],
        progress: ProgressSink,
    ) -> BenchResult:
        if self._execute_group is None:
            raise RuntimeError("bench arms need a group executor; none is wired")
        by_name = {task.name: task for task in tasks}
        needed = comparison_caps(planned)
        opened: set[tuple[str, int]] = set()
        reserved = 0.0
        metrics: list[RunMetrics] = []
        spent: float | None = 0.0
        stopped = False
        for _, members in groupby(planned, key=lambda item: item.group):
            group = tuple(members)
            if meta.budget_usd > 0 and spent is None:
                progress.publish(note(Status.WARN, msg("bench.cost_unknown")))
                stopped = True
                break
            key = comparison_key(group[0])
            if key not in opened:
                if stopped:
                    continue
                remaining = meta.budget_usd - reserved - spent if spent is not None else 0.0
                if meta.budget_usd > 0 and remaining < needed[key]:
                    progress.publish(note(Status.WARN, msg("bench.budget", spent=f"{spent:.2f}")))
                    stopped = True
                    continue
                opened.add(key)
                reserved += needed[key]
            for item in group:
                progress.publish(
                    started(
                        f"bench-{item.order}",
                        msg(
                            "bench.run",
                            order=item.order,
                            total=len(planned),
                            task=item.task,
                            condition=f"{item.condition.value} · {item.arm} · {item.session}",
                            rep=item.rep,
                        ),
                    )
                )
            results = self._execute_group(by_name[group[0].task], group)
            for item, result in zip(group, results, strict=True):
                verdict = "bench.accepted" if result.accepted else "bench.rejected"
                progress.publish(
                    finished(
                        f"bench-{item.order}",
                        Status.OK if result.accepted else Status.FAIL,
                        msg(verdict),
                    )
                )
            metrics.extend(results)
            reserved -= sum(item.cap_usd for item in group)
            spent = sum_costs((spent, *(launched_cost(result) for result in results)))
            self.save(meta, metrics)
        return BenchResult(meta, tuple(metrics), stopped, self.folder(meta.bench_id))

    def save(self, meta: BenchMeta, metrics: Sequence[RunMetrics]) -> None:
        folder = self.folder(meta.bench_id)
        document = {"meta": asdict(meta), "runs": [stored_run(item) for item in metrics]}
        self._workspace.write_text(f"{folder}/results.json", json.dumps(document, indent=2) + "\n")
        self._workspace.write_text(LATEST, meta.bench_id + "\n")

    def load(self, bench_id: str = "") -> BenchResult | None:
        return load_bench(self._workspace, bench_id)

    def report(self, result: BenchResult, folder: str = "") -> str:
        target = folder or result.folder
        summary = summarize(result.metrics)
        text = report_markdown(result.meta, summary, result.metrics) + proof_markdown(
            result.metrics
        )
        self._workspace.write_text(f"{target}/report.md", text)
        for name, svg in charts(summary).items():
            self._workspace.write_text(f"{target}/{name}", svg + "\n")
        return text

    def publish(self, result: BenchResult, folder: str = DOCS_DIR, readme: str = README) -> None:
        self.report(result, folder)
        summary = summarize(result.metrics)
        spent = sum_costs(item.cost_usd for item in result.metrics)
        section = readme_section(result.meta, summary, spent, folder)
        current = self._workspace.read_text(readme) or ""
        self._workspace.write_text(readme, splice(current, section))
