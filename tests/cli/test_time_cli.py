from __future__ import annotations

import json
from pathlib import Path

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.cli.time import time_blocks
from cuanta.domain.ledger import Run
from cuanta.domain.time_anatomy import TimeReport
from tests.cli.test_costs_cli import _seed
from tests.fakes import FakeRunner
from tests.support import invoke
from tests.tui.test_time_ui import sample_time
from tests.unit.test_time_costs import phase_event


def test_cli_time_blocks_show_request_latency_and_unknown_duration() -> None:
    blocks = str(time_blocks(sample_time()))
    for value in ("90.500", "11.250", "n/a", "20.125", "0.750", "request-1"):
        assert value in blocks
    assert "n/a" in str(time_blocks(TimeReport()))


def test_costs_runs_and_spectrum_expose_persisted_timing(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    _seed(
        tmp_path,
        Run(
            "RUN",
            "mandate",
            "claude",
            "sonnet",
            started_at="2025-12-20T10:00:00Z",
            ended_at="2025-12-20T10:02:00Z",
            status="ok",
            task_type="feature",
        ),
    )
    ledger = SqliteLedger(tmp_path / ".cuanta" / "ledger.db")
    ledger.add_events((phase_event("RUN", 2.75), phase_event("RUN", 120.0, "run_wall")))
    ledger.close()
    RunReports(LocalWorkspace(tmp_path)).save_meta(
        "RUN", {"variant": "low", "implementation_profile": "fast"}
    )
    result = invoke(["costs", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    row = json.loads(result.stdout)["time_medians"][0]
    assert (row["task_type"], row["model"], row["variant"], row["profile"]) == (
        "feature",
        "sonnet",
        "low",
        "fast",
    )
    verify = next(phase for phase in row["phases"] if phase["phase"] == "verification")
    assert verify["seconds"] == 2.75 and verify["samples"] == 1
    plain = invoke(["costs", "--plain", "--project", str(tmp_path)])
    assert plain.exit_code == 0, plain.stdout
    assert "median seconds" in plain.stdout and "2.750" in plain.stdout and "n/a" in plain.stdout
    for command in (["runs", "show", "RUN"], ["spectrum", "RUN"]):
        shown = invoke([*command, "--json", "--project", str(tmp_path)])
        assert shown.exit_code == 0, shown.stdout
        timing = json.loads(shown.stdout)["time"]
        assert timing["wall_seconds"] == 120.0
        assert (
            next(phase["seconds"] for phase in timing["phases"] if phase["phase"] == "verification")
            == 2.75
        )
