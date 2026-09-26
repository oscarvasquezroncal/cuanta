from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.application.bench import BenchResult
    from cuanta.cli.document import Block, Document
    from cuanta.domain.bench import Condition

bench_app = typer.Typer(
    help="Benchmark cuanta against plain claude -p on fixed tasks.", no_args_is_help=True
)

DEFAULT_TASKS = Path("bench") / "tasks"
DEFAULT_FIXTURES = Path("tests") / "fixtures" / "repos"


@bench_app.command("run", help="Run every task and condition; --yes spends.")
def run_command(
    ctx: typer.Context,
    suite: Annotated[str, typer.Option("--suite", help="mini or full.")] = "mini",
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
    task: Annotated[
        list[str] | None, typer.Option("--task", help="Only this task (repeatable).")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Spend: run the bench for real.")] = False,
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
) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.commands.route import check_choice
    from cuanta.cli.document import Document, Hint, KeyValues, Line
    from cuanta.domain.bench import SESSION_PROFILES, BenchMeta, sessions_for
    from cuanta.domain.errors import DomainFailure, NotAvailable
    from cuanta.domain.progress import Status

    conditions = parse_conditions(conditions_text)
    tasks_path = _resolve(session, tasks_dir, DEFAULT_TASKS)
    container = Container.for_project(session.project)
    check_choice(profile, SESSION_PROFILES, "--session")
    tasks = container.bench_tasks(tasks_path, suite)
    if only:
        tasks = tuple(item for item in tasks if item.name in only)
    if not tasks:
        raise DomainFailure(f"no bench tasks in suite {suite}", "check --suite and --tasks")
    engine = container.engine("claude")
    if engine is None or not engine.available():
        raise NotAvailable("claude not found on PATH", "install Claude Code")
    runs = len(tasks) * len(conditions) * reps * len(sessions_for(profile))
    ceiling = runs * per_run_usd
    if budget_usd > 0:
        ceiling = min(ceiling, budget_usd)
    plan = KeyValues(
        (
            ("suite", f"{suite} ({len(tasks)} tasks)"),
            ("conditions", ", ".join(item.value for item in conditions)),
            ("session", profile),
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
    )
    runner = container.bench_runner(
        _resolve(session, fixtures, DEFAULT_FIXTURES),
        tasks_path.parent / "kit",
        model,
        None,
        keep,
    )
    result = runner.run(meta, tasks, conditions, session.presenter)
    runner.report(result)
    blocks: list[Block] = [plan, *_summary(result)]
    if result.stopped_early:
        blocks.append(Line("bench budget reached: remaining runs skipped", Status.WARN))
    blocks.append(Hint(f"report: {result.folder}/report.md · cuanta bench report --readme"))
    return Document(blocks=tuple(blocks), payload=_payload(result))


def _summary(result: "BenchResult") -> "tuple[Block, ...]":
    from cuanta.cli.document import Column, Line, Table
    from cuanta.cli.fmt import usd
    from cuanta.domain.bench import summarize
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
    return (table, Line(f"total spent {usd(spent)}"))


def _payload(result: "BenchResult") -> dict[str, object]:
    from dataclasses import asdict

    from cuanta.domain.bench import summarize
    from cuanta.domain.costs import sum_costs

    return {
        "ran": True,
        "bench_id": result.meta.bench_id,
        "folder": result.folder,
        "stopped_early": result.stopped_early,
        "spent_usd": sum_costs(item.cost_usd for item in result.metrics),
        "conditions": [
            {
                "condition": row.condition.value,
                "session": row.session,
                "runs": row.runs,
                "accepted": row.accepted,
                "tokens_per_accepted_median": row.tokens_per_accepted.median,
                "cost_median": row.cost.median,
            }
            for row in summarize(result.metrics)
        ],
        "runs": [
            {**asdict(item), "condition": item.condition.value, "models": list(item.models)}
            for item in result.metrics
        ],
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
