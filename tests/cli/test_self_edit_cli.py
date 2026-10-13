from __future__ import annotations

from pathlib import Path

import pytest

from tests.cli.test_engine_guarantees import forge
from tests.cli.test_mandate_from_cli import run_cli, written
from tests.fakes import FakeRunner


def test_a_run_from_an_editable_install_inside_the_project_warns_at_launch(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cuanta

    forge(tmp_path)
    monkeypatch.setattr(cuanta, "__file__", str(tmp_path / "src" / "cuanta" / "__init__.py"))
    path = written(tmp_path, "TYPE: bug\nWHAT: Fix the cart total\nWHERE: src/cart.py\n")
    shown = run_cli(tmp_path, "run", str(path), "--dry-run", "--lang", "en")
    assert shown.exit_code == 0, shown.stdout
    assert "editable install inside this project" in " ".join(shown.stdout.split())
    copy = run_cli(tmp_path, "run", str(path), "--dry-run", "--sandbox", "--lang", "en")
    assert "editable install inside this project" not in " ".join(copy.stdout.split())
