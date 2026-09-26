from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.bootstrap import Container
from cuanta.domain.bench import README_END, README_START
from tests.fakes import FakeRunner
from tests.support import FIXTURES, invoke

ROOT = Path(__file__).parents[2]
FAKE = FIXTURES / "fake_claude.py"


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
        "CUANTA_PORT": "47420",
        "FAKE_CLAUDE_BENCH": "1",
        "FAKE_CLAUDE_FIX": "src/calc/__init__.py|left - right|left + right",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def _args(project: Path, *extra: str) -> list[str]:
    return [
        "bench",
        "run",
        "--project",
        str(project),
        "--tasks",
        str(ROOT / "bench" / "tasks"),
        "--fixtures",
        str(FIXTURES / "repos"),
        "--json",
        *extra,
    ]


def test_bench_without_yes_only_shows_the_ceiling(tmp_path: Path, env: dict[str, str]) -> None:
    result = invoke(_args(tmp_path, "--reps", "2", "--per-run-usd", "1"), env=env)
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document == {"ran": False, "runs": 30, "ceiling_usd": 30.0}
    assert not (tmp_path / ".cuanta" / "bench").exists()


@pytest.mark.timeout(300)
def test_mini_bench_completes_with_the_fake_engine(tmp_path: Path, env: dict[str, str]) -> None:
    result = invoke(
        _args(tmp_path, "--suite", "mini", "--reps", "1", "--seed", "7", "--yes"), env=env
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["ran"] is True
    runs = document["runs"]
    assert len(runs) == 15
    assert {run["condition"] for run in runs} == {"baseline", "cuanta", "cuanta-routed"}
    solved = {(run["task"], run["condition"]) for run in runs if run["accepted"]}
    assert solved == {
        ("calc-add", "baseline"),
        ("calc-add", "cuanta"),
        ("calc-add", "cuanta-routed"),
    }
    assert all(run["run_id"] for run in runs)
    assert all(run["fresh_tokens"] + run["output_tokens"] > 0 for run in runs)
    routed = [run for run in runs if run["condition"] == "cuanta-routed"]
    assert all(run["models"] for run in routed)
    folder = tmp_path / ".cuanta" / "bench" / document["bench_id"]
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "| baseline | 5 | 1 | 20% |" in report
    for chart in ("tokens.svg", "success.svg", "cost.svg"):
        assert (folder / chart).read_text(encoding="utf-8").startswith("<svg")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    published = invoke(["bench", "report", "--readme", "--project", str(tmp_path)], env=env)
    assert published.exit_code == 0, published.stdout + published.stderr
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## Benchmark" in readme
    assert README_START in readme
    assert README_END in readme
    assert "| baseline | 1/5 |" in readme
    assert (tmp_path / "docs" / "bench" / "tokens.svg").is_file()
