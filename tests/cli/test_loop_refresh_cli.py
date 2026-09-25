from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.bootstrap import Container
from tests.fakes import FakeRunner, copy_repo
from tests.support import FIXTURES, invoke

FAKE = FIXTURES / "fake_claude.py"
TEMPLATE = (
    Path(__file__).parents[2]
    / "src/cuanta/assets/forge/skills/agent-system-init/templates/MANDATE_TEMPLATE.template.md"
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner) -> dict[str, str]:
    original = Container.for_project

    def build(cls: type[Container], project: Path) -> Container:
        container = original(project)
        container.runner = SubprocessRunner()
        return container

    monkeypatch.setattr(Container, "for_project", classmethod(build))
    values = {
        "CUANTA_CLAUDE_BIN": f'"{sys.executable}" "{FAKE}"',
        "CUANTA_PORT": "47500",
        "FAKE_FORGE_TEMPLATE": str(TEMPLATE),
        "FAKE_CLAUDE_FIX": "src/calc/__init__.py|left - right|left + right",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def _configure(root: Path) -> None:
    command = f"'{sys.executable}' -m pytest -p no:cacheprovider".replace("\\", "/")
    config = root / ".cuanta" / "config.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(f'[test]\nrunner = "pytest"\ncommand = "{command}"\n', encoding="utf-8")


def test_loop_refuses_below_strong(tmp_path: Path, env: dict[str, str]) -> None:
    root = copy_repo("ts_moderate", tmp_path)
    result = invoke(["loop", "--json", "--project", str(root)], env=env)
    assert result.exit_code == 3
    error = json.loads(result.stdout)["error"]
    assert error["message"].startswith("whiskers too short")
    assert "VERIFY_TIER=moderate" in error["message"]
    pretty = invoke(["loop", "--project", str(root), "--no-emoji"], env=env, pretty=True)
    assert pretty.exit_code == 3
    assert "( O.O )" in pretty.stdout


def test_loop_fixes_then_stops_green(tmp_path: Path, env: dict[str, str]) -> None:
    root = copy_repo("bugfix", tmp_path)
    assert (
        invoke(["init", str(root), "--yes", "--json", "--skip-telemetry"], env=env).exit_code == 0
    )
    _configure(root)
    result = invoke(["loop", "--max-iterations", "2", "--json", "--project", str(root)], env=env)
    assert result.exit_code == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["stop"] == "green"
    assert len(report["iterations"]) == 1
    assert report["iterations"][0]["ok"] is True


def test_refresh_reports_tier_and_runs_forge(tmp_path: Path, env: dict[str, str]) -> None:
    root = copy_repo("bugfix", tmp_path)
    assert (
        invoke(["init", str(root), "--yes", "--json", "--skip-telemetry"], env=env).exit_code == 0
    )
    result = invoke(["refresh", "--json", "--project", str(root)], env=env)
    assert result.exit_code == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["transition"] == "verify: strong (unchanged)"
    assert [stage["stage"] for stage in report["stages"]] == ["graph", "forge", "verify"]
    assert report["run_id"]
