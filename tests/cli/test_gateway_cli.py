from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.bootstrap import Container
from tests.fakes import FakeRunner, copy_repo
from tests.support import invoke


@pytest.fixture
def real_processes(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner) -> None:
    original = Container.for_project

    def build(cls: type[Container], project: Path) -> Container:
        container = original(project)
        container.runner = SubprocessRunner()
        return container

    monkeypatch.setattr(Container, "for_project", classmethod(build))


def _configure(root: Path) -> None:
    (root / ".cuanta").mkdir(exist_ok=True)
    command = f"'{sys.executable}' -m pytest -p no:cacheprovider".replace("\\", "/")
    (root / ".cuanta" / "config.toml").write_text(
        f'[test]\nrunner = "pytest"\ncommand = "{command}"\n', encoding="utf-8"
    )


def test_cli_test_red_json_then_cat(tmp_path: Path, real_processes: None) -> None:
    root = copy_repo("failing_suite", tmp_path)
    _configure(root)
    result = invoke(["test", "--json", "--project", str(root)])
    assert result.exit_code == 1, result.stdout
    document = json.loads(result.stdout)
    assert document["status"] == "red"
    assert len(document["failures"]) == 3
    capsule = document["log_capsule"]
    shown = invoke(["cat", capsule, "--level", "L2", "--json", "--project", str(root)])
    assert shown.exit_code == 0
    window = json.loads(shown.stdout)
    assert window["level"] == "L2"
    assert 0 < len(window["lines"]) <= 40
    meta = invoke(["cat", capsule[:10], "--level", "L0", "--plain", "--project", str(root)])
    assert capsule in meta.stdout
    summary = invoke(["cat", capsule, "--level", "L1", "--json", "--project", str(root)])
    assert json.loads(summary.stdout)["lines"][0]["text"].startswith("pytest: 7 passed, 50 failed")
    sliced = invoke(["cat", capsule, "--lines", "1:3", "--json", "--project", str(root)])
    assert [line["n"] for line in json.loads(sliced.stdout)["lines"]] == [1, 2, 3]


def test_cli_test_pretty_shows_hairballs(tmp_path: Path, real_processes: None) -> None:
    root = copy_repo("failing_suite", tmp_path)
    _configure(root)
    result = invoke(["test", "--project", str(root), "--no-emoji"], pretty=True)
    assert result.exit_code == 1
    assert "50 failed in 3 hairballs - hiss" in result.stdout
    assert "cuanta cat cap:" in result.stdout


def test_cli_test_green_plain(tmp_path: Path, real_processes: None) -> None:
    root = copy_repo("python_strong", tmp_path)
    _configure(root)
    result = invoke(["test", "--plain", "--project", str(root)])
    assert result.exit_code == 0
    assert "+ 1 passed - purr" in result.stdout


def test_cli_test_without_runner_exits_three(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = copy_repo("js_weak", tmp_path)
    result = invoke(["test", "--json", "--project", str(root)])
    assert result.exit_code == 3
    error = json.loads(result.stdout)["error"]
    assert "VERIFY_TIER=weak" in error["message"]


def test_cat_unknown_capsule(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(["cat", "cap:nothing", "--plain", "--project", str(tmp_path)])
    assert result.exit_code == 1


def test_cat_bad_level(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = invoke(["cat", "cap:x", "--level", "L9", "--json", "--project", str(tmp_path)])
    assert result.exit_code == 1


def test_affected_runs_related_tests_after_a_full_baseline(
    tmp_path: Path, real_processes: None
) -> None:
    root = copy_repo("bugfix", tmp_path)
    (root / "tests" / "test_unrelated.py").write_text(
        "def test_unrelated() -> None:\n    assert True\n", encoding="utf-8"
    )
    _configure(root)
    project = ["--project", str(root)]
    first = json.loads(invoke(["test", "--affected", "--json", *project]).stdout)
    assert first["scope"]["mode"] == "full"
    assert "baseline" in first["scope"]["reason"]
    source = root / "src" / "calc" / "__init__.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    second = json.loads(invoke(["test", "--affected", "--json", *project]).stdout)
    assert second["scope"]["mode"] == "affected"
    assert second["scope"]["changed"] == ["src/calc/__init__.py"]
    assert second["scope"]["tests"] == ["tests/test_calc.py"]
    assert "test_unrelated" not in second["run"]["command"]
    assert "tests/test_calc.py" in second["run"]["command"]
    full = json.loads(invoke(["test", "--json", *project]).stdout)
    assert "scope" not in full
    after = json.loads(invoke(["test", "--affected", "--json", *project]).stdout)
    assert after["scope"]["mode"] == "full"
    assert "nothing changed" in after["scope"]["reason"]
