from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute, global_options

if TYPE_CHECKING:
    from cuanta.application.results import ResultView
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Block, Document

runs_app = typer.Typer(
    help="Runs cuanta launched, with their stored reports.", no_args_is_help=True
)

LIST_LIMIT = 30


@runs_app.command("list", help="Recent runs, newest first.")
def list_command(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=1, help="How many runs.")] = LIST_LIMIT,
) -> None:
    execute(ctx, lambda session: _list(session, limit))


@runs_app.command("show", help="A run's report and consumption.")
def show_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print the human run report as Markdown.")
    ] = False,
) -> None:
    execute(ctx, lambda session: _show(session, run_id, markdown))


@runs_app.command("open", help="Open the app on a run's Result screen.")
def open_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id or a unique prefix.")],
) -> None:
    from cuanta.cli.commands.ui import launch
    from cuanta.domain.errors import CuantaError

    options = global_options(ctx)
    try:
        resolved = _resolve(options.project, run_id)
    except CuantaError as error:
        typer.echo(f"cuanta: {error}", err=True)
        if error.hint:
            typer.echo(f"  {error.hint}", err=True)
        raise typer.Exit(int(error.exit_code)) from error
    launch(options, open_run=resolved)


def _resolve(project: object, run_id: str) -> str:
    from pathlib import Path

    from cuanta.bootstrap import Container

    root = project if isinstance(project, Path) else Path.cwd()
    container = Container.for_project(root.resolve())
    try:
        return container.resolve_run(run_id)
    finally:
        container.close()


def _list(session: Session, limit: int) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Hint, Table
    from cuanta.cli.fmt import usd

    container = Container.for_project(session.project)
    runs = container.runs_query().run(limit)
    stored = container.stored_reports()
    table = Table(
        "runs",
        (
            Column("id"),
            Column("kind"),
            Column("engine"),
            Column("status"),
            Column("cost", numeric=True),
            Column("started"),
            Column("report"),
        ),
        tuple(
            (
                run.id,
                run.kind,
                run.engine or "-",
                run.status,
                usd(run.cost_usd),
                run.started_at[:16].replace("T", " ") or "-",
                "yes" if run.id in stored else "-",
            )
            for run in runs
        ),
    )
    blocks: list[Block] = [table]
    if not runs:
        blocks.append(Hint("no runs yet · run cuanta mandate or cuanta test"))
    else:
        blocks.append(Hint("cuanta runs show <id> · cuanta runs open <id>"))
    payload: dict[str, object] = {
        "runs": [
            {
                "id": run.id,
                "kind": run.kind,
                "engine": run.engine,
                "status": run.status,
                "cost_usd": run.cost_usd,
                "started_at": run.started_at,
                "report": run.id in stored,
            }
            for run in runs
        ]
    }
    return Document(blocks=tuple(blocks), payload=payload)


def _load(container: "Container", run_id: str) -> "ResultView":
    from cuanta.domain.errors import DomainFailure

    resolved = container.resolve_run(run_id)
    view = container.result_query(container.shared_ledger()).load(resolved)
    if view is None:
        raise DomainFailure(f"run {run_id} is not in the ledger", "list them: cuanta runs list")
    return view


def _show(session: Session, run_id: str, markdown: bool) -> "Document":
    from cuanta.application.results import run_markdown
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Hint, KeyValues, Line, MarkdownText, Verbatim
    from cuanta.cli.fmt import usd
    from cuanta.domain.messages import english
    from cuanta.domain.overhead import overhead_messages, overhead_payload

    container = Container.for_project(session.project)
    try:
        view = _load(container, run_id)
    finally:
        container.close()
    run = view.run
    payload: dict[str, object] = {
        "run_id": run.id,
        "kind": run.kind,
        "type": view.task_type,
        "simple": view.simple,
        "status": run.status,
        "engine": run.engine,
        "model": run.model,
        "cost_usd": run.cost_usd,
        "duration_s": view.duration_s,
        "changed_files": list(view.changed_files),
        "report": view.text,
        "report_path": view.report_path or None,
        "sections": [section.key for section in view.sections],
        "overhead": overhead_payload(view.overhead) if view.overhead is not None else None,
    }
    if markdown:
        text = run_markdown(view)
        return Document(
            blocks=(Verbatim(text.rstrip("\n")),), payload={**payload, "markdown": text}
        )
    duration = "n/a" if view.duration_s is None else f"{view.duration_s:,.0f} s"
    rows = (
        ("run", run.id),
        ("type", view.task_type or run.kind),
        ("mode", "simple (one agent, no project knowledge)" if view.simple else "Forge pipeline"),
        ("status", run.status),
        ("engine", f"{run.engine} · {run.model or 'default model'}"),
        ("duration", duration),
        ("cost", usd(run.cost_usd)),
        ("files changed", str(len(view.changed_files))),
    )
    blocks: list[Block] = [KeyValues(rows)]
    if view.overhead is not None:
        blocks.extend(Line(english(line)) for line in overhead_messages(view.overhead))
    if view.text.strip():
        blocks.append(MarkdownText(view.text))
    else:
        blocks.append(Hint("no stored report for this run"))
    if view.report_path:
        blocks.append(Hint(f"report: {view.report_path} · cuanta runs open {run.id}"))
    return Document(blocks=tuple(blocks), payload=payload)
