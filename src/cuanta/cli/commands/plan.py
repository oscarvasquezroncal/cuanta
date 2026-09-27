from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document


def plan_command(
    ctx: typer.Context,
    what: Annotated[str, typer.Option("--for", help="Request to compile at zero spend.")],
    task_type: Annotated[str, typer.Option("--type")] = "feature",
    why: Annotated[str, typer.Option("--why")] = "",
    where: Annotated[str, typer.Option("--where")] = "",
    constraints: Annotated[str, typer.Option("--constraints")] = "",
    out_of_scope: Annotated[str, typer.Option("--out-of-scope")] = "",
) -> None:
    execute(
        ctx, lambda session: _plan(session, what, task_type, why, where, constraints, out_of_scope)
    )


def _plan(
    session: Session,
    what: str,
    task_type: str,
    why: str,
    where: str,
    constraints: str,
    out_of_scope: str,
) -> Document:
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Heading, KeyValues, Table
    from cuanta.domain.change_plan import EXECUTION, deny_rules, strict_tools
    from cuanta.domain.mandate import MandateRequest, MandateType

    if task_type not in {item.value for item in MandateType}:
        raise ValueError("Unknown request type")
    container = Container.for_project(session.project)
    try:
        plan = container.change_plan(
            MandateRequest(task_type, what, why, where, constraints, "", out_of_scope)
        )
        denied = tuple(
            dict.fromkeys((*deny_rules(plan), *(EXECUTION if plan.guard or plan.read_only else ())))
        )
        rows = (
            *((item.path, "edit", f"{item.confidence:.0%}", item.reason) for item in plan.edit),
            *((path, "read", "", "context") for path in plan.read),
            *((path, "protected", "", "exclusion") for path in plan.guard),
        )
        return Document(
            blocks=(
                Table(
                    "Change plan",
                    (Column("Path"), Column("Role"), Column("Confidence"), Column("Reason")),
                    rows,
                ),
                Heading("Guard rules"),
                KeyValues(tuple((str(number), rule) for number, rule in enumerate(denied, 1))),
                Heading("Verify"),
                KeyValues(
                    tuple((str(number), command) for number, command in enumerate(plan.verify, 1))
                ),
            ),
            payload={
                **asdict(plan),
                "guard_rules": denied,
                "tools": strict_tools(plan) if plan.guard or plan.read_only else (),
                "cost_usd": 0.0,
            },
        )
    finally:
        container.close()
