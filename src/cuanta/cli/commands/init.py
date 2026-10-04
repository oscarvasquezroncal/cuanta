from collections.abc import Callable
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute, interrupted

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
    refresh_forge: Annotated[
        bool,
        typer.Option(
            "--refresh-forge",
            help="Run Forge again on a project it already initialized (uses the model).",
        ),
    ] = False,
    skip_graph: Annotated[
        bool, typer.Option("--skip-graph", help="Skip the graph bootstrap.")
    ] = False,
    template: Annotated[
        bool,
        typer.Option(
            "--template",
            help="Only write a missing docs/MANDATE_TEMPLATE.md from the vendored Forge copy.",
        ),
    ] = False,
) -> None:

    def action(session: Session) -> "Document":
        target = path.resolve() if path is not None else session.project
        if not target.is_dir():
            from cuanta.domain.errors import EnvironmentFailure

            raise EnvironmentFailure(f"not a folder: {target}")
        if refresh_forge and skip_forge:
            from cuanta.domain.errors import DomainFailure
            from cuanta.domain.messages import english, msg

            raise DomainFailure(
                english(msg("init.refresh_skip")), english(msg("init.refresh_skip_hint"))
            )
        if template:
            return _template(replace(session, project=target), dry_run)
        return _run(
            replace(session, project=target),
            engine,
            scope.value,
            dry_run,
            skip_forge,
            skip_telemetry,
            refresh_forge,
            skip_graph,
        )

    execute(ctx, action)


def _run(
    session: Session,
    engine: str,
    scope: str,
    dry_run: bool,
    skip_forge: bool,
    skip_telemetry: bool,
    refresh_forge: bool,
    skip_graph: bool,
) -> "Document":
    from cuanta.application.init_project import InitOptions, can_resume
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
    container = Container.for_project(session.project, verbose=session.options.verbose)
    if session.settings.mode is OutputMode.PRETTY:
        session.presenter.render(
            Document(blocks=(MascotBlock(Mood.WATCHING, f"init · {session.project.name}"),))
        )

    def wired() -> bool:
        return container.telemetry().wired_port("claude") is not None

    consent = dry_run or _telemetry_consent(session, skip_telemetry, wired)
    use_case = container.init_project(session.presenter, telemetry_consent=consent, scope=scope)
    options = InitOptions(
        dry_run=dry_run,
        skip_forge=skip_forge,
        skip_telemetry=skip_telemetry,
        refresh_forge=refresh_forge,
        skip_graph=skip_graph,
    )
    try:
        report = use_case.run(options)
        recorded = container.recorded_run()
    except KeyboardInterrupt as interrupt:
        resumable = not dry_run and can_resume(container.workspace())
        raise interrupted(interrupt, container.recorded_run(), resumable) from interrupt
    finally:
        container.close()
    context = report.context
    detection = context.detection
    lines = summary_lines(detection, session.settings.unicode, forge_runs=context.forge_runs)
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
        if finding.fix is not None:
            blocks.append(Hint(f"fix: {english(finding.fix)}"))
    ok = report.ok
    warnings = sum(1 for finding in context.verify_lines if finding.status is Status.WARN)
    final, next_hint = _verdict(ok, dry_run, report.seconds, warnings)
    blocks.append(final)
    payload = {
        "ok": ok,
        "dry_run": dry_run,
        "scope": scope,
        "detection": detection_payload(detection),
        "stages": [
            {
                "stage": key,
                "status": result.status.value,
                "detail": result.detail,
                "seconds": result.seconds,
            }
            for key, result in report.stages
        ],
        "seconds": report.seconds,
        "resumed_from": report.resumed_from,
        "graph": context.graph_line,
        "telemetry": context.telemetry_line,
        "planned": [english(change) for change in context.planned],
        "new_files": context.new_files,
        "verify": [
            {
                "status": finding.status.value,
                "text": finding.text,
                "fix": english(finding.fix) if finding.fix is not None else "",
            }
            for finding in context.verify_lines
        ],
        "run_id": context.run_id,
        "cost_usd": context.cost_usd,
        "registration": context.registration,
        "denials": context.denials,
        "next": next_hint,
    }
    return Document(
        blocks=tuple(blocks), payload=payload, exit_code=0 if ok else 1, recorded_run=recorded
    )


def _verdict(
    ok: bool, dry_run: bool, seconds: float | None, warnings: int = 0
) -> tuple["Block", str]:
    from cuanta.cli.document import Hint, Line, MascotBlock, Panel
    from cuanta.domain.messages import counted, english, msg
    from cuanta.domain.progress import Status, took
    from cuanta.domain.voice import Mood

    next_hint = "cuanta init" if dry_run else ("cuanta test" if ok else "cuanta doctor")
    verdict = msg("init.complete" if ok else "init.problems")
    total = verdict if seconds is None else took(seconds, verdict)
    lines: list[Block] = [
        MascotBlock(Mood.HAPPY if ok else Mood.ALARMED),
        Line(english(total), Status.OK if ok else Status.FAIL),
    ]
    if ok and warnings:
        lines.append(Line(english(counted("init.warnings", "count", warnings)), Status.WARN))
    lines.append(Hint(f"next: {next_hint}"))
    return Panel("purr" if ok else "hiss", tuple(lines)), next_hint


def _template(session: Session, dry_run: bool) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Line
    from cuanta.domain.forge_template import MANDATE_TEMPLATE, TemplateState
    from cuanta.domain.messages import english
    from cuanta.domain.progress import Status

    container = Container.for_project(session.project, verbose=session.options.verbose)
    try:
        outcome = container.mandate_templates().write(dry_run)
    finally:
        container.close()
    message = english(outcome.message)
    ready = outcome.written or outcome.state is TemplateState.READY
    status = Status.OK if ready else Status.INFO
    payload = {
        "dry_run": dry_run,
        "template": {
            "path": MANDATE_TEMPLATE,
            "state": outcome.state.value,
            "written": outcome.written,
            "message": message,
        },
    }
    return Document(blocks=(Line(message, status),), payload=payload)


def _telemetry_consent(session: Session, skipped: bool, wired: Callable[[], bool]) -> bool:
    if skipped:
        return False
    if session.options.yes:
        return True
    if not session.interactive:
        return False
    if wired():
        return True
    return typer.confirm(
        "Wire local telemetry (127.0.0.1 only) for runs cuanta launches?", default=True
    )
