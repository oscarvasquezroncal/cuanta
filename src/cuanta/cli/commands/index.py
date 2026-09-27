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
) -> None:
    execute(ctx, lambda session: _index(session, rebuild, status))


def _index(session: Session, rebuild: bool, status: bool) -> Document:
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, KeyValues

    container = Container.for_project(session.project)
    service = container.index_service(rebuild)
    try:
        report = service.status() if status and not rebuild else service.update()
    finally:
        service.close()
        container.close()
    return Document(
        blocks=(KeyValues(tuple((key, str(value)) for key, value in asdict(report).items())),),
        payload=asdict(report),
    )
