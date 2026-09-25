import contextlib
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document


def listen_command(
    ctx: typer.Context,
    background: Annotated[
        bool, typer.Option("--background", help="Detach and keep running.")
    ] = False,
    status: Annotated[bool, typer.Option("--status", help="Show whether it runs.")] = False,
    stop: Annotated[bool, typer.Option("--stop", help="Stop the background listener.")] = False,
    port: Annotated[int | None, typer.Option("--port", help="Port (default from config).")] = None,
) -> None:
    execute(ctx, lambda session: _listen(session, background, status, stop, port))


def _status_document(running: bool, port: int, pid: int, received: int, written: int) -> "Document":
    from cuanta.cli.document import Document, KeyValues, Line
    from cuanta.domain.progress import Status

    line = Line(
        f"listening on 127.0.0.1:{port}" if running else "not running · nap",
        Status.OK if running else Status.INFO,
    )
    rows = (("pid", str(pid)), ("received", f"{received:,}"), ("written", f"{written:,}"))
    payload = {
        "running": running,
        "port": port,
        "pid": pid,
        "received": received,
        "written": written,
    }
    return Document(blocks=(line, KeyValues(rows)) if running else (line,), payload=payload)


def _listen(
    session: Session, background: bool, status: bool, stop: bool, port: int | None
) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Line
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.progress import Note, Status

    container = Container.for_project(session.project)
    control = container.listener()
    chosen = port or container.config.port
    if sum((background, status, stop)) > 1:
        raise DomainFailure("pick one of --background, --status, --stop")
    if status:
        current = control.status()
        return _status_document(
            current.running, current.port, current.pid, current.received, current.written
        )
    if stop:
        stopped = control.stop()
        line = Line(
            "listener stopped" if stopped else "no listener to stop · nap",
            Status.OK if stopped else Status.INFO,
        )
        return Document(blocks=(line,), payload={"stopped": stopped})
    if background:
        started = control.start_background(chosen)
        if started.port != container.config.port:
            container.persist_port(started.port)
        return _status_document(True, started.port, started.pid, started.received, started.written)
    running = control.status()
    if running.running:
        raise DomainFailure(
            f"a listener already runs on 127.0.0.1:{running.port}",
            "cuanta listen --stop to stop it",
        )
    free = control.free_port(chosen)
    if free != container.config.port:
        container.persist_port(free)

    def ready(_: object) -> None:
        session.presenter.publish(
            Note(Status.OK, f"listening on 127.0.0.1:{free} · ctrl+c to stop")
        )

    with contextlib.suppress(KeyboardInterrupt):
        control.serve(free, ready)
    return Document(
        blocks=(Line("listener stopped", Status.INFO),), payload={"stopped": True, "port": free}
    )
