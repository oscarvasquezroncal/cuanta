from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document

telemetry_app = typer.Typer(help="Wire, unwire or inspect engine telemetry.", no_args_is_help=True)

ENGINE_HELP = "claude, codex or all."


@telemetry_app.command("on", help="Wire engines to the local listener (backs up configs first).")
def on_command(
    ctx: typer.Context,
    engine: Annotated[str, typer.Option("--engine", help=ENGINE_HELP)] = "all",
) -> None:
    execute(ctx, lambda session: _toggle(session, engine, True))


@telemetry_app.command("off", help="Restore the configs cuanta backed up.")
def off_command(
    ctx: typer.Context,
    engine: Annotated[str, typer.Option("--engine", help=ENGINE_HELP)] = "all",
) -> None:
    execute(ctx, lambda session: _toggle(session, engine, False))


@telemetry_app.command("status", help="Listener and per-engine wiring.")
def status_command(ctx: typer.Context) -> None:
    execute(ctx, _status)


@telemetry_app.command("env", help="Print a shell snippet with the Claude Code telemetry env.")
def env_command(
    ctx: typer.Context,
    shell: Annotated[
        str, typer.Option("--shell", help="cmd, pwsh, bash, zsh or fish (default: detected).")
    ] = "",
) -> None:
    execute(ctx, lambda session: _env(session, shell))


def _state_status(state: str) -> object:
    from cuanta.domain.progress import Status

    return {
        "on": Status.OK,
        "off": Status.INFO,
        "other": Status.WARN,
        "unavailable": Status.SKIP,
    }.get(state, Status.INFO)


def _reports_document(reports: tuple[object, ...], title: str) -> "Document":
    from cuanta.cli.document import Document, Line
    from cuanta.domain.progress import Status
    from cuanta.domain.telemetry import WiringReport

    blocks = []
    payload = []
    for report in reports:
        if not isinstance(report, WiringReport):
            continue
        status = _state_status(report.state.value)
        blocks.append(
            Line(
                f"{report.engine}: {report.state.value} · {report.detail}",
                status if isinstance(status, Status) else Status.INFO,
            )
        )
        payload.append(
            {
                "engine": report.engine,
                "state": report.state.value,
                "detail": report.detail,
                "path": report.path,
            }
        )
    return Document(blocks=tuple(blocks), payload={title: payload})


def _toggle(session: Session, engine: str, enable: bool) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.domain.errors import DomainFailure

    if engine not in {"all", "claude", "codex"}:
        raise DomainFailure(f"unknown engine {engine}", "use claude, codex or all")
    service = Container.for_project(session.project).telemetry()
    reports = service.enable(engine) if enable else service.disable(engine)
    return _reports_document(reports, "engines")


def _status(session: Session) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Line
    from cuanta.domain.progress import Status

    report = Container.for_project(session.project).telemetry().status()
    listener = report.listener
    base = _reports_document(report.engines, "engines")
    head = Line(
        f"listener: 127.0.0.1:{listener.port} · {listener.written:,} events written"
        if listener.running
        else f"listener: not running (port {report.port}) · cuanta listen --background",
        Status.OK if listener.running else Status.WARN,
    )
    payload = {
        "listener": {"running": listener.running, "port": report.port, "written": listener.written},
        **base.payload,
    }
    return Document(blocks=(head, *base.blocks), payload=payload)


def _env(session: Session, shell: str) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Verbatim
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.shells import Shell, shell_from_name

    container = Container.for_project(session.project)
    chosen = shell_from_name(shell) if shell else container.shell()
    service = container.telemetry()
    if shell and chosen is Shell.UNKNOWN:
        raise DomainFailure(f"unsupported shell: {shell}", "use cmd, pwsh, bash, zsh or fish")
    if chosen is Shell.UNKNOWN:
        blocks = []
        snippets: dict[str, str] = {}
        for candidate in (Shell.CMD, Shell.POWERSHELL, Shell.BASH):
            text = service.env_snippet(candidate)
            snippets[candidate.value] = text
            blocks.append(Verbatim(f"{candidate.value}:\n{text}"))
        return Document(blocks=tuple(blocks), payload={"shell": "unknown", "snippets": snippets})
    text = service.env_snippet(chosen)
    return Document(blocks=(Verbatim(text),), payload={"shell": chosen.value, "snippet": text})
