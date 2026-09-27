from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cuanta.cli.app import app
from tests.fakes import FakeRunner


def test_plan_for_defaults_to_zero_spend_and_no_ledger(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    (tmp_path / "cart.py").write_bytes(b"checkout = 1\n")
    (tmp_path / "renderer").mkdir()
    (tmp_path / "renderer" / "view.py").write_bytes(b"render = 1\n")
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "--project",
            str(tmp_path),
            "plan",
            "--for",
            "Fix cart.py checkout",
            "--out-of-scope",
            "renderer/**",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["cost_usd"] == 0.0
    assert any(item["path"] == "cart.py" for item in payload["edit"])
    assert "Write(renderer/**)" in payload["guard_rules"]
    assert "Bash" not in payload["tools"]
    assert not fake_runner.calls and not (tmp_path / ".cuanta" / "ledger.db").exists()
