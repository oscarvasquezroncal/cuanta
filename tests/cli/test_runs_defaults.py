from __future__ import annotations

import json
from pathlib import Path

from typer.testing import Result

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.domain.ledger import Run
from tests.fakes import FakeRunner
from tests.support import invoke

STARTED = "2026-10-02T13:30:00Z"
SHOWN = 10


def seed(root: Path, *runs: Run) -> None:
    (root / ".cuanta").mkdir(parents=True, exist_ok=True)
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    try:
        for run in runs:
            ledger.add_run(run)
    finally:
        ledger.close()


def mandate(run_id: str, **changes: str) -> Run:
    return Run(
        run_id,
        changes.get("kind", "mandate"),
        "claude",
        started_at=STARTED,
        ended_at=STARTED,
        status=changes.get("status", "ok"),
        parent_id=changes.get("parent_id", ""),
    )


def runs(root: Path, *args: str) -> Result:
    return invoke(["runs", *args, "--project", str(root)])


def shown_id(root: Path) -> str:
    result = runs(root, "show", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    return str(json.loads(result.stdout)["run_id"])


def test_runs_show_without_an_id_shows_the_last_run_a_person_launched(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    seed(
        tmp_path,
        mandate("01JA0MANDATE"),
        mandate("01JB0TEAMROOT", kind="cross", status="failed"),
        mandate("01JC0TEAMROLE", kind="cross", parent_id="01JB0TEAMROOT"),
    )
    assert shown_id(tmp_path) == "01JB0TEAMROOT"
    seed(tmp_path, mandate("01JD0LATEST", status="interrupted"))
    assert shown_id(tmp_path) == "01JD0LATEST"
    plain = runs(tmp_path, "show", "--plain")
    assert plain.exit_code == 0, plain.stdout + plain.stderr
    assert "01JD0LATEST" in plain.stdout
    by_prefix = json.loads(runs(tmp_path, "show", "01JA", "--json").stdout)
    assert by_prefix["run_id"] == "01JA0MANDATE"


def test_runs_show_without_runs_says_so_and_creates_nothing(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = runs(tmp_path, "show", "--json")
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["message"] == "no runs yet"
    assert "cuanta run" in error["hint"]
    assert not (tmp_path / ".cuanta").exists()


def test_runs_alone_lists_the_last_ten(tmp_path: Path, fake_runner: FakeRunner) -> None:
    seed(tmp_path, *(mandate(f"01JR{index:02d}") for index in range(12)))
    listed = runs(tmp_path, "--json")
    assert listed.exit_code == 0, listed.stdout + listed.stderr
    ids = [row["id"] for row in json.loads(listed.stdout)["runs"]]
    assert ids == [f"01JR{index:02d}" for index in range(11, 11 - SHOWN, -1)]
    plain = runs(tmp_path, "--plain")
    assert plain.exit_code == 0, plain.stdout + plain.stderr
    assert "01JR11" in plain.stdout and "01JR01" not in plain.stdout
    assert "Usage:" not in plain.stdout
    assert "cuanta runs show [id] · cuanta runs open <id>" in plain.stdout
    every = json.loads(runs(tmp_path, "list", "--json").stdout)["runs"]
    assert len(every) == 12
    helped = runs(tmp_path, "--help")
    assert helped.exit_code == 0
    assert "Usage:" in helped.stdout and "show" in helped.stdout


def test_the_help_screen_teaches_runs_without_an_id(tmp_path: Path) -> None:
    for language, last in (("en", "(the last run)"), ("es", "(la última)")):
        result = invoke(["help", "--lang", language, "--json", "--project", str(tmp_path)])
        items = {item["key"]: item["command"] for item in json.loads(result.stdout)["items"]}
        assert items["result"].startswith(f"cuanta runs show  {last} · cuanta runs  ")


def test_runs_alone_without_runs_points_to_the_first_one(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = runs(tmp_path, "--plain")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "no runs yet · start one: cuanta run mandate.md" in result.stdout
    assert "cuanta test" not in result.stdout
    assert not (tmp_path / ".cuanta").exists()


def test_runs_show_inside_a_copy_reads_the_report_from_the_state_root(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    copy = tmp_path / "copy"
    copy.mkdir()
    seed(origin, mandate("01JA0ROOT"))
    RunReports(LocalWorkspace(origin)).save_report("01JA0ROOT", "# Report\n\nthe total is fixed\n")
    for project, env in ((origin, {}), (copy, {"CUANTA_STATE_ROOT": str(origin)})):
        result = invoke(["runs", "show", "--json", "--project", str(project)], env=env)
        assert result.exit_code == 0, result.stdout + result.stderr
        data = json.loads(result.stdout)
        assert data["run_id"] == "01JA0ROOT"
        assert "the total is fixed" in data["report"], project
    assert not (copy / ".cuanta").exists()
