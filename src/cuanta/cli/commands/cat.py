from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document


def cat_command(
    ctx: typer.Context,
    capsule: Annotated[str, typer.Argument(help="Capsule id, e.g. cap:1a2b3c.")],
    lines: Annotated[str | None, typer.Option("--lines", help="Line range a:b (1-based).")] = None,
    level: Annotated[
        str, typer.Option("--level", help="L0 meta, L1 summary, L2 window, L3 full.")
    ] = "L2",
) -> None:
    execute(ctx, lambda session: _cat(session, capsule, lines, level))


def _cat(session: Session, reference: str, lines: str | None, level_name: str) -> "Document":
    from cuanta.application.cat_capsule import CatCapsule
    from cuanta.bootstrap import Container
    from cuanta.cli.document import Document, KeyValues, Verbatim
    from cuanta.cli.output import OutputMode
    from cuanta.domain.capsules import Level, parse_range
    from cuanta.domain.errors import DomainFailure

    try:
        level = Level(level_name.upper())
    except ValueError as error:
        raise DomainFailure(f"unknown level {level_name}", "use L0, L1, L2 or L3") from error
    try:
        selection = parse_range(lines) if lines else None
    except ValueError as error:
        raise DomainFailure(f"bad --lines {lines}: {error}", "example: --lines 10:60") from error
    container = Container.for_project(session.project)
    ledger = container.ledger()
    try:
        view = CatCapsule(ledger, container.capsule_store()).run(reference, level, selection)
    finally:
        ledger.close()
    capsule = view.capsule
    meta = KeyValues(
        (
            ("capsule", capsule.id),
            ("kind", capsule.kind),
            ("size", f"{capsule.size_bytes:,} bytes · {capsule.lines:,} lines"),
        )
    )
    width = len(str(max((number for number, _ in view.lines), default=1)))
    body = "\n".join(f"{number:>{width}}  {text}" for number, text in view.lines)
    payload = {
        "id": capsule.id,
        "kind": capsule.kind,
        "size_bytes": capsule.size_bytes,
        "lines_total": view.total_lines,
        "level": view.level.value,
        "lines": [{"n": number, "text": text} for number, text in view.lines],
    }
    full_page = view.level is Level.L3 and lines is None
    if full_page and session.settings.mode is OutputMode.PRETTY:
        from cuanta.cli.presenters.pretty import PrettyPresenter

        presenter = session.presenter
        if isinstance(presenter, PrettyPresenter) and presenter.console.is_terminal:
            with presenter.console.pager(styles=False):
                presenter.console.print(body, markup=False, highlight=False)
            return Document(blocks=(meta,), payload=payload)
    if view.level is Level.L0 and lines is None:
        return Document(blocks=(meta,), payload=payload)
    return Document(blocks=(meta, Verbatim(body)), payload=payload)
