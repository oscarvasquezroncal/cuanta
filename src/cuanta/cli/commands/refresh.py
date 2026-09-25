from typing import TYPE_CHECKING

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Block, Document


def refresh_command(ctx: typer.Context) -> None:
    execute(ctx, _refresh)


def _refresh(session: Session) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, KeyValues, Line, MascotBlock, Panel
    from cuanta.domain.progress import Status
    from cuanta.domain.voice import Mood

    container = Container.for_project(session.project)
    try:
        report = container.refresh_project(session.presenter).run()
    finally:
        container.close()
    context = report.context
    blocks: list[Block] = [
        Line(report.transition, Status.WARN if report.tier_changed else Status.OK),
        KeyValues(
            tuple(
                (key, f"{result.status.value} · {result.detail}") for key, result in report.stages
            )
        ),
    ]
    blocks.extend(Line(finding.text, finding.status) for finding in context.verify_lines)
    ok = report.ok and context.verify_ok
    blocks.append(
        Panel(
            "purr" if ok else "hiss",
            (
                MascotBlock(Mood.HAPPY if ok else Mood.ALARMED),
                Hint("cuanta spectrum" if ok else "cuanta doctor"),
            ),
        )
    )
    payload = {
        "ok": ok,
        "transition": report.transition,
        "tier_changed": report.tier_changed,
        "verify_tier": report.detection.verify_tier.value,
        "stages": [
            {"stage": key, "status": result.status.value, "detail": result.detail}
            for key, result in report.stages
        ],
        "verify": [
            {"status": finding.status.value, "text": finding.text}
            for finding in context.verify_lines
        ],
        "new_files": context.new_files,
        "run_id": context.run_id,
    }
    return Document(blocks=tuple(blocks), payload=payload, exit_code=0 if ok else 1)
