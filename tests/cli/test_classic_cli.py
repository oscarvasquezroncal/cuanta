from __future__ import annotations

import json
from pathlib import Path

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.run_reports import RunReports
from cuanta.ports.system import Completed
from tests.cli.conftest import CLAUDE_HELP
from tests.cli.test_engine_guarantees import cross_args, forge
from tests.cli.test_scout_cli import agents_of, configure, preview
from tests.fakes import FakeRunner, FakeStream
from tests.support import invoke

RESULT = '{"type":"result","subtype":"success","total_cost_usd":0.01,"is_error":false}'


def test_classic_keeps_the_pipeline_and_docs_where_v5_would_scout(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    configure(tmp_path, "[runs]\nscout_threshold = 0.01\n")
    v5 = preview(tmp_path)
    shape = v5["shape"]
    assert isinstance(shape, dict) and shape["shape"] == "scout"
    assert "docs-updater" not in agents_of(v5)
    classic = preview(tmp_path, "--classic")
    forced = classic["shape"]
    assert isinstance(forced, dict)
    assert (forced["shape"], forced["forced"]) == ("pipeline", True)
    agents = agents_of(classic)
    assert "scout" not in agents
    assert {"architecture-analyst", "docs-updater"} <= set(agents)
    assert classic["docs"] is None
    assert "Docs are off for this run" not in str(classic["prompt"])
    assert not fake_runner.stdins


def test_classic_refuses_a_scout_shape(tmp_path: Path, fake_runner: FakeRunner) -> None:
    forge(tmp_path)
    result = invoke(cross_args(tmp_path, "--classic", "--shape", "scout", "--dry-run", "--json"))
    assert result.exit_code == 1, result.stdout
    assert "--classic runs without a scout" in json.loads(result.stdout)["error"]["message"]
    assert not fake_runner.stdins


def launch(tmp_path: Path, fake_runner: FakeRunner, *flags: str) -> tuple[list[str], str]:
    before = len(fake_runner.calls)
    result = invoke(
        cross_args(tmp_path, "--route", "fixed", "--max-budget-usd", "1", "--json", *flags)
    )
    assert result.exit_code == 0, result.stdout
    calls = [call for call in fake_runner.calls[before:] if call[:2] == ("claude", "-p")]
    assert len(calls) == 1
    return list(calls[0]), str(json.loads(result.stdout)["run_id"])


def test_a_classic_run_launches_without_steering_and_records_its_mode(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    fake_runner.responses["claude --help"] = Completed(0, CLAUDE_HELP + " --input-format", "")
    fake_runner.streams["claude -p"] = FakeStream([RESULT])
    governed, v5_run = launch(tmp_path, fake_runner, "--profile", "balanced")
    assert governed[governed.index("--input-format") + 1] == "stream-json"
    plain, classic_run = launch(tmp_path, fake_runner, "--classic")
    assert "--input-format" not in plain
    reports = RunReports(LocalWorkspace(tmp_path))
    classic_meta = reports.meta(classic_run)
    v5_meta = reports.meta(v5_run)
    assert classic_meta is not None and classic_meta["mode"] == "classic"
    assert v5_meta is not None and "mode" not in v5_meta
    costs = invoke(["costs", "--metrics", "--json", "--project", str(tmp_path)])
    assert costs.exit_code == 0, costs.stdout
    modes = {row["run_id"]: row["mode"] for row in json.loads(costs.stdout)["metrics"]}
    assert modes == {v5_run: "v5", classic_run: "classic"}


def test_a_queued_mandate_keeps_its_classic_flag(tmp_path: Path, fake_runner: FakeRunner) -> None:
    forge(tmp_path)
    added = invoke(
        [
            "queue",
            "add",
            "--project",
            str(tmp_path),
            "--type",
            "bug",
            "--what",
            "Fix incorrect addition",
            "--why",
            "The sum is wrong",
            "--out-of-scope",
            "Documentation",
            "--classic",
        ]
    )
    assert added.exit_code == 0, added.stdout
    stored = json.loads((tmp_path / ".cuanta" / "queue.json").read_text(encoding="utf-8"))
    assert "--classic" in stored["items"][0]["args"]
    refused = invoke(
        [
            "queue",
            "add",
            "--project",
            str(tmp_path),
            "--type",
            "bug",
            "--what",
            "w",
            "--why",
            "y",
            "--out-of-scope",
            "o",
            "--classic",
            "--shape",
            "scout",
        ]
    )
    assert refused.exit_code != 0
    assert "--classic runs without a scout" in refused.stdout + refused.stderr
