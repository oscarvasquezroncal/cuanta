from __future__ import annotations

import json
from pathlib import Path

from tests.fakes import FakeRunner
from tests.support import invoke


def test_index_update_status_rebuild_and_recovery(tmp_path: Path, fake_runner: FakeRunner) -> None:
    (tmp_path / "globals.css").write_text("body { color: red; }\n", encoding="utf-8")
    arguments = ["index", "--json", "--project", str(tmp_path)]
    first = invoke(arguments)
    assert first.exit_code == 0, first.stdout
    assert json.loads(first.stdout)["changed"] == 1
    status = invoke([*arguments, "--status"])
    assert status.exit_code == 0
    assert json.loads(status.stdout)["changed"] == 0
    assert json.loads(status.stdout)["files"] == 1
    rebuilt = invoke([*arguments, "--rebuild"])
    assert rebuilt.exit_code == 0, rebuilt.stdout
    assert json.loads(rebuilt.stdout)["changed"] == 1
    database = tmp_path / ".cuanta" / "index.db"
    database.write_bytes(b"corrupted index")
    recovered = invoke(arguments)
    assert recovered.exit_code == 0, recovered.stdout
    assert json.loads(recovered.stdout)["recovered"]
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()
    database.unlink()
    recreated = invoke(arguments)
    assert recreated.exit_code == 0
    assert json.loads(recreated.stdout)["changed"] == 1
    assert not fake_runner.calls
