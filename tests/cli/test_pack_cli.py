from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cuanta.cli.app import app
from tests.fakes import FakeRunner


def test_pack_zero_spend_without_engine_or_ledger(tmp_path: Path, fake_runner: FakeRunner) -> None:
    (tmp_path / "cart.py").write_text("def checkout():\n    return 1\n", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "--project",
            str(tmp_path),
            "pack",
            "--for",
            "cart.py checkout",
            "--depth",
            "quick",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["cost_usd"] == 0.0
    assert payload["tokens"] <= payload["budget"] == 2000
    assert payload["cache_key"] and payload["decisions"]
    assert not fake_runner.calls and not (tmp_path / ".cuanta" / "ledger.db").exists()


def test_pack_rejects_invalid_depth(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["--project", str(tmp_path), "pack", "--for", "cart", "--depth", "infinite"]
    )
    assert result.exit_code != 0 and "Unknown pack depth" in result.output
