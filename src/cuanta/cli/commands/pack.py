from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document


def pack_command(
    ctx: typer.Context,
    what: Annotated[str, typer.Option("--for", help="Request to pack at zero spend.")],
    depth: Annotated[str, typer.Option("--depth")] = "normal",
    task_type: Annotated[str, typer.Option("--type")] = "feature",
    why: Annotated[str, typer.Option("--why")] = "",
    where: Annotated[str, typer.Option("--where")] = "",
    constraints: Annotated[str, typer.Option("--constraints")] = "",
    out_of_scope: Annotated[str, typer.Option("--out-of-scope")] = "",
) -> None:
    execute(
        ctx,
        lambda session: _pack(
            session, what, depth, task_type, why, where, constraints, out_of_scope
        ),
    )


def _pack(
    session: Session,
    what: str,
    depth: str,
    task_type: str,
    why: str,
    where: str,
    constraints: str,
    out_of_scope: str,
) -> Document:
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, KeyValues, Table, Verbatim
    from cuanta.domain.depth import Depth
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.mandate import MandateRequest, MandateType
    from cuanta.domain.messages import english, msg

    if task_type not in {item.value for item in MandateType}:
        raise ValueError("Unknown request type")
    if depth not in {item.value for item in Depth}:
        raise DomainFailure(
            english(msg("run_error.pack_depth")), english(msg("run_error.pack_depth_hint"))
        )
    container = Container.for_project(session.project)
    try:
        pack = container.context_pack(
            MandateRequest(task_type, what, why, where, constraints, "", out_of_scope), depth
        )
        return Document(
            blocks=(
                KeyValues(
                    (
                        ("Estimated tokens", str(pack.tokens)),
                        ("Budget", str(pack.budget)),
                        ("Cost", "$0.00"),
                    )
                ),
                Table(
                    "Pack decisions",
                    (Column("Path"), Column("Layer"), Column("Included"), Column("Reason")),
                    tuple(
                        (item.path or item.key, item.layer.value, str(item.included), item.reason)
                        for item in pack.decisions
                    ),
                ),
                Verbatim(pack.text),
            ),
            payload={**asdict(pack), "text": pack.text, "cost_usd": 0.0},
        )
    finally:
        container.close()
