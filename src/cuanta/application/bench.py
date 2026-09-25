from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace

from cuanta.domain.bench import (
    LEAN_SESSION,
    BenchMeta,
    BenchTask,
    Condition,
    PlannedRun,
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
from cuanta.domain.errors import CuantaError
from cuanta.domain.messages import msg
from cuanta.domain.progress import Status, finished, note, started
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


class BenchExecutor:
    def __init__(
        self,
        sandbox: BenchSandbox,
        attempt: Callable[[BenchTask, Condition, str, float, str], Attempt],
        monotonic: Callable[[], float],
    ) -> None:
        self._sandbox = sandbox
        self._attempt = attempt
        self._monotonic = monotonic

    def __call__(
        self,
        task: BenchTask,
        condition: Condition,
        rep: int,
        cap: float,
        session: str = LEAN_SESSION,
    ) -> RunMetrics:
        label = f"{task.name}-{condition.value}-{rep}"
        empty = RunMetrics(
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
        )
        try:
            root = self._sandbox.prepare(task, condition is not Condition.BASELINE, label)
        except CuantaError as error:
            return replace(empty, error=error.message)
        began = self._monotonic()
        try:
            try:
                done = self._attempt(task, condition, root, cap, session)
            except CuantaError as error:
                return replace(empty, wall_s=self._monotonic() - began, error=error.message)
            wall = self._monotonic() - began
            capped = was_capped(done.subtype, done.cost_usd, cap)
            accepted, tail = (False, "") if capped else self._sandbox.accept(task, root)
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
            )
        finally:
            self._sandbox.discard(root)


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


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
    )


def _loaded(value: object) -> tuple[int, int, int]:
    if isinstance(value, list) and len(value) == 3:
        return (_int(value[0]), _int(value[1]), _int(value[2]))
    return (0, 0, 0)


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
        execute: Callable[[BenchTask, Condition, int, float, str], RunMetrics],
        workspace: Workspace,
    ) -> None:
        self._execute = execute
        self._workspace = workspace

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
            tasks, conditions, meta.reps, meta.seed, sessions_for(meta.session)
        )
        metrics: list[RunMetrics] = []
        spent = 0.0
        stopped = False
        for item in planned:
            remaining = meta.budget_usd - spent
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
                by_name[item.task], item.condition, item.rep, meta.per_run_usd, item.session
            )
            metrics.append(result)
            spent += result.cost_usd or 0.0
            verdict = "bench.accepted" if result.accepted else "bench.rejected"
            progress.publish(
                finished(key, Status.OK if result.accepted else Status.FAIL, msg(verdict))
            )
            self.save(meta, metrics)
        return BenchResult(meta, tuple(metrics), stopped, self.folder(meta.bench_id))

    def save(self, meta: BenchMeta, metrics: Sequence[RunMetrics]) -> None:
        folder = self.folder(meta.bench_id)
        document = {"meta": asdict(meta), "runs": [asdict(item) for item in metrics]}
        self._workspace.write_text(f"{folder}/results.json", json.dumps(document, indent=2) + "\n")
        self._workspace.write_text(LATEST, meta.bench_id + "\n")

    def load(self, bench_id: str = "") -> BenchResult | None:
        return load_bench(self._workspace, bench_id)

    def report(self, result: BenchResult, folder: str = "") -> str:
        target = folder or result.folder
        summary = summarize(result.metrics)
        text = report_markdown(result.meta, summary, result.metrics)
        self._workspace.write_text(f"{target}/report.md", text)
        for name, svg in charts(summary).items():
            self._workspace.write_text(f"{target}/{name}", svg + "\n")
        return text

    def publish(self, result: BenchResult, folder: str = DOCS_DIR, readme: str = README) -> None:
        self.report(result, folder)
        summary = summarize(result.metrics)
        spent = sum(item.cost_usd or 0.0 for item in result.metrics)
        section = readme_section(result.meta, summary, spent, folder)
        current = self._workspace.read_text(readme) or ""
        self._workspace.write_text(readme, splice(current, section))
