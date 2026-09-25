from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from typer.testing import CliRunner, Result

from cuanta.cli.app import app
from cuanta.cli.group import hoist_globals

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
GOLDEN_DIR = Path(__file__).parent / "cli" / "golden"
FIXTURES = Path(__file__).parent / "fixtures"


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def invoke(
    args: Sequence[str],
    env: Mapping[str, str | None] | None = None,
    pretty: bool = False,
    input_text: str | None = None,
) -> Result:
    environment: dict[str, str | None] = {
        "COLUMNS": "100",
        "LINES": "50",
        "NO_COLOR": None,
        "COLORFGBG": None,
        "CUANTA_FORCE_TTY": "1" if pretty else None,
        "CUANTA_THEME": None,
        "CUANTA_ENGINE": None,
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
    }
    if env:
        environment.update(env)
    runner = CliRunner()
    return runner.invoke(
        app, hoist_globals(list(args)), env=environment, input=input_text, catch_exceptions=False
    )


def assert_golden(name: str, actual: str) -> None:
    path = GOLDEN_DIR / name
    normalized = "\n".join(line.rstrip() for line in strip_ansi(actual).splitlines()) + "\n"
    if os.environ.get("CUANTA_UPDATE_GOLDEN") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized, encoding="utf-8", newline="\n")
    expected = path.read_text(encoding="utf-8")
    assert normalized == expected
