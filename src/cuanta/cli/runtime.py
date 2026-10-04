from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from cuanta.cli.theme import ThemeName

if TYPE_CHECKING:
    from cuanta.cli.document import Document
    from cuanta.cli.output import Environment, GlobalOptions, OutputSettings
    from cuanta.cli.presenters.base import Presenter
    from cuanta.domain.errors import InterruptedFailure
    from cuanta.tui.i18n import Catalog

FORCE_TTY_ENV = "CUANTA_FORCE_TTY"
HINT_SEPARATOR = " · "


class Interrupted(KeyboardInterrupt):
    def __init__(self, run_id: str = "", resumable: bool = False) -> None:
        super().__init__(run_id)
        self.run_id = run_id
        self.resumable = resumable


def interrupted(
    interrupt: KeyboardInterrupt, run_id: str = "", resumable: bool = False
) -> Interrupted:
    if isinstance(interrupt, Interrupted):
        return Interrupted(interrupt.run_id or run_id, interrupt.resumable or resumable)
    return Interrupted(run_id, resumable)


def interrupted_failure(interrupt: KeyboardInterrupt) -> InterruptedFailure:
    from cuanta.domain.errors import InterruptedFailure
    from cuanta.domain.messages import english, msg

    stopped = interrupted(interrupt)
    hints: list[str] = []
    if stopped.run_id:
        hints.append(english(msg("interrupt.see", run=stopped.run_id)))
    if stopped.resumable:
        hints.append(english(msg("interrupt.resume")))
    told = msg("interrupt.run", run=stopped.run_id) if stopped.run_id else msg("interrupt.stopped")
    return InterruptedFailure(english(told), HINT_SEPARATOR.join(hints))


@dataclass(frozen=True, slots=True)
class Session:
    options: GlobalOptions
    settings: OutputSettings
    presenter: Presenter
    project: Path

    @property
    def catalog(self) -> Catalog:
        from cuanta.tui.i18n import Catalog

        return Catalog(self.settings.language)

    @property
    def interactive(self) -> bool:
        from cuanta.cli.output import OutputMode

        return self.settings.mode is OutputMode.PRETTY and not self.options.yes


def detect_environment() -> Environment:
    from cuanta.cli.output import Environment

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
    from cuanta.cli.output import GlobalOptions

    root = ctx.find_root()
    value = root.obj
    return value if isinstance(value, GlobalOptions) else GlobalOptions()


def _config_defaults(project: Path) -> tuple[ThemeName | None, bool, str]:
    from cuanta.bootstrap import load_config

    config = load_config(project)
    theme = ThemeName(config.theme) if config.theme in {"dark", "light", "auto"} else None
    return theme, config.emoji, config.language


def build_presenter(settings: OutputSettings) -> Presenter:
    from cuanta.cli.output import OutputMode

    if settings.mode is OutputMode.JSON:
        from cuanta.cli.presenters.json_presenter import JsonPresenter

        return JsonPresenter()
    from cuanta.tui.i18n import Catalog

    if settings.mode is OutputMode.PLAIN:
        from cuanta.cli.presenters.plain import PlainPresenter

        return PlainPresenter(catalog=Catalog(settings.language))
    from cuanta.cli.presenters.pretty import PrettyPresenter, build_console

    forced = os.environ.get(FORCE_TTY_ENV) == "1"
    return PrettyPresenter(
        settings, build_console(settings, force_terminal=True if forced else None)
    )


def open_session(ctx: typer.Context) -> Session:
    from cuanta.cli.output import resolve_output

    options = global_options(ctx)
    project = (options.project or Path.cwd()).resolve()
    from cuanta.tui.i18n import os_locale, resolve_language

    theme_default, emoji_default, configured = _config_defaults(project)
    language = resolve_language(options.lang, configured, os_locale())
    settings = resolve_output(options, detect_environment(), theme_default, emoji_default, language)
    return Session(options, settings, build_presenter(settings), project)


def execute(ctx: typer.Context, action: Callable[[Session], Document]) -> None:
    from cuanta.domain.errors import CuantaError, ExitCode

    session = open_session(ctx)
    code = ExitCode.OK
    document: Document | None = None
    try:
        document = action(session)
        session.presenter.render(document)
        if document.after_render is not None:
            sys.stdout.flush()
            document.after_render()
        code = ExitCode(document.exit_code)
    except CuantaError as error:
        from cuanta.bootstrap import record_failure

        error.log_path = record_failure(session.project, error)
        session.presenter.fail(error)
        code = error.exit_code
    except (KeyboardInterrupt, typer.Abort) as stop:
        interrupt = stop if isinstance(stop, KeyboardInterrupt) else KeyboardInterrupt()
        recorded = document.recorded_run if document is not None else ""
        failure = interrupted_failure(interrupted(interrupt, recorded))
        from cuanta.bootstrap import record_failure

        failure.log_path = record_failure(session.project, interrupt)
        session.presenter.fail(failure)
        code = failure.exit_code
    except Exception as error:
        if session.options.verbose:
            import traceback

            traceback.print_exc(file=sys.stderr)
        from cuanta.bootstrap import record_failure

        unexpected_failure = CuantaError(session.catalog("failure.unexpected"))
        unexpected_failure.log_path = record_failure(session.project, error)
        session.presenter.fail(unexpected_failure)
        code = ExitCode.ENVIRONMENT
    finally:
        session.presenter.close()
    if code is not ExitCode.OK:
        raise typer.Exit(int(code))
