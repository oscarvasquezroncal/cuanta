from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import TYPE_CHECKING

import typer

from cuanta.cli.commands.mandate import SHORTCUT_TYPES, mandate_command
from cuanta.cli.runtime import execute

if TYPE_CHECKING:
    from cuanta.cli.commands.mandate import MandateArgs
    from cuanta.cli.document import Document
    from cuanta.cli.runtime import Session
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.messages import Message

SHORTCUT_CONTEXT = {"allow_extra_args": True}


class Shortcut(StrEnum):
    RUN = "run"
    FEAT = "feat"
    FIX = "fix"
    AUDIT = "audit"


def _refused(key: str, hint: str, **values: object) -> "DomainFailure":
    from cuanta.domain.errors import DomainFailure
    from cuanta.domain.messages import english, msg

    return DomainFailure(english(msg(key, **values)), english(msg(hint, **values)))


def _with_file(args: "MandateArgs", extra: tuple[str, ...]) -> "MandateArgs":
    from dataclasses import replace
    from pathlib import Path

    if len(extra) > 1:
        raise _refused("shortcut.one_file", "shortcut.one_file_hint", count=len(extra))
    if extra and args.from_file is not None:
        raise _refused("shortcut.file_twice", "shortcut.twice_hint")
    if not extra and args.from_file is None:
        raise _refused("shortcut.needs_file", "shortcut.needs_file_hint")
    return replace(args, from_file=Path(extra[0])) if extra else args


def shortcut_args(shortcut: Shortcut, args: "MandateArgs", extra: tuple[str, ...]) -> "MandateArgs":
    from dataclasses import replace

    if shortcut is Shortcut.RUN:
        return _with_file(args, extra)
    text = " ".join(extra).strip()
    if text and args.what.strip():
        raise _refused("shortcut.text_twice", "shortcut.twice_hint")
    what = text or args.what
    if not what.strip() and args.from_file is None and not args.from_failure:
        raise _refused("shortcut.needs_text", "shortcut.needs_text_hint", command=shortcut.value)
    if args.from_file is not None:
        return replace(args, shortcut=shortcut.value, what=what)
    return replace(args, type=args.type or SHORTCUT_TYPES[shortcut.value], what=what)


def text_file_note(
    shortcut: Shortcut, args: "MandateArgs", extra: tuple[str, ...]
) -> "Message | None":
    from pathlib import Path

    from cuanta.domain.mandate_file import lone_path
    from cuanta.domain.messages import msg

    if shortcut is Shortcut.RUN or args.from_file is not None:
        return None
    found = lone_path(" ".join(extra))
    if found is None:
        return None
    try:
        named = Path(found.path).expanduser().is_file()
    except (OSError, RuntimeError):
        return None
    if not named:
        return None
    shown = f'"{found.path}"' if any(char.isspace() for char in found.path) else found.path
    return msg("shortcut.text_is_file", path=shown, command=shortcut.value)


def _command(shortcut: Shortcut) -> Callable[..., None]:
    @wraps(mandate_command)
    def command(ctx: typer.Context, **values: object) -> None:
        from cuanta.cli.commands.queue import mandate_args

        args = mandate_args(values)
        extra = tuple(ctx.args)
        execute(ctx, lambda session: _launch(session, shortcut, args, extra))

    return command


def _launch(
    session: "Session", shortcut: Shortcut, args: "MandateArgs", extra: tuple[str, ...]
) -> "Document":
    from cuanta.cli.commands.mandate import run_mandate

    shaped = shortcut_args(shortcut, args, extra)
    note = text_file_note(shortcut, args, extra)
    return run_mandate(session, shaped, () if note is None else (note,))


run_command = _command(Shortcut.RUN)
feat_command = _command(Shortcut.FEAT)
fix_command = _command(Shortcut.FIX)
audit_command = _command(Shortcut.AUDIT)
