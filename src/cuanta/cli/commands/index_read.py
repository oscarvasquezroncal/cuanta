from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document


def find_command(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Terms, identifiers or paths to find.")],
    limit: Annotated[int, typer.Option("--limit", min=1, max=60)] = 10,
    task_type: Annotated[str, typer.Option("--task", help="Task history to consider.")] = "",
    rerank: Annotated[
        bool, typer.Option("--rerank", help="Opt in to a consented Jev metadata batch.")
    ] = False,
) -> None:
    execute(ctx, lambda session: _read(session, "find", query, limit, task_type, rerank))


def card_command(ctx: typer.Context, path: Annotated[str, typer.Argument()]) -> None:
    execute(ctx, lambda session: _read(session, "card", path))


def impact_command(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument()],
    depth: Annotated[int, typer.Option("--depth", min=1, max=5)] = 1,
) -> None:
    execute(ctx, lambda session: _read(session, "impact", path, depth))


def facts_command(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument()] = "",
    stale: Annotated[
        bool, typer.Option("--stale", help="Show facts awaiting revalidation.")
    ] = False,
) -> None:
    execute(ctx, lambda session: _read(session, "facts", path, stale=stale))


def _read(
    session: Session,
    kind: str,
    value: str,
    limit: int = 10,
    task_type: str = "",
    rerank: bool = False,
    stale: bool = False,
) -> Document:
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Table, Verbatim

    container = Container.for_project(session.project)
    reader = container.index_reader()
    try:
        reader.update()
        if kind == "card":
            card = reader.card(value)
            return Document(blocks=(Verbatim(card.text),), payload=asdict(card))
        if kind == "impact":
            impact = reader.impact(value, limit)
            return Document(
                blocks=(Table("Impact", (Column("Path"), Column("Reason")), impact),),
                payload={"path": value, "impact": impact},
            )
        if kind == "facts":
            facts = reader.facts(value, stale)
            fact_rows = tuple(
                (
                    f"{row.path}:{row.line}-{row.end_line or row.line}",
                    "revalidate" if row.stale else "fresh",
                    row.text,
                    row.provenance,
                )
                for row in facts
            )
            return Document(
                blocks=(
                    Table(
                        "Facts",
                        (Column("Anchor"), Column("State"), Column("Fact"), Column("Source")),
                        fact_rows,
                    ),
                ),
                payload={"facts": tuple(asdict(row) for row in facts)},
            )
        backend = container.index_reranker() if rerank else None
        hits = reader.find(value, limit, task_type, backend, container.config.instinct_share_paths)
        hit_rows = tuple((hit.path, f"{hit.score:.3f}", "; ".join(hit.reasons)) for hit in hits)
        return Document(
            blocks=(
                Table(
                    "Files",
                    (Column("Path"), Column("Score", numeric=True), Column("Reasons")),
                    hit_rows,
                ),
            ),
            payload={
                "hits": tuple(asdict(hit) for hit in hits),
                "rerank": reader.rerank_reason,
                "cost_usd": reader.rerank_receipt.cost_usd if reader.rerank_receipt else None,
            },
        )
    finally:
        reader.close()
        container.close()
