from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cuanta import __version__
from cuanta.cli.app import app


def test_version_exits_without_a_project_or_command(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["--project", str(tmp_path), "--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout == f"cuanta {__version__}\n"
    assert not tuple(tmp_path.iterdir())
