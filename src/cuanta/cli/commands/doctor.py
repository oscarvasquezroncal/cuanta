from typing import TYPE_CHECKING

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document


def doctor_command(ctx: typer.Context) -> None:
    execute(ctx, _doctor)


def _doctor(session: Session) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import (
        Block,
        Column,
        Commands,
        Document,
        Hint,
        Line,
        MascotBlock,
        Panel,
        Table,
    )
    from cuanta.cli.views import detection_payload
    from cuanta.domain.progress import Status
    from cuanta.domain.voice import Mood, glyph

    report = Container.for_project(session.project).doctor().run()
    unicode = session.settings.unicode
    rows = tuple(
        (glyph(check.status, unicode), check.name, check.detail) for check in report.checks
    )
    table = Table("", (Column(""), Column("check"), Column("detail")), rows)
    fixes = Commands(
        "fixes",
        tuple((check.name, check.fix) for check in report.checks if check.fix),
    )
    failures = report.count(Status.FAIL)
    warnings = report.count(Status.WARN)
    if failures:
        mood, verdict, status = Mood.ALARMED, f"{failures} failing · hiss", Status.FAIL
    elif warnings:
        mood, verdict, status = Mood.WATCHING, f"{warnings} to look at", Status.WARN
    else:
        mood, verdict, status = Mood.HAPPY, "all green · purr", Status.OK
    summary = Panel(
        "doctor",
        (MascotBlock(mood), Line(verdict, status), Hint("cuanta init sets up anything missing")),
    )
    payload = {
        "healthy": report.healthy,
        "checks": [
            {
                "name": check.name,
                "status": check.status.value,
                "detail": check.detail,
                "fix": check.fix,
            }
            for check in report.checks
        ],
        "detection": detection_payload(report.detection),
    }
    blocks: tuple[Block, ...] = (table, fixes, summary) if fixes.items else (table, summary)
    return Document(blocks=blocks, payload=payload, exit_code=0 if report.healthy else 1)
