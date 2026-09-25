from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document

ledger_app = typer.Typer(help="Inspect and export the local ledger.", no_args_is_help=True)


@ledger_app.command("export", help="Export ledger tables as JSON or CSV.")
def export_command(
    ctx: typer.Context,
    fmt: Annotated[str, typer.Option("--format", help="json or csv.")] = "json",
    table: Annotated[
        str, typer.Option("--table", help="all, runs, events, test_runs, decisions, baselines.")
    ] = "all",
    run: Annotated[str, typer.Option("--run", help="Only rows of this run id.")] = "",
    out: Annotated[
        Path | None, typer.Option("--out", help="Write to a file instead of stdout.")
    ] = None,
    every: Annotated[
        bool, typer.Option("--all", help="Every table; with csv, a ZIP with one CSV per table.")
    ] = False,
    include_raw: Annotated[
        bool, typer.Option("--include-raw", help="Keep the raw payload of each event.")
    ] = False,
) -> None:
    chosen = "all" if every else table
    execute(ctx, lambda session: _export(session, fmt, chosen, run, out, include_raw))


def _export(
    session: Session, fmt: str, table: str, run: str, out: Path | None, include_raw: bool
) -> "Document":
    from cuanta.application.export import export
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Line, Verbatim
    from cuanta.domain.progress import Status

    container = Container.for_project(session.project)
    if out is None and fmt == "csv" and table == "all":
        out = session.project / ".cuanta" / "exports" / "ledger.zip"
    if out is not None:
        target = out if out.is_absolute() else Path.cwd() / out
        final, rows = container.export_ledger(fmt, table, target, include_raw, run)
        size = final.stat().st_size
        return Document(
            blocks=(Line(f"wrote {final} ({rows:,} rows, {size:,} bytes)", Status.OK),),
            payload={
                "path": str(final),
                "bytes": size,
                "rows": rows,
                "format": fmt,
                "table": table,
            },
        )
    ledger = container.ledger()
    try:
        text = export(ledger, fmt, table, run, include_raw)
    finally:
        ledger.close()
    if session.options.json and fmt == "json":
        import json

        return Document(blocks=(), payload=json.loads(text))
    return Document(
        blocks=(Verbatim(text.rstrip("\n")),),
        payload={"format": fmt, "table": table, "content": text},
    )
