from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.application.models import CatalogView
    from cuanta.cli.document import Block, Document
    from cuanta.domain.models import ModelEntry

models_app = typer.Typer(
    help="Model catalog: what each engine offers, and its tier.", no_args_is_help=True
)


@models_app.command("list", help="Every model per engine with its tier, prices and context.")
def list_command(
    ctx: typer.Context,
    engine: Annotated[str, typer.Option("--engine", help="Only this engine.")] = "",
    tier: Annotated[str, typer.Option("--tier", help="Only this tier.")] = "",
    refresh: Annotated[bool, typer.Option("--refresh", help="Ask the engines again.")] = False,
) -> None:
    execute(ctx, lambda session: _list(session, engine, tier, refresh))


@models_app.command("refresh", help="Ask the installed engines again and rewrite the cache.")
def refresh_command(ctx: typer.Context) -> None:
    execute(ctx, lambda session: _list(session, "", "", True))


@models_app.command("tier", help="Override the tier of one model for this project.")
def tier_command(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="Model id, alias or engine:model.")],
    tier: Annotated[str, typer.Argument(help="economy, standard, premium or frontier.")],
) -> None:
    execute(ctx, lambda session: _tier(session, model, tier))


@models_app.command("stats", help="Runs, success and cost per model, from the ledger.")
def stats_command(ctx: typer.Context) -> None:
    execute(ctx, _stats)


@models_app.command("probe", help="Send one tiny prompt to a model; shows the cost first.")
def probe_command(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="Model id, alias or engine:model.")],
    yes: Annotated[bool, typer.Option("--yes", help="Spend the tokens.")] = False,
) -> None:
    execute(ctx, lambda session: _probe(session, model, yes))


def _price(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _context(tokens: int) -> str:
    if not tokens:
        return "-"
    return f"{tokens // 1_000_000}M" if tokens >= 1_000_000 else f"{tokens // 1_000}k"


def _row(entry: "ModelEntry") -> tuple[str, ...]:
    marker = "*" if entry.default else ""
    return (
        entry.engine,
        f"{entry.id}{marker}",
        entry.resolved if entry.resolved != entry.id else "",
        entry.tier.value,
        entry.tier_source.value,
        _context(entry.context),
        _price(entry.input_price),
        _price(entry.output_price),
        ",".join(entry.efforts) or "-",
        entry.availability.value,
    )


def _entry_json(entry: "ModelEntry") -> dict[str, object]:
    from cuanta.domain.models import entry_to_json

    return entry_to_json(entry)


def _list(session: Session, engine: str, tier: str, refresh: bool) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Hint, Line, Table

    view: CatalogView = Container.for_project(session.project).model_service().view(refresh)
    shown = tuple(
        entry
        for entry in view.entries
        if (not engine or entry.engine == engine) and (not tier or entry.tier.value == tier)
    )
    blocks: list[Block] = [
        Line(
            f"engines: {', '.join(view.engines) or 'none found'} · tiers v{view.table_version}, "
            f"verified {view.verified_on} · catalog {view.refreshed_at}"
            f"{' (cached)' if view.cached else ''}"
        ),
        Table(
            "models",
            (
                Column("engine"),
                Column("model"),
                Column("resolves to"),
                Column("tier"),
                Column("from"),
                Column("context", numeric=True),
                Column("in $/M", numeric=True),
                Column("out $/M", numeric=True),
                Column("effort"),
                Column("status"),
            ),
            tuple(_row(entry) for entry in shown),
        ),
        Hint("* default model · change a tier with: cuanta models tier <model> <tier>"),
    ]
    payload: dict[str, object] = {
        "refreshed_at": view.refreshed_at,
        "cached": view.cached,
        "table_version": view.table_version,
        "verified_on": view.verified_on,
        "engines": list(view.engines),
        "models": [_entry_json(entry) for entry in shown],
    }
    return Document(blocks=tuple(blocks), payload=payload)


def _tier(session: Session, model: str, tier: str) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Line
    from cuanta.domain.progress import Status

    entry = Container.for_project(session.project).model_service().set_tier(model, tier)
    text = f"{entry.engine}:{entry.id} → {entry.tier.value} (saved in .cuanta/config.toml)"
    return Document(
        blocks=(Line(text, Status.OK),),
        payload={"model": entry.key, "tier": entry.tier.value},
    )


def _stats(session: Session) -> "Document":
    from cuanta.application.models import model_stats
    from cuanta.application.routing import role_stats
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Hint, Table
    from cuanta.cli.fmt import usd

    container = Container.for_project(session.project)
    entries = container.model_service().view().entries
    ledger = container.ledger()
    try:
        rows = model_stats(ledger.runs(), entries)
        roles = role_stats(ledger.routing_decisions())
    finally:
        ledger.close()
    table = Table(
        "per model",
        (
            Column("engine"),
            Column("model"),
            Column("tier"),
            Column("runs", numeric=True),
            Column("success", numeric=True),
            Column("avg cost", numeric=True),
        ),
        tuple(
            (
                row.engine,
                row.model,
                row.tier.value if row.tier else "-",
                str(row.runs),
                f"{row.success:.0%}",
                usd(row.average_cost),
            )
            for row in rows
        ),
    )
    blocks: list[Block] = [table]
    if not rows:
        blocks.append(Hint("no finished runs in the ledger yet"))
    blocks.append(
        Table(
            "per task type, role and tier",
            (
                Column("task"),
                Column("role"),
                Column("tier"),
                Column("runs", numeric=True),
                Column("green", numeric=True),
                Column("avg cost", numeric=True),
            ),
            tuple(
                (
                    row.task_type,
                    row.role,
                    row.tier,
                    str(row.samples),
                    f"{row.success:.0%}",
                    usd(row.average_cost),
                )
                for row in roles
            ),
        )
    )
    payload: dict[str, object] = {
        "models": [
            {
                "engine": row.engine,
                "model": row.model,
                "tier": row.tier.value if row.tier else None,
                "runs": row.runs,
                "ok": row.ok,
                "success": row.success,
                "average_cost_usd": row.average_cost,
            }
            for row in rows
        ],
        "roles": [
            {
                "task_type": row.task_type,
                "role": row.role,
                "tier": row.tier,
                "samples": row.samples,
                "green": row.green,
                "success": row.success,
                "average_cost_usd": row.average_cost,
            }
            for row in roles
        ],
    }
    return Document(blocks=tuple(blocks), payload=payload)


def _probe(session: Session, model: str, yes: bool) -> "Document":
    from cuanta.application.models import PROBE_BUDGET_USD, PROBE_INPUT_TOKENS
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Line
    from cuanta.domain.progress import Status

    outcome = Container.for_project(session.project).probe_model(model, yes or session.options.yes)
    entry = outcome.entry
    if outcome.ok is None:
        cost = (
            "unknown (no price)" if outcome.estimate is None else f"about ${outcome.estimate:.4f}"
        )
        text = (
            f"probe {entry.key}: one prompt of ~{PROBE_INPUT_TOKENS} tokens, {cost}, "
            f"capped at ${PROBE_BUDGET_USD:.2f} · re-run with --yes to spend it"
        )
        return Document(
            blocks=(Line(text, Status.INFO),),
            payload={"model": entry.key, "estimate_usd": outcome.estimate, "probed": False},
        )
    text = (
        f"probe {entry.key}: {'answered' if outcome.ok else 'failed'} · "
        f"{_price(outcome.cost_usd)} spent"
    )
    return Document(
        blocks=(Line(text, Status.OK if outcome.ok else Status.FAIL),),
        payload={"model": entry.key, "probed": outcome.ok, "cost_usd": outcome.cost_usd},
        exit_code=0 if outcome.ok else 1,
    )
