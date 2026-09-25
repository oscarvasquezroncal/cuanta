from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Block, Document


class Scope(StrEnum):
    PROJECT = "project"
    USER = "user"


def init_command(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="Project folder (default: cwd).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print planned changes only.")] = False,
    engine: Annotated[str, typer.Option("--engine", help="Engine that runs Forge.")] = "claude",
    scope: Annotated[
        Scope, typer.Option("--scope", help="Forge install location.")
    ] = Scope.PROJECT,
    skip_forge: Annotated[bool, typer.Option("--skip-forge", help="Skip the Forge stage.")] = False,
    skip_telemetry: Annotated[
        bool, typer.Option("--skip-telemetry", help="Skip telemetry wiring.")
    ] = False,
) -> None:

    def action(session: Session) -> "Document":
        target = path.resolve() if path is not None else session.project
        if not target.is_dir():
            from cuanta.domain.errors import EnvironmentFailure

            raise EnvironmentFailure(f"not a folder: {target}")
        return _run(
            replace(session, project=target),
            dry_run,
            engine,
            scope.value,
            skip_forge,
            skip_telemetry,
        )

    execute(ctx, action)


def _run(
    session: Session,
    dry_run: bool,
    engine: str,
    scope: str,
    skip_forge: bool,
    skip_telemetry: bool,
) -> "Document":
    from cuanta.application.init_project import InitOptions
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, KeyValues, Line, MascotBlock, Panel
    from cuanta.cli.output import OutputMode
    from cuanta.cli.views import detection_payload
    from cuanta.domain.detection import summary_lines
    from cuanta.domain.messages import english
    from cuanta.domain.progress import Status
    from cuanta.domain.voice import Mood

    if engine != "claude":
        from cuanta.domain.errors import NotAvailable

        raise NotAvailable(f"init runs Forge with claude only; got {engine}", "use --engine claude")
    container = Container.for_project(session.project)
    if session.settings.mode is OutputMode.PRETTY:
        session.presenter.render(
            Document(blocks=(MascotBlock(Mood.WATCHING, f"init · {session.project.name}"),))
        )
    consent = dry_run or _telemetry_consent(session, skip_telemetry)
    use_case = container.init_project(session.presenter, telemetry_consent=consent, scope=scope)
    try:
        report = use_case.run(
            InitOptions(dry_run=dry_run, skip_forge=skip_forge, skip_telemetry=skip_telemetry)
        )
    finally:
        container.close()
    context = report.context
    detection = context.detection
    lines = summary_lines(detection, session.settings.unicode)
    rows = [
        (key, value) if key != "telemetry:" else (key, context.telemetry_line)
        for key, value in lines
    ]
    blocks: list[Block] = [KeyValues(tuple(rows))]
    if context.graph_line:
        blocks.append(KeyValues((("graph:", context.graph_line),)))
    if context.registration:
        registration_status = Status.WARN if "deferred" in context.registration else Status.OK
        blocks.append(Line(f"registration: {context.registration}", registration_status))
    if dry_run:
        planned = tuple(Line(english(change), Status.INFO) for change in context.planned)
        blocks.append(Panel("dry run · nothing written", planned or (Line("nap: nothing to do"),)))
    for finding in context.verify_lines:
        blocks.append(Line(finding.text, finding.status))
    for new_file in context.new_files:
        blocks.append(Line(f"kept yours, wrote {new_file}", Status.WARN))
    ok = report.ok
    mood = Mood.HAPPY if ok else Mood.ALARMED
    next_hint = "cuanta init" if dry_run else ("cuanta test" if ok else "cuanta doctor")
    final = Panel(
        "purr" if ok else "hiss",
        (
            MascotBlock(mood),
            Line(
                "init complete" if ok else "init finished with problems",
                Status.OK if ok else Status.FAIL,
            ),
            Hint(f"next: {next_hint}"),
        ),
    )
    blocks.append(final)
    payload = {
        "ok": ok,
        "dry_run": dry_run,
        "scope": scope,
        "detection": detection_payload(detection),
        "stages": [
            {"stage": key, "status": result.status.value, "detail": result.detail}
            for key, result in report.stages
        ],
        "resumed_from": report.resumed_from,
        "graph": context.graph_line,
        "telemetry": context.telemetry_line,
        "planned": [english(change) for change in context.planned],
        "new_files": context.new_files,
        "verify": [
            {"status": finding.status.value, "text": finding.text}
            for finding in context.verify_lines
        ],
        "run_id": context.run_id,
        "cost_usd": context.cost_usd,
        "registration": context.registration,
        "denials": context.denials,
        "next": next_hint,
    }
    return Document(blocks=tuple(blocks), payload=payload, exit_code=0 if ok else 1)


def _telemetry_consent(session: Session, skipped: bool) -> bool:
    if skipped:
        return False
    if session.options.yes:
        return True
    if not session.interactive:
        return False
    return typer.confirm(
        "Wire local telemetry (127.0.0.1 only) for runs cuanta launches?", default=True
    )
