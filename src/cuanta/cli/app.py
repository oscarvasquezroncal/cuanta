from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from cuanta.cli.commands import (
    bench,
    cat,
    doctor,
    init,
    instinct,
    ledger,
    listen,
    loop,
    mandate,
    meow,
    models,
    probe,
    refresh,
    route,
    runs,
    spectrum,
    telemetry,
    test,
    ui,
)
from cuanta.cli.group import hoist_globals
from cuanta.cli.output import GlobalOptions
from cuanta.cli.theme import ThemeName

app = typer.Typer(
    name="cuanta",
    help="cuanta — every token, accounted for.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)


@app.callback()
def root(
    ctx: typer.Context,
    plain: Annotated[bool, typer.Option("--plain", help="No color, emoji or animation.")] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="One JSON document on stdout.")
    ] = False,
    no_emoji: Annotated[bool, typer.Option("--no-emoji", help="Force ASCII glyphs.")] = False,
    theme: Annotated[ThemeName, typer.Option("--theme", help="Color theme.")] = ThemeName.AUTO,
    project: Annotated[
        Path | None, typer.Option("--project", help="Project folder (default: cwd).")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Assume yes to prompts.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="More detail.")] = False,
) -> None:
    ctx.obj = GlobalOptions(
        plain=plain,
        json=json_output,
        no_emoji=no_emoji,
        theme=theme,
        project=project,
        yes=yes,
        verbose=verbose,
    )


app.command("init", help="Bootstrap this folder: detect, graph, telemetry, Forge, verify.")(
    init.init_command
)
app.command("test", help="Gateway: run the suite once, cluster failures into hairballs.")(
    test.gateway_command
)
app.command("cat", help="Page through a stored capsule by level or line range.")(cat.cat_command)
app.command("listen", help="Local OTLP/JSON collector (--background, --status, --stop).")(
    listen.listen_command
)
app.add_typer(telemetry.telemetry_app, name="telemetry")
app.command("spectrum", help="Token map, leaks and Utilization Index for a run.")(
    spectrum.spectrum_command
)
app.add_typer(ledger.ledger_app, name="ledger")
app.add_typer(instinct.instinct_app, name="instinct")
app.add_typer(models.models_app, name="models")
app.add_typer(probe.probe_app, name="probe")
app.add_typer(bench.bench_app, name="bench")
app.add_typer(runs.runs_app, name="runs")
app.command("route", help="Plan which model each Forge role gets, and why (dry run).")(
    route.route_command
)
app.command("mandate", help="Fill the Forge mandate and run it headlessly.")(
    mandate.mandate_command
)
app.command("pounce", help="Alias of mandate.", hidden=True)(mandate.mandate_command)
app.command("refresh", help="Forge refresh, graph reindex, VERIFY_TIER drift report.")(
    refresh.refresh_command
)
app.command("loop", help="Guarded fix loop (VERIFY_TIER=strong only).")(loop.loop_command)
app.command("doctor", help="Environment and project health.")(doctor.doctor_command)
app.command("purr", help="Alias of doctor.", hidden=True)(doctor.doctor_command)
app.command("ui", help="Open the full-screen app (--web serves it in a browser).")(ui.ui_command)
app.command("meow", help="Wordmark, Michi, version and palette.")(meow.meow_command)


def _prefer_utf8_when_piped() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    arguments = sys.argv[1:]
    if not arguments and ui.interactive_terminal() and "NO_COLOR" not in os.environ:
        ui.launch(GlobalOptions())
        return
    _prefer_utf8_when_piped()
    app(args=hoist_globals(sys.argv[1:]), prog_name="cuanta")
