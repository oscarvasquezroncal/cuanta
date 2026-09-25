from typing import TYPE_CHECKING

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document


def meow_command(ctx: typer.Context) -> None:
    execute(ctx, _meow)


def _meow(session: Session) -> "Document":
    from cuanta.application.meow import meow
    from cuanta.cli.document import Document, KeyValues, MascotBlock, Swatches, Wordmark
    from cuanta.cli.theme import PALETTE, semantic_colors
    from cuanta.domain.voice import Mood

    report = meow()
    colors = semantic_colors(session.settings.theme)
    payload = {
        "name": report.name,
        "version": report.version,
        "tagline": report.tagline,
        "mascot": report.mascot,
        "theme": session.settings.theme.value,
        "palette": {
            swatch.semantic: {"token": swatch.token, "color": colors[swatch.semantic]}
            for swatch in PALETTE
        },
    }
    blocks = (
        Wordmark(),
        MascotBlock(Mood.HAPPY, f"{report.mascot} says meow"),
        KeyValues((("version", report.version), ("theme", session.settings.theme.value))),
        Swatches(),
    )
    return Document(blocks=blocks, payload=payload)
