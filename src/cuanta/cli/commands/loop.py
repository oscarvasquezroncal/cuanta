from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.application.loop import LoopReport
    from cuanta.cli.document import Block, Document


def loop_command(
    ctx: typer.Context,
    max_iterations: Annotated[
        int, typer.Option("--max-iterations", help="Stop after this many fixes.")
    ] = 3,
    budget_usd: Annotated[
        float, typer.Option("--budget-usd", help="Stop once the loop's runs cost this much.")
    ] = 0.0,
    engine: Annotated[str, typer.Option("--engine", help="claude, codex or opencode.")] = "",
    model: Annotated[str, typer.Option("--model", help="Model for each fix.")] = "",
) -> None:
    execute(ctx, lambda session: _loop(session, max_iterations, budget_usd, engine, model))


def _loop(
    session: Session, max_iterations: int, budget_usd: float, engine: str, model: str
) -> "Document":
    from cuanta.application.loop import FixLoop, FixStep, TestStep
    from cuanta.bootstrap import Container
    from cuanta.cli.commands.mandate import MandateArgs, run_mandate_core
    from cuanta.domain.errors import NotAvailable
    from cuanta.domain.ledger import Run
    from cuanta.domain.loop import LOOP_OUT_OF_SCOPE, loop_gate

    container = Container.for_project(session.project)
    detection = container.detector().run(with_engines=False)
    loop_text = container.workspace().read_text("docs/LOOP.md")
    gate = loop_gate(detection.verify_tier, detection.verify.evidence, loop_text)
    if not gate.allowed:
        raise NotAvailable(
            f"whiskers too short: {gate.missing}", "cuanta doctor shows which sensor is missing"
        )
    ledger = container.shared_ledger()
    loop_id = container.new_run_id()
    started = container.clock.now_iso()
    ledger.add_run(Run(id=loop_id, kind="loop", started_at=started, status="running"))
    budget = budget_usd or container.config.budget_usd
    gateway = container.gateway(ledger)

    def run_tests(run_id: str) -> TestStep:
        report = gateway.run(
            detection.stack, container.config, detection.verify_tier, run_id=run_id
        )
        return TestStep(report.status.value, len(report.signatures))

    def fix(run_id: str, _: int) -> FixStep:
        args = MandateArgs(
            type="bug",
            out_of_scope=LOOP_OUT_OF_SCOPE,
            from_failure=True,
            engine=engine,
            model=model,
            parent=run_id,
        )
        report = run_mandate_core(session, container, args, verdict=False)
        return FixStep(report.run.id, report.ok, report.run.cost_usd)

    try:
        outcome = FixLoop(run_tests, fix, session.presenter).run(loop_id, max_iterations, budget)
        ledger.update_run(
            Run(
                id=loop_id,
                kind="loop",
                started_at=started,
                ended_at=container.clock.now_iso(),
                status=outcome.stop.value,
                cost_usd=outcome.spent_usd,
            )
        )
    finally:
        container.close()
    return _document(outcome)


def _document(outcome: "LoopReport") -> "Document":
    from cuanta.cli.document import Column, Document, KeyValues, Line, MascotBlock, Panel, Table
    from cuanta.cli.fmt import usd
    from cuanta.domain.progress import Status
    from cuanta.domain.voice import Mood

    rows = tuple(
        (
            str(item.number),
            item.test_status,
            str(item.signatures),
            item.mandate_run,
            usd(item.cost_usd),
        )
        for item in outcome.iterations
    )
    mood = Mood.HAPPY
    if not outcome.ok:
        mood = Mood.ALARMED if outcome.iterations else Mood.SLEEPY
    blocks: list[Block] = []
    if rows:
        columns = (
            Column("#"),
            Column("tests"),
            Column("hairballs", numeric=True),
            Column("mandate"),
            Column("cost", numeric=True),
        )
        blocks.append(Table("iterations", columns, rows))
    status = Status.OK if outcome.ok else Status.WARN
    blocks.append(
        Panel(
            "purr" if outcome.ok else "hiss",
            (
                MascotBlock(mood),
                Line(f"stopped: {outcome.stop.value}", status),
                KeyValues((("loop", outcome.loop_id), ("spent", usd(outcome.spent_usd)))),
            ),
        )
    )
    payload = {
        "loop_id": outcome.loop_id,
        "stop": outcome.stop.value,
        "spent_usd": outcome.spent_usd,
        "final_status": outcome.final_status,
        "iterations": [
            {
                "number": item.number,
                "test_status": item.test_status,
                "signatures": item.signatures,
                "mandate_run": item.mandate_run,
                "ok": item.mandate_ok,
                "cost_usd": item.cost_usd,
            }
            for item in outcome.iterations
        ],
    }
    return Document(blocks=tuple(blocks), payload=payload, exit_code=0 if outcome.ok else 1)
