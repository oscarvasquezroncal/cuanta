from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.bootstrap import Container
from cuanta.domain.ledger import Run
from tests.fakes import FakeRunner
from tests.support import FIXTURES, invoke

ROOT = Path(__file__).parents[2]
FAKE = FIXTURES / "fake_claude.py"


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner) -> dict[str, str]:
    original = Container.for_project

    def build(cls: type[Container], project: Path, verbose: bool = False) -> Container:
        container = original(project, verbose)
        container.runner = SubprocessRunner()
        return container

    monkeypatch.setattr(Container, "for_project", classmethod(build))
    values = {
        "CUANTA_CLAUDE_BIN": f'"{sys.executable}" "{FAKE}"',
        "CUANTA_PORT": "47421",
        "FAKE_CLAUDE_BENCH": "1",
        "FAKE_CLAUDE_FIX": "src/calc/__init__.py|left - right|left + right",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def compare(project: Path, *extra: str, output: str = "--json") -> list[str]:
    return [
        "bench",
        "run",
        "--project",
        str(project),
        "--tasks",
        str(ROOT / "bench" / "tasks"),
        "--fixtures",
        str(FIXTURES / "repos"),
        "--suite",
        "mini",
        "--task",
        "calc-add",
        "--reps",
        "1",
        output,
        *extra,
    ]


def test_the_preview_lists_planned_runs_caps_and_the_worst_case(
    tmp_path: Path, fake_claude: dict[str, str]
) -> None:
    result = invoke(
        compare(
            tmp_path,
            "--compare",
            "read-discipline",
            "--arm-cap",
            "discipline-on=0.3",
            "--per-run-usd",
            "0.4",
            "--budget-usd",
            "1",
        ),
        env=fake_claude,
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["ran"] is False and document["comparison"] == "read-discipline"
    assert document["runs"] == 2
    assert document["caps_total_usd"] == pytest.approx(0.7)
    assert document["overshoot_usd"] == pytest.approx(0.1)
    assert document["ceiling_usd"] == pytest.approx(0.9)
    assert sorted((row["arm"], row["cap_usd"]) for row in document["planned"]) == [
        ("discipline-off", 0.4),
        ("discipline-on", 0.3),
    ]
    assert not (tmp_path / ".cuanta" / "bench").exists()
    unfit = invoke(
        compare(tmp_path, "--compare", "warm-queue", "--per-run-usd", "0.4", "--budget-usd", "0.5"),
        env=fake_claude,
    )
    assert json.loads(unfit.stdout)["ceiling_usd"] == 0.0
    chosen = invoke(
        compare(
            tmp_path,
            "--compare",
            "warm-queue",
            "--per-run-usd",
            "0.4",
            "--budget-usd",
            "0.9",
            "--overshoot-usd",
            "0.25",
        ),
        env=fake_claude,
    )
    picked = json.loads(chosen.stdout)
    assert picked["overshoot_usd"] == 0.25 and picked["ceiling_usd"] == pytest.approx(1.3)
    plain = invoke(
        compare(tmp_path, "--compare", "finish", "--per-run-usd", "0.12", output="--plain"),
        env=fake_claude,
    )
    assert plain.exit_code == 0, plain.stdout
    assert "tight-cap $0.12" in plain.stdout and "tight-cap: runs.governor=true" in plain.stdout
    assert "at most $0.22 if no run passes its cap by more than $0.10" in plain.stdout
    assert "planned runs" in plain.stdout and "nothing spent" in plain.stdout
    assert not (tmp_path / ".cuanta" / "ledger.db").exists()


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (("--arm-cap", "scout=0.5"), "--arm-cap needs --compare"),
        (("--overshoot-usd", "0.2"), "--overshoot-usd needs --compare"),
        (("--compare", "everything"), "unknown comparison everything"),
        (("--compare", "scout", "--arm-cap", "tight-cap=0.1"), "names no arm of scout"),
        (("--compare", "scout", "--conditions", "cuanta"), "--conditions does not apply"),
        (("--compare", "scout", "--shape", "pipeline"), "sets the shape of each arm"),
        (("--compare", "finish", "--session", "both"), "one session profile"),
    ],
)
def test_comparisons_refuse_options_they_cannot_honor(
    tmp_path: Path, fake_claude: dict[str, str], flags: tuple[str, ...], message: str
) -> None:
    result = invoke(compare(tmp_path, *flags), env=fake_claude)
    assert result.exit_code == 1, result.stdout
    assert message in json.loads(result.stdout)["error"]["message"]
    assert not (tmp_path / ".cuanta" / "bench").exists()


@pytest.mark.timeout(300)
def test_a_warm_queue_pair_runs_and_reports_its_target(
    tmp_path: Path, fake_claude: dict[str, str]
) -> None:
    result = invoke(
        compare(
            tmp_path, "--compare", "warm-queue", "--seed", "3", "--per-run-usd", "0.5", "--yes"
        ),
        env=fake_claude,
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["ran"] is True
    runs = document["runs"]
    assert [run["proof"]["arm"] for run in runs] == ["queue-first", "queue-second"]
    assert {run["proof"]["group"] for run in runs} == {1}
    assert all(run["proof"]["comparison"] == "warm-queue" for run in runs)
    assert all(run["proof"]["cap_usd"] == 0.5 for run in runs)
    assert all(run["run_id"] for run in runs)
    (target,) = document["targets"]
    assert target["target"] == "warm-queue" and target["verdict"] in {"met", "missed", "n/a"}
    folder = Path(document["folder"])
    report = (tmp_path / folder / "report.md").read_text(encoding="utf-8")
    assert "## Targets" in report and "| warm-queue |" in report
    assert "cuanta · queue-first" in report
    again = invoke(["bench", "report", "--project", str(tmp_path), "--json"], env=fake_claude)
    assert again.exit_code == 0, again.stdout
    assert json.loads(again.stdout)["targets"] == document["targets"]
    shown = invoke(["bench", "report", "--project", str(tmp_path), "--plain"], env=fake_claude)
    assert "targets" in shown.stdout and "warm-queue" in shown.stdout


def test_the_default_overshoot_is_the_largest_one_the_ledger_measured(
    tmp_path: Path, fake_claude: dict[str, str]
) -> None:
    container = Container.for_project(tmp_path)
    try:
        container.ledger().add_run(
            Run(
                "OVER",
                "mandate",
                "claude",
                model="claude-sonnet-5",
                status="failed",
                cost_usd=0.72,
                cap_usd=0.5,
                end_reason="error_max_budget_usd",
            )
        )
    finally:
        container.close()
    result = invoke(
        compare(tmp_path, "--compare", "finish", "--per-run-usd", "0.3", "--budget-usd", "1"),
        env=fake_claude,
    )
    document = json.loads(result.stdout)
    assert document["overshoot_usd"] == pytest.approx(0.22)
    assert document["ceiling_usd"] == pytest.approx(0.52)
