from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Block, Document

instinct_app = typer.Typer(help="Configure System 1: typed fast decisions.", no_args_is_help=True)


@instinct_app.command("show", help="Current backend, availability, consent, recent decisions.")
def show_command(ctx: typer.Context) -> None:
    execute(ctx, _show)


@instinct_app.command("use", help="Switch backend: heuristic, jev or llm.")
def use_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="heuristic, jev or llm.")],
    global_scope: Annotated[
        bool, typer.Option("--global", help="Set the user-level default.")
    ] = False,
) -> None:
    execute(ctx, lambda session: _use(session, name, global_scope))


@instinct_app.command("probe", help="Ask three sample questions; print answers, latency, cost.")
def probe_command(ctx: typer.Context) -> None:
    execute(ctx, _probe)


def _show(session: Session) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, KeyValues, Table
    from cuanta.domain.messages import english

    container = Container.for_project(session.project)
    backend = container.instinct_backend()
    source = container.instinct_source()
    usable, said = backend.available()
    detail = english(said)
    consented = backend.name in container.config.remote_consent
    connection = container.instinct_connection()
    warning = container.instinct_setup_warning()
    ledger = container.ledger()
    try:
        recent = ledger.decisions(limit=5)
    finally:
        ledger.close()
    rows: tuple[tuple[str, str], ...] = (
        ("backend", backend.name),
        ("source", source),
        ("available", f"{'yes' if usable else 'no'} · {detail}"),
        ("remote", "yes" if backend.remote else "no (offline)"),
        (
            "consent",
            "given" if consented or not backend.remote else "missing: heuristic answers instead",
        ),
    )
    if connection is not None:
        rows = (*rows, ("connection", english(connection)))
    if warning is not None:
        rows = (*rows, ("warning", english(warning)))
    blocks: list[Block] = [KeyValues(rows)]
    if recent:
        blocks.append(
            Table(
                "recent decisions",
                (
                    Column("backend"),
                    Column("question"),
                    Column("answer"),
                    Column("ms", numeric=True),
                    Column("outcome"),
                    Column("fallback"),
                ),
                tuple(
                    (
                        item.backend,
                        item.question[:48],
                        item.answer,
                        str(item.latency_ms),
                        item.outcome or "-",
                        item.fallback_error or "-",
                    )
                    for item in recent
                ),
            )
        )
    payload = {
        "backend": backend.name,
        "source": source,
        "available": usable,
        "detail": detail,
        "remote": backend.remote,
        "consent": consented,
        "connection": english(connection) if connection is not None else None,
        "warning": english(warning) if warning is not None else None,
        "recent": [
            {
                "backend": item.backend,
                "question": item.question,
                "answer": item.answer,
                "latency_ms": item.latency_ms,
                "outcome": item.outcome,
                "fallback_error": item.fallback_error,
            }
            for item in recent
        ],
    }
    return Document(blocks=tuple(blocks), payload=payload)


def _use(session: Session, name: str, global_scope: bool = False) -> "Document":
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, Line
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.messages import english
    from cuanta.domain.progress import Status

    container = Container.for_project(session.project)
    backend = container.instinct_backend(name)
    consent = list(
        container.global_instinct_consent() if global_scope else container.config.remote_consent
    )
    if backend.remote and name not in consent:
        question = (
            f"{name} sends redacted decision context (no source code) to a remote service. "
            f"Allow for {'your user-level default' if global_scope else 'this project'}?"
        )
        allowed = session.options.yes or (
            session.interactive and typer.confirm(question, default=False)
        )
        if not allowed:
            raise DomainFailure(f"{name} needs consent", "re-run with --yes to consent")
        consent.append(name)
        if global_scope:
            container.set_global_value("instinct.consent", consent)
        else:
            container.set_project_value("instinct.consent", consent)
    if global_scope:
        container.set_global_value("instinct.backend", name)
    else:
        container.set_project_value("instinct.backend", name)
    usable, said = backend.available()
    detail = english(said)
    status = Status.OK if usable else Status.WARN
    note = "" if usable else " · unavailable now, heuristic answers until it is"
    scope = "global" if global_scope else "project"
    return Document(
        blocks=(Line(f"instinct → {name} ({scope}; {detail}){note}", status),),
        payload={
            "backend": name,
            "scope": scope,
            "available": usable,
            "detail": detail,
            "consent": consent,
        },
    )


def _probe(session: Session) -> "Document":
    from cuanta.application.instinct import probe_questions
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Column, Document, Table
    from cuanta.domain.instinct import Primitive

    container = Container.for_project(session.project)
    ledger = container.ledger()
    rows: list[tuple[str, str, str, str, str]] = []
    try:
        maker = container.decisions(ledger)
        for primitive, question, context, options in probe_questions():
            if primitive is Primitive.CHOOSE:
                choice, decided = maker.choose(question, options, context, run_id="probe")
                answer = f"{choice.option} (p={choice.probability:.2f})"
            elif primitive is Primitive.NOUL:
                noul, decided = maker.noul(question, context, run_id="probe")
                answer = f"p_yes={noul.p_yes:.2f}"
            else:
                score, decided = maker.score(question, 0, 10, context, run_id="probe")
                answer = f"{score.value:.1f} (confidence {score.confidence:.2f})"
            receipt = decided.receipt
            rows.append(
                (
                    primitive.value,
                    question[:52],
                    answer,
                    str(receipt.latency_ms),
                    f"${receipt.cost_usd:.4f} · {receipt.backend}",
                )
            )
    finally:
        ledger.close()
    table = Table(
        f"probe · {maker.backend.name}",
        (
            Column("primitive"),
            Column("question"),
            Column("answer"),
            Column("ms", numeric=True),
            Column("cost"),
        ),
        tuple(rows),
    )
    payload = {
        "backend": maker.backend.name,
        "answers": [
            {
                "primitive": row[0],
                "question": row[1],
                "answer": row[2],
                "latency_ms": int(row[3]),
                "cost": row[4],
            }
            for row in rows
        ],
    }
    return Document(blocks=(table,), payload=payload)
