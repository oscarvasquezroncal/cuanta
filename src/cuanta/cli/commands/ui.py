import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import typer

from cuanta.cli.output import GlobalOptions
from cuanta.cli.runtime import FORCE_TTY_ENV, global_options
from cuanta.cli.theme import ThemeName

UI_THEMES = ("calico-dark", "calico-light", "auto", "ansi")
GLOBAL_TO_UI = {ThemeName.DARK: "calico-dark", ThemeName.LIGHT: "calico-light"}
CONFIG_TO_UI = {"dark": "calico-dark", "light": "calico-light"}


WEB_DRIVER = "web_driver"


def interactive_terminal(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    if env.get(FORCE_TTY_ENV) == "1" or WEB_DRIVER in env.get("TEXTUAL_DRIVER", ""):
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def ui_theme(options: GlobalOptions, requested: str, configured: str) -> str:
    if requested in UI_THEMES:
        return requested
    if options.theme in GLOBAL_TO_UI:
        return GLOBAL_TO_UI[options.theme]
    if configured in UI_THEMES:
        return configured
    return CONFIG_TO_UI.get(configured, "auto")


def ui_command(
    ctx: typer.Context,
    web: Annotated[bool, typer.Option("--web", help="Serve the app in a browser.")] = False,
    lang: Annotated[str, typer.Option("--lang", help="en or es (default: config, then OS).")] = "",
    theme: Annotated[
        str, typer.Option("--ui-theme", help="calico-dark, calico-light, auto or ansi.")
    ] = "",
    no_animation: Annotated[bool, typer.Option("--no-animation", help="Keep Michi still.")] = False,
    host: Annotated[str, typer.Option("--host", help="Web host.")] = "localhost",
    port: Annotated[int, typer.Option("--port", help="Web port.")] = 8000,
) -> None:
    launch(global_options(ctx), web, lang, theme, not no_animation, host, port)


def launch(
    options: GlobalOptions,
    web: bool = False,
    lang: str = "",
    theme: str = "",
    animate: bool = True,
    host: str = "localhost",
    port: int = 8000,
    open_run: str = "",
) -> None:
    from cuanta.bootstrap import load_config
    from cuanta.domain.errors import CuantaError, EnvironmentFailure

    try:
        if options.plain or options.json:
            raise EnvironmentFailure(
                "the app needs an interactive terminal", "drop --plain and --json to open it"
            )
        project = (options.project or Path.cwd()).resolve()
        config = load_config(project)
        from cuanta.tui.i18n import os_locale, resolve_language

        language = resolve_language(lang, config.language, os_locale())
        chosen = ui_theme(options, theme, config.theme)
        if web:
            from cuanta.tui.web import serve

            serve(project, language, chosen, host, port)
            return
        if not interactive_terminal():
            raise EnvironmentFailure(
                "the app needs an interactive terminal",
                "run cuanta ui from a terminal, or use cuanta --help for scriptable commands",
            )
        from cuanta.tui.app import run

        run(
            project,
            language,
            chosen,
            animate and "CUANTA_NO_ANIMATION" not in os.environ,
            open_run,
        )
    except CuantaError as error:
        typer.echo(f"cuanta: {error}", err=True)
        if error.hint:
            typer.echo(f"  {error.hint}", err=True)
        raise typer.Exit(int(error.exit_code)) from error
