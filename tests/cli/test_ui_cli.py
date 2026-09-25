from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest
from rich.console import Console

from cuanta.cli.commands import ui
from cuanta.cli.document import Document, MascotBlock
from cuanta.cli.mascot import michi
from cuanta.cli.output import GlobalOptions, OutputMode, OutputSettings
from cuanta.cli.presenters.pretty import PrettyPresenter, build_theme
from cuanta.cli.theme import ThemeName
from cuanta.domain.voice import Mood
from tests.support import invoke, strip_ansi


def test_ui_refuses_json_and_plain(tmp_path: Path) -> None:
    for flag in ("--json", "--plain"):
        result = invoke([flag, "--project", str(tmp_path), "ui"])
        assert result.exit_code != 0
        assert "interactive terminal" in result.stderr


def test_ui_refuses_a_pipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "interactive_terminal", lambda: False)
    result = invoke(["--project", str(tmp_path), "ui"])
    assert result.exit_code != 0
    assert "from a terminal" in result.stderr


def test_ui_theme_precedence() -> None:
    plain = GlobalOptions()
    assert ui.ui_theme(plain, "ansi", "calico-light") == "ansi"
    assert ui.ui_theme(GlobalOptions(theme=ThemeName.LIGHT), "", "") == "calico-light"
    assert ui.ui_theme(plain, "", "calico-light") == "calico-light"
    assert ui.ui_theme(plain, "", "dark") == "calico-dark"
    assert ui.ui_theme(plain, "", "") == "auto"


def test_no_arguments_without_a_terminal_prints_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "cuanta"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert "Usage" in completed.stdout + completed.stderr


def test_help_does_not_import_textual() -> None:
    code = (
        "import sys; from cuanta.cli.app import main; sys.argv=['cuanta','--help']\n"
        "try:\n    main()\nexcept SystemExit:\n    pass\n"
        "print('TEXTUAL' if 'textual' in sys.modules else 'LEAN', file=sys.stderr)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert completed.stderr.strip().endswith("LEAN")


EARS = michi(Mood.HAPPY)[0].strip()


def _mascot(color_system: Literal["256", "truecolor"], emoji: bool = True) -> str:
    settings = OutputSettings(OutputMode.PRETTY, ThemeName.DARK, True, emoji, False)
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        width=80,
        force_terminal=True,
        color_system=color_system,
        theme=build_theme(settings),
    )
    PrettyPresenter(settings, console).render(Document(blocks=(MascotBlock(Mood.HAPPY, "hi"),)))
    return strip_ansi(buffer.getvalue())


def test_pretty_michi_is_pixel_art_only_with_truecolor_and_emoji() -> None:
    assert "▀" in _mascot("truecolor")
    assert EARS in _mascot("256")
    assert EARS in _mascot("truecolor", emoji=False)


def test_the_textual_web_driver_counts_as_interactive() -> None:
    from cuanta.cli.commands.ui import interactive_terminal

    driver = {"TEXTUAL_DRIVER": "textual.drivers.web_driver:WebDriver"}
    assert interactive_terminal(driver)
    assert interactive_terminal({"CUANTA_FORCE_TTY": "1"})
