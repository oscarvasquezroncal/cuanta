from typing import TYPE_CHECKING, Annotated

import typer

from cuanta.cli.runtime import Session, execute

if TYPE_CHECKING:
    from cuanta.cli.document import Document

GUIDE_ITEMS = ("app", "file", "oneline", "result", "costs", "limits")
GUIDE_NOTES = ("limits_more", "more")


def help_command(
    ctx: typer.Context,
    lang: Annotated[str, typer.Option("--lang", help="en or es (default: config, then OS).")] = "",
) -> None:
    execute(ctx, lambda session: guide(session, lang))


def guide(session: Session, lang: str) -> "Document":
    from cuanta.bootstrap import load_config
    from cuanta.cli.document import Commands, Document, Hint
    from cuanta.domain.messages import msg
    from cuanta.tui.i18n import Catalog, os_locale, resolve_language

    language = resolve_language(
        lang or session.options.lang, load_config(session.project).language, os_locale()
    )
    say = Catalog(language).message
    title = say(msg("guide.title"))
    items = tuple(
        (say(msg(f"guide.{key}")), say(msg(f"guide.{key}_command"))) for key in GUIDE_ITEMS
    )
    notes = tuple(say(msg(f"guide.{key}")) for key in GUIDE_NOTES)
    return Document(
        blocks=(Commands(title, items), *(Hint(line) for line in notes)),
        payload={
            "language": language,
            "title": title,
            "items": [
                {"key": key, "label": label, "command": command}
                for key, (label, command) in zip(GUIDE_ITEMS, items, strict=True)
            ],
            "notes": list(notes),
        },
    )
