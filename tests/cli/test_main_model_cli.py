from __future__ import annotations

import json
from pathlib import Path

from tests.cli.test_engine_guarantees import forge
from tests.fakes import FakeRunner
from tests.support import invoke

TEAM = ("mandate", "-t", "refactor", "--what", "split the interpreter", "-p", "balanced")
MAIN_LINE = (
    "team · orchestrator → opus (premium) · "
    "the main session runs on the chosen model claude-opus-5-5"
)


def run(root: Path, *args: str) -> tuple[int, dict[str, object]]:
    result = invoke([*args, "--dry-run", "--json", "--project", str(root)])
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    return result.exit_code, data


def listed(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(item) for item in value]


def settings_of(data: dict[str, object]) -> dict[str, object]:
    command = listed(data["command"])
    found = json.loads(Path(command[command.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert isinstance(found, dict)
    return found


def test_the_main_session_model_is_the_orchestrator_of_the_team(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    code, data = run(tmp_path, *TEAM, "-m", "claude-opus-5-5")
    assert code == 0, data
    team = listed(data["team"])
    assert MAIN_LINE in team
    assert not any(line.startswith("team · orchestrator → sonnet") for line in team)
    assert data["model"] == "claude-opus-5-5"
    code, same = run(tmp_path, *TEAM, "-m", "claude-opus-5-5", "--role-model", "orchestrator=opus")
    assert code == 0, same
    code, plain = run(tmp_path, *TEAM)
    assert code == 0, plain
    assert any(line.startswith("team · orchestrator → sonnet") for line in listed(plain["team"]))
    assert not fake_runner.stdins


def test_an_orchestrator_pin_on_another_model_than_the_main_session_is_refused(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    code, data = run(
        tmp_path, *TEAM, "-m", "claude-opus-5-5", "--role-model", "orchestrator=sonnet"
    )
    assert code == 1, data
    error = data["error"]
    assert isinstance(error, dict)
    assert error["message"] == (
        "the orchestrator is pinned to sonnet, but the main session runs on claude-opus-5-5"
    )
    assert error["hint"] == "pin the orchestrator to the main session model, or drop the pin"
    assert not fake_runner.stdins


def test_fast_output_runs_on_a_balanced_team_whose_main_session_is_opus(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    forge(tmp_path)
    code, data = run(tmp_path, *TEAM, "-m", "claude-opus-5-5", "-v", "fast-low")
    assert code == 0, data
    settings = settings_of(data)
    assert settings["fastMode"] is True
    assert settings["effortLevel"] == "low"
    assert "model" not in settings
    code, refused = run(tmp_path, *TEAM, "-v", "fast-low")
    assert code == 1, refused
    error = refused["error"]
    assert isinstance(error, dict)
    assert error["message"] == "fast output requires a supported Opus model"
    assert not fake_runner.stdins
