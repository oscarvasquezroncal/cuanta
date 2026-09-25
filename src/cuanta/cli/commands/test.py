import os
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Block, Document


def gateway_command(
    ctx: typer.Context,
    affected: Annotated[
        bool,
        typer.Option(
            "--affected",
            help="Run only tests related to files changed since the last full run "
            "(last failed first); the full suite still decides the final verdict.",
        ),
    ] = False,
) -> None:
    execute(ctx, lambda session: _test(session, affected))


def _test(session: Session, affected: bool = False) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Hint, Line, Table
    from cuanta.domain.messages import english
    from cuanta.domain.progress import Status, StepFinished, StepStarted
    from cuanta.domain.testing import GatewayStatus
    from cuanta.domain.voice import hairball_phrase, separator

    container = Container.for_project(session.project)
    detection = container.detector().run(with_engines=False)
    ledger = container.ledger()
    try:
        gateway = container.gateway(ledger)
        choice, _ = gateway.choose(detection.stack, container.config, detection.verify_tier)
        scope = "affected tests" if affected else "once"
        session.presenter.publish(StepStarted("test", f"running {choice.name} {scope}"))
        scoped = container.affected_gateway(ledger).run(
            detection.stack,
            container.config,
            detection.verify_tier,
            affected,
            run_id=os.environ.get("CUANTA_RUN_ID", ""),
        )
    finally:
        ledger.close()
    report = scoped.report
    selection = scoped.selection
    outcome = report.outcome
    dot = separator(session.settings.unicode)
    green = report.status is GatewayStatus.GREEN
    session.presenter.publish(
        StepFinished("test", Status.OK if green else Status.FAIL, f"{outcome.duration_s:.1f}s")
    )
    blocks: list[Block] = []
    if selection is not None:
        scope_status = Status.INFO if not selection.full else Status.SKIP
        blocks.append(Line(f"scope: {english(selection.reason)}", scope_status))
    if green:
        blocks.append(Line(f"{outcome.passed:,} passed {dot} purr", Status.OK))
    else:
        failed = outcome.failed + outcome.errored
        triage = dict(report.triage)
        text = f"{hairball_phrase(failed, len(report.signatures))} {dot} hiss"
        blocks.append(Line(text, Status.FAIL))
        rows = tuple(
            (
                signature.id,
                f"{signature.tests:,}",
                signature.location or "-",
                triage.get(signature.id, "-"),
                signature.verbatim[:72],
            )
            for signature in report.signatures
        )
        blocks.append(
            Table(
                "hairballs",
                (
                    Column("signature"),
                    Column("tests", numeric=True),
                    Column("location"),
                    Column("triage"),
                    Column("error"),
                ),
                rows,
            )
        )
        if report.persistent:
            blocks.append(
                Line(
                    f"persistent: {', '.join(report.persistent)} seen twice in run {report.run_id}",
                    Status.WARN,
                )
            )
        blocks.append(Hint(f"cuanta cat {report.capsule} --level L2"))
    payload = report.contract()
    if selection is not None:
        payload["scope"] = {
            "mode": "full" if selection.full else "affected",
            "reason": english(selection.reason),
            "changed": list(selection.changed),
            "tests": list(selection.tests),
        }
    return Document(
        blocks=tuple(blocks),
        payload=payload,
        exit_code=0 if green else 1,
    )
