from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document


def index_command(
    ctx: typer.Context,
    rebuild: Annotated[bool, typer.Option("--rebuild", help="Rebuild the local index.")] = False,
    status: Annotated[bool, typer.Option("--status", help="Show the stored inventory.")] = False,
    summaries: Annotated[
        bool,
        typer.Option("--summaries", help="Preview economy summary estimate; --yes confirms spend."),
    ] = False,
) -> None:
    execute(ctx, lambda session: _index(session, rebuild, status, summaries))


def _index(session: Session, rebuild: bool, status: bool, summaries: bool) -> Document:
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, KeyValues

    container = Container.for_project(session.project)
    container.progress = session.presenter
    try:
        service = container.index_service(rebuild)
        try:
            report = service.status() if status and not rebuild else container.indexed(service)
            summary = container.index_summaries(service, session.options.yes) if summaries else None
        finally:
            service.close()
    finally:
        container.close()
    payload = asdict(report)
    if summary is not None:
        payload["summaries"] = asdict(summary)
    rows = tuple((key, str(value)) for key, value in asdict(report).items())
    if summary is not None:
        rows += tuple((f"summary.{key}", str(value)) for key, value in asdict(summary).items())
    return Document(
        blocks=(KeyValues(rows),),
        payload=payload,
    )
