from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from cuanta.cli.output import (
    Environment,
    GlobalOptions,
    OutputMode,
    OutputSettings,
    resolve_output,
)
from cuanta.cli.theme import ThemeName
from cuanta.domain.errors import CuantaError, ExitCode

if TYPE_CHECKING:
    from cuanta.cli.document import Document
    from cuanta.cli.presenters.base import Presenter

FORCE_TTY_ENV = "CUANTA_FORCE_TTY"


@dataclass(frozen=True, slots=True)
class Session:
    options: GlobalOptions
    settings: OutputSettings
    presenter: Presenter
    project: Path

    @property
    def interactive(self) -> bool:
        return self.settings.mode is OutputMode.PRETTY and not self.options.yes


def detect_environment() -> Environment:
    forced = os.environ.get(FORCE_TTY_ENV) == "1"
    stream = sys.stdout
    is_tty = forced or (hasattr(stream, "isatty") and stream.isatty())
    return Environment(
        is_tty=is_tty,
        no_color="NO_COLOR" in os.environ,
        encoding=getattr(stream, "encoding", None) or "",
        colorfgbg=os.environ.get("COLORFGBG"),
    )


def global_options(ctx: typer.Context) -> GlobalOptions:
    root = ctx.find_root()
    value = root.obj
    return value if isinstance(value, GlobalOptions) else GlobalOptions()


def _config_defaults(project: Path) -> tuple[ThemeName | None, bool]:
    from cuanta.bootstrap import load_config

    config = load_config(project)
    theme = ThemeName(config.theme) if config.theme in {"dark", "light", "auto"} else None
    return theme, config.emoji


def build_presenter(settings: OutputSettings) -> Presenter:
    if settings.mode is OutputMode.JSON:
        from cuanta.cli.presenters.json_presenter import JsonPresenter

        return JsonPresenter()
    if settings.mode is OutputMode.PLAIN:
        from cuanta.cli.presenters.plain import PlainPresenter

        return PlainPresenter()
    from cuanta.cli.presenters.pretty import PrettyPresenter, build_console

    forced = os.environ.get(FORCE_TTY_ENV) == "1"
    return PrettyPresenter(
        settings, build_console(settings, force_terminal=True if forced else None)
    )


def open_session(ctx: typer.Context) -> Session:
    options = global_options(ctx)
    project = (options.project or Path.cwd()).resolve()
    theme_default, emoji_default = _config_defaults(project)
    settings = resolve_output(options, detect_environment(), theme_default, emoji_default)
    return Session(options, settings, build_presenter(settings), project)


def execute(ctx: typer.Context, action: Callable[[Session], Document]) -> None:
    session = open_session(ctx)
    code = ExitCode.OK
    try:
        document = action(session)
        session.presenter.render(document)
        code = ExitCode(document.exit_code)
    except CuantaError as error:
        session.presenter.fail(error)
        code = error.exit_code
    except KeyboardInterrupt:
        session.presenter.fail(CuantaError("interrupted · nine lives: re-run to resume"))
        code = ExitCode.INTERRUPTED
    except Exception as error:
        if session.options.verbose:
            import traceback

            traceback.print_exc(file=sys.stderr)
        session.presenter.fail(
            CuantaError(f"unexpected {type(error).__name__}: {error}", "re-run with --verbose")
        )
        code = ExitCode.ENVIRONMENT
    finally:
        session.presenter.close()
    if code is not ExitCode.OK:
        raise typer.Exit(int(code))
