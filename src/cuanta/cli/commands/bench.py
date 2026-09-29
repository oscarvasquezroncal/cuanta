from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.application.bench import BenchResult
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Block, Document
    from cuanta.domain.bench import BenchTask, Condition

bench_app = typer.Typer(
    help="Benchmark cuanta against plain claude -p on fixed tasks.", no_args_is_help=True
)

DEFAULT_TASKS = Path("bench") / "tasks"
DEFAULT_FIXTURES = Path("tests") / "fixtures" / "repos"


@bench_app.command("run", help="Run every task and condition; --yes spends.")
def run_command(
    ctx: typer.Context,
    suite: Annotated[
        str, typer.Option("--suite", help="mini, full, index, or investigation.")
    ] = "mini",
    reps: Annotated[int, typer.Option("--reps", min=1, help="Repetitions per task.")] = 3,
    budget_usd: Annotated[
        float, typer.Option("--budget-usd", min=0.0, help="Cap for the whole bench.")
    ] = 40.0,
    per_run_usd: Annotated[
        float, typer.Option("--per-run-usd", min=0.01, help="Cap for each run.")
    ] = 3.0,
    model: Annotated[str, typer.Option("--model", help="Main model, pinned.")] = "sonnet",
    conditions: Annotated[
        str, typer.Option("--conditions", help="Comma list: baseline,cuanta,cuanta-routed.")
    ] = "",
    seed: Annotated[int, typer.Option("--seed", help="Order seed; 0 picks one.")] = 0,
    tasks_dir: Annotated[
        Path | None, typer.Option("--tasks", help="Task folder; the kit sits next to it.")
    ] = None,
    fixtures: Annotated[Path | None, typer.Option("--fixtures", help="Fixture repos.")] = None,
    keep: Annotated[bool, typer.Option("--keep", help="Keep each run's repo copy.")] = False,
    profile: Annotated[
        str, typer.Option("--session", help="lean (default), full, or both to compare them.")
    ] = "lean",
    index: Annotated[
        str, typer.Option("--index", help="on or off for Cuanta conditions; baseline is off.")
    ] = "on",
    shape: Annotated[
        str, typer.Option("--shape", help="single or pipeline; omit for the mandate default.")
    ] = "",
    pack: Annotated[
        str, typer.Option("--pack", help="on or off for automatic Cuanta context packs.")
    ] = "on",
    depth: Annotated[
        str, typer.Option("--depth", help="quick, normal, or deep; omit for the default.")
    ] = "",
    task: Annotated[
        list[str] | None, typer.Option("--task", help="Only this task (repeatable).")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Spend: run the bench for real.")] = False,
    compare: Annotated[
        str,
        typer.Option(
            "--compare", help="read-discipline, scout, warm-queue or finish: run that comparison."
        ),
    ] = "",
    arm_cap: Annotated[
        list[str] | None,
        typer.Option("--arm-cap", help="Cap per run of one arm, ARM=USD (repeatable)."),
    ] = None,
    overshoot_usd: Annotated[
        float | None,
        typer.Option(
            "--overshoot-usd",
            min=0.0,
            help="With --compare: USD one run may pass its cap by; default: largest measured.",
        ),
    ] = None,
) -> None:
    execute(
        ctx,
        lambda session: _run(
            session,
            suite,
            reps,
            budget_usd,
            per_run_usd,
            model,
            conditions,
            seed,
            tasks_dir,
            fixtures,
            keep,
            yes,
            profile,
            tuple(task or ()),
            index,
            shape,
            pack,
            depth,
            compare,
            tuple(arm_cap or ()),
            overshoot_usd,
        ),
    )


@bench_app.command("report", help="Write the Markdown report and SVG charts of a bench.")
def report_command(
    ctx: typer.Context,
    bench_id: Annotated[str, typer.Option("--id", help="Bench id; the latest by default.")] = "",
    readme: Annotated[
        bool, typer.Option("--readme", help="Also update docs/bench and the README section.")
    ] = False,
) -> None:
    execute(ctx, lambda session: _report(session, bench_id, readme))


def parse_conditions(text: str) -> "tuple[Condition, ...]":
    from cuanta.domain.bench import CONDITIONS, Condition
    from cuanta.domain.errors import DomainFailure

    if not text.strip():
        return CONDITIONS
    names = {item.value for item in CONDITIONS}
    chosen: list[Condition] = []
    for part in text.split(","):
        name = part.strip()
        if name not in names:
            raise DomainFailure(f"unknown condition {name}", f"use {', '.join(sorted(names))}")
        chosen.append(Condition(name))
    return tuple(chosen)


def _resolve(session: Session, value: Path | None, default: Path) -> Path:
    if value is None:
        return session.project / default
    return value if value.is_absolute() else session.project / value


def _run(
    session: Session,
    suite: str,
    reps: int,
    budget_usd: float,
    per_run_usd: float,
    model: str,
    conditions_text: str,
    seed: int,
    tasks_dir: Path | None,
    fixtures: Path | None,
    keep: bool,
    yes: bool,
    profile: str = "lean",
    only: tuple[str, ...] = (),
    index: str = "on",
    shape: str = "",
    pack: str = "on",
    depth: str = "",
    compare: str = "",
    arm_cap: tuple[str, ...] = (),
    overshoot_usd: float | None = None,
) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.commands.route import check_choice
    from cuanta.cli.document import Document, Hint, KeyValues, Line
    from cuanta.domain.bench import INDEX_MODES, SESSION_PROFILES, BenchMeta, sessions_for
    from cuanta.domain.depth import DEPTHS
    from cuanta.domain.errors import DomainFailure, NotAvailable
    from cuanta.domain.mandate import Shape
    from cuanta.domain.progress import Status

    conditions = parse_conditions(conditions_text)
    tasks_path = _resolve(session, tasks_dir, DEFAULT_TASKS)
    container = Container.for_project(session.project)
    check_choice(profile, SESSION_PROFILES, "--session")
    check_choice(index, INDEX_MODES, "--index")
    check_choice(shape, (Shape.SINGLE.value, Shape.PIPELINE.value), "--shape")
    check_choice(pack, INDEX_MODES, "--pack")
    if pack not in INDEX_MODES:
        raise DomainFailure(f"unknown --pack {pack}", "use one of on, off")
    check_choice(depth, tuple(DEPTHS), "--depth")
    tasks = container.bench_tasks(tasks_path, suite)
    if only:
        tasks = tuple(item for item in tasks if item.name in only)
    if not tasks:
        raise DomainFailure(f"no bench tasks in suite {suite}", "check --suite and --tasks")
    engine = container.engine("claude")
    if engine is None or not engine.available():
        raise NotAvailable("claude not found on PATH", "install Claude Code")
    if arm_cap and not compare:
        raise DomainFailure("--arm-cap needs --compare", "add --compare or drop --arm-cap")
    if overshoot_usd is not None and not compare:
        raise DomainFailure(
            "--overshoot-usd needs --compare", "add --compare or drop --overshoot-usd"
        )
    if compare:
        return _run_proof(
            session,
            container,
            engine.version(),
            tasks,
            ProofOptions(
                suite,
                reps,
                budget_usd,
                per_run_usd,
                model,
                conditions_text,
                seed,
                fixtures,
                tasks_path,
                keep,
                yes,
                profile,
                index,
                shape,
                pack,
                depth,
                compare,
                arm_cap,
                overshoot_usd,
            ),
        )
    runs = len(tasks) * len(conditions) * reps * len(sessions_for(profile))
    ceiling = runs * per_run_usd
    if budget_usd > 0:
        ceiling = min(ceiling, budget_usd)
    plan = KeyValues(
        (
            ("suite", f"{suite} ({len(tasks)} tasks)"),
            ("conditions", ", ".join(item.value for item in conditions)),
            ("session", profile),
            ("index", index),
            ("shape", shape or "default"),
            ("pack", pack),
            ("depth", depth or "default"),
            ("runs", f"{runs} ({reps} per task and condition)"),
            ("engine", f"claude {engine.version()} · main model {model}"),
            ("spend", f"at most ${ceiling:,.2f} (${per_run_usd:,.2f} per run)"),
        )
    )
    if not (yes or session.options.yes):
        return Document(
            blocks=(plan, Hint("nothing spent · add --yes to run it")),
            payload={"ran": False, "runs": runs, "ceiling_usd": ceiling},
        )
    now = container.clock.now_iso()
    bench_id = "".join(char for char in now[:19] if char.isalnum())
    meta = BenchMeta(
        bench_id=bench_id,
        suite=suite,
        reps=reps,
        seed=seed or container.clock.now_ms() % 1_000_000,
        engine="claude",
        engine_version=engine.version(),
        model=model,
        started_at=now,
        budget_usd=budget_usd,
        per_run_usd=per_run_usd,
        tasks=tuple(task.name for task in tasks),
        session=profile,
        index=index,
        shape=shape,
        pack=pack,
        depth=depth,
    )
    runner = container.bench_runner(
        _resolve(session, fixtures, DEFAULT_FIXTURES),
        tasks_path.parent / "kit",
        model,
        None,
        keep,
        shape=shape,
        pack=pack,
        depth=depth,
    )
    result = runner.run(meta, tasks, conditions, session.presenter)
    runner.report(result)
    blocks: list[Block] = [plan, *_summary(result)]
    if result.stopped_early:
        blocks.append(Line("bench budget reached: remaining runs skipped", Status.WARN))
    blocks.append(Hint(f"report: {result.folder}/report.md · cuanta bench report --readme"))
    return Document(blocks=tuple(blocks), payload=_payload(result))


@dataclass(frozen=True, slots=True)
class ProofOptions:
    suite: str
    reps: int
    budget_usd: float
    per_run_usd: float
    model: str
    conditions: str
    seed: int
    fixtures: Path | None
    tasks_path: Path
    keep: bool
    yes: bool
    profile: str
    index: str
    shape: str
    pack: str
    depth: str
    compare: str
    arm_cap: tuple[str, ...]
    overshoot_usd: float | None = None


def _run_proof(
    session: Session,
    container: "Container",
    version: str,
    tasks: "tuple[BenchTask, ...]",
    options: ProofOptions,
) -> "Document":
    from cuanta.cli.document import Column, Document, Hint, KeyValues, Line, Table
    from cuanta.domain.bench import BOTH_SESSIONS, BenchMeta
    from cuanta.domain.bench_proof import (
        ARMS,
        PAIRED,
        Comparison,
        arm_caps,
        arm_settings,
        parse_comparison,
        plan_proof,
        worst_case,
    )
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.progress import Status

    comparison = parse_comparison(options.compare)
    caps = arm_caps(options.arm_cap, comparison)
    if options.conditions.strip():
        raise DomainFailure(
            "--conditions does not apply to --compare", "each comparison runs its own arms"
        )
    if options.profile == BOTH_SESSIONS:
        raise DomainFailure("--compare runs one session profile", "use --session lean or full")
    if comparison is Comparison.SCOUT and options.shape:
        raise DomainFailure("--compare scout sets the shape of each arm", "drop --shape")
    arms = ARMS[comparison]
    preview = plan_proof(
        tasks,
        comparison,
        options.reps,
        options.seed,
        caps,
        options.per_run_usd,
        options.profile,
        options.index,
    )
    total = sum(item.cap_usd for item in preview)
    overshoot = (
        container.bench_overshoot() if options.overshoot_usd is None else options.overshoot_usd
    )
    worst = worst_case(preview, options.budget_usd, overshoot)
    paired = "; each pair runs back to back in one copy" if comparison in PAIRED else ""
    plan = KeyValues(
        (
            ("suite", f"{options.suite} ({len(tasks)} tasks)"),
            ("comparison", f"{comparison.value}: {', '.join(arm.name for arm in arms)}"),
            ("session", options.profile),
            ("index", options.index),
            ("shape", options.shape or "default"),
            ("pack", options.pack),
            ("depth", options.depth or "default"),
            ("runs", f"{len(preview)} ({options.reps} per task and arm{paired})"),
            ("engine", f"claude {version} · main model {options.model}"),
            (
                "caps",
                ", ".join(
                    f"{arm.name} ${caps.get(arm.name, options.per_run_usd):,.2f}" for arm in arms
                ),
            ),
            ("arm config", "; ".join(f"{arm.name}: {arm_settings(arm)}" for arm in arms)),
            (
                "spend",
                f"at most ${worst:,.2f} if no run passes its cap by more than "
                f"${overshoot:,.2f} (per-run caps add to ${total:,.2f}; "
                f"bench cap ${options.budget_usd:,.2f})",
            ),
        )
    )
    planned_table = Table(
        "planned runs",
        (Column("task"), Column("arm"), Column("rep", numeric=True), Column("cap", numeric=True)),
        tuple(
            (item.task, item.arm, str(item.rep), f"${item.cap_usd:,.2f}")
            for item in sorted(preview, key=lambda run: (run.arm, run.task, run.rep))
        ),
    )
    if not (options.yes or session.options.yes):
        return Document(
            blocks=(
                plan,
                planned_table,
                Hint(
                    "nothing spent · add --yes to run it · the order is shuffled at run time "
                    "· a comparison starts only when the bench cap covers all its arms"
                ),
            ),
            payload={
                "ran": False,
                "comparison": comparison.value,
                "runs": len(preview),
                "ceiling_usd": round(worst, 6),
                "caps_total_usd": total,
                "overshoot_usd": overshoot,
                "planned": [
                    {"task": item.task, "arm": item.arm, "rep": item.rep, "cap_usd": item.cap_usd}
                    for item in preview
                ],
            },
        )
    now = container.clock.now_iso()
    meta = BenchMeta(
        bench_id="".join(char for char in now[:19] if char.isalnum()),
        suite=options.suite,
        reps=options.reps,
        seed=options.seed or container.clock.now_ms() % 1_000_000,
        engine="claude",
        engine_version=version,
        model=options.model,
        started_at=now,
        budget_usd=options.budget_usd,
        per_run_usd=options.per_run_usd,
        tasks=tuple(task.name for task in tasks),
        session=options.profile,
        index=options.index,
        shape=options.shape,
        pack=options.pack,
        depth=options.depth,
    )
    planned = plan_proof(
        tasks,
        comparison,
        options.reps,
        meta.seed,
        caps,
        options.per_run_usd,
        options.profile,
        options.index,
    )
    runner = container.bench_runner(
        _resolve(session, options.fixtures, DEFAULT_FIXTURES),
        options.tasks_path.parent / "kit",
        options.model,
        None,
        options.keep,
        shape=options.shape,
        pack=options.pack,
        depth=options.depth,
    )
    result = runner.run_proof(meta, tasks, planned, session.presenter)
    runner.report(result)
    blocks: list[Block] = [plan, *_summary(result)]
    if result.stopped_early:
        blocks.append(Line("bench budget reached: remaining runs skipped", Status.WARN))
    blocks.append(Hint(f"report: {result.folder}/report.md · cuanta bench report"))
    return Document(blocks=tuple(blocks), payload=_payload(result))


def _summary(result: "BenchResult") -> "tuple[Block, ...]":
    from cuanta.cli.document import Column, Line, Table
    from cuanta.cli.fmt import usd
    from cuanta.domain.bench import summarize
    from cuanta.domain.bench_proof import targets
    from cuanta.domain.costs import sum_costs

    rows = summarize(result.metrics)
    spent = sum_costs(item.cost_usd for item in result.metrics)
    table = Table(
        "per condition",
        (
            Column("condition"),
            Column("accepted", numeric=True),
            Column("tokens/accepted", numeric=True),
            Column("median cost", numeric=True),
            Column("spent", numeric=True),
        ),
        tuple(
            (
                row.label,
                f"{row.accepted}/{row.runs}",
                f"{row.tokens_per_accepted.median:,.0f}"
                if row.tokens_per_accepted.median is not None
                else "n/a",
                usd(row.cost.median),
                usd(row.spent_usd),
            )
            for row in rows
        ),
    )
    found = targets(result.metrics)
    if not found:
        return (table, Line(f"total spent {usd(spent)}"))
    goals = Table(
        "targets",
        (Column("target"), Column("verdict"), Column("measured")),
        tuple((item.comparison.value, item.verdict.value, item.summary) for item in found),
    )
    return (table, Line(f"total spent {usd(spent)}"), goals)


def _payload(result: "BenchResult") -> dict[str, object]:
    from cuanta.application.bench import stored_run
    from cuanta.domain.bench import summarize
    from cuanta.domain.bench_proof import targets, targets_payload
    from cuanta.domain.costs import sum_costs

    found = targets(result.metrics)
    extra: dict[str, object] = {"targets": targets_payload(found)} if found else {}
    return {
        "ran": True,
        "index": result.meta.index,
        "shape": result.meta.shape,
        "pack": result.meta.pack,
        "depth": result.meta.depth,
        "bench_id": result.meta.bench_id,
        "folder": result.folder,
        "stopped_early": result.stopped_early,
        "spent_usd": sum_costs(item.cost_usd for item in result.metrics),
        "conditions": [
            {
                "condition": row.condition.value,
                "session": row.session,
                "index": row.index,
                "shape": row.shape,
                "pack": row.pack,
                "depth": row.depth,
                "runs": row.runs,
                "accepted": row.accepted,
                "tokens_per_accepted_median": row.tokens_per_accepted.median,
                "cost_median": row.cost.median,
            }
            for row in summarize(result.metrics)
        ],
        "runs": [
            {**stored_run(item), "condition": item.condition.value, "models": list(item.models)}
            for item in result.metrics
        ],
        **extra,
    }


def _report(session: Session, bench_id: str, readme: bool) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint
    from cuanta.domain.errors import DomainFailure

    container = Container.for_project(session.project)
    runner = container.bench_runner(
        session.project / DEFAULT_FIXTURES, session.project / "bench" / "kit", "", None, False
    )
    result = runner.load(bench_id)
    if result is None:
        raise DomainFailure("no bench results found", "run: cuanta bench run --yes")
    runner.report(result)
    if readme:
        runner.publish(result)
    where = "docs/bench and README.md" if readme else f"{result.folder}/report.md"
    blocks: list[Block] = [*_summary(result), Hint(f"written: {where}")]
    return Document(blocks=tuple(blocks), payload=_payload(result))
