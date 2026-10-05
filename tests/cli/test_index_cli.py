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


def test_rebuild_after_excluding_a_bulky_folder_reclaims_the_space(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
    bulky = tmp_path / ".venv312" / "Lib" / "site-packages" / "big"
    bulky.mkdir(parents=True)
    body = "".join(
        f"def helper_{number}(value: int) -> int:\n    return value + {number}\n\n"
        for number in range(200)
    )
    for number in range(40):
        (bulky / f"mod{number}.py").write_text(body, encoding="utf-8")
    arguments = ["index", "--json", "--project", str(tmp_path)]
    first = invoke(arguments)
    assert first.exit_code == 0, first.stdout
    assert json.loads(first.stdout)["files"] == 41
    state = tmp_path / ".cuanta"
    bloated = sum(
        item.stat().st_size
        for item in state.iterdir()
        if item.is_file() and item.name != "inventory-hashes.json"
    )
    (state / "config.toml").write_text('[detect]\nexclude = [".venv312"]\n', encoding="utf-8")
    rebuilt = invoke([*arguments, "--rebuild"])
    assert rebuilt.exit_code == 0, rebuilt.stdout
    report = json.loads(rebuilt.stdout)
    assert report["files"] == 1
    names = sorted(item.name for item in state.iterdir() if item.name != "index.db.hold")
    assert names == ["config.toml", "index.db", "inventory-hashes.json"]
    assert sum(item.stat().st_size for item in state.iterdir() if item.is_file()) * 4 < bloated
    assert report["reclaimed_bytes"] == bloated - (state / "index.db").stat().st_size
    assert not fake_runner.calls
