from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "tests" / "performance.py"
INHERITED = ("PYTEST_", "COV_CORE_", "COVERAGE_")
PERFORMANCE_TESTS = (
    "import json\nimport os\nimport sys\nfrom pathlib import Path\n"
    "import pytest\nimport sample\n\n"
    "@pytest.mark.perf\n"
    "@pytest.mark.parametrize('value', [1, 2], ids=['alpha space', 'bracket[edge]'])\n"
    "def test_performance(value, request):\n"
    "    assert 'retained_metadata' not in sys.modules\n"
    "    assert 'PYTEST_XDIST_WORKER' not in os.environ\n"
    "    assert sample.performance() == 99\n"
    "    with Path('executed.jsonl').open('a') as stream:\n"
    "        stream.write(json.dumps({'nodeid': request.node.nodeid, 'pid': os.getpid()}) + '\\n')\n"
    "    if Path('fail-performance').exists():\n"
    "        pytest.fail('intentional performance assertion')\n\n"
    "@pytest.mark.live\n@pytest.mark.perf\n"
    "def test_live_performance():\n"
    "    Path('LIVE-RAN').write_text('unexpected')\n"
    "    pytest.fail('live test must not run')\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -q --strict-markers -p no:cacheprovider "
        '--dist=loadgroup --max-worker-restart=0 -m "not live"\n'
        "timeout = 120\nmarkers =\n    live: opt-in\n    perf: performance budget\n",
        encoding="utf-8",
    )
    (tmp_path / ".coveragerc").write_text(
        "[run]\nsource = sample\nbranch = true\ndata_file = .coverage\n[report]\nfail_under = 85\n",
        encoding="utf-8",
    )
    (tmp_path / "sample.py").write_text(
        "def normal():\n    total = 0\n"
        + "    total += 1\n" * 24
        + "    return total\n\ndef performance():\n    marker = 99\n    return marker\n",
        encoding="utf-8",
    )
    (tmp_path / "retained_metadata.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "with Path('imports.txt').open('a') as stream:\n"
        "    stream.write(str(os.getpid()) + '\\n')\n",
        encoding="utf-8",
    )
    (tmp_path / "test_unrelated.py").write_text(
        "import retained_metadata\nimport sample\n\n"
        "def test_functional():\n    assert sample.normal() == 24\n",
        encoding="utf-8",
    )
    (tmp_path / "test_performance.py").write_text(PERFORMANCE_TESTS, encoding="utf-8")
    return tmp_path


def invoke(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(INHERITED) and key != "PYTHONPATH"
    }
    return subprocess.run(
        [sys.executable, *args],
        cwd=project,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=45,
    )


def seed_coverage(project: Path) -> None:
    result = invoke(
        project,
        "-m",
        "pytest",
        "-n",
        "0",
        "-m",
        "not live and not perf",
        "--cov",
        "--cov-report=term",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "93.33%" in result.stdout


def covered_lines(project: Path) -> set[int]:
    data = CoverageData(basename=str(project / ".coverage"))
    data.read()
    filename = next(name for name in data.measured_files() if Path(name).name == "sample.py")
    return set(data.lines(filename) or ())


def test_performance_gate_uses_fresh_process_and_exact_selection_with_accumulated_coverage(
    project: Path,
) -> None:
    seed_coverage(project)
    before = covered_lines(project)
    result = invoke(project, str(GATE))
    assert result.returncode == 0, result.stdout + result.stderr
    executed = [json.loads(line) for line in (project / "executed.jsonl").read_text().splitlines()]
    assert [record["nodeid"] for record in executed] == [
        "test_performance.py::test_performance[alpha space]",
        "test_performance.py::test_performance[bracket[edge]]",
    ]
    execution_pids = {record["pid"] for record in executed}
    discovery_imports = (project / "imports.txt").read_text().splitlines()
    assert len(execution_pids) == 1 and len(discovery_imports) == 2
    assert not (project / "LIVE-RAN").exists()
    assert before < covered_lines(project)
    assert "100.00%" in result.stdout and "85.0%" in result.stdout


@pytest.mark.parametrize("problem", ["error", "empty", "duplicate"])
def test_performance_discovery_fails_closed_without_executing_tests(
    project: Path, problem: str
) -> None:
    if problem == "error":
        (project / "test_broken.py").write_text(
            "raise RuntimeError('collection failed')\n", encoding="utf-8"
        )
    elif problem == "empty":
        (project / "test_performance.py").write_text(
            "def test_plain():\n    pass\n", encoding="utf-8"
        )
    else:
        (project / "conftest.py").write_text(
            "def pytest_collection_modifyitems(items):\n    items.extend(items[:])\n",
            encoding="utf-8",
        )
    result = invoke(project, str(GATE))
    assert result.returncode != 0
    assert not (project / "executed.jsonl").exists()
    assert not (project / "LIVE-RAN").exists()


@pytest.mark.parametrize("change", ["remove", "reorder", "live"])
def test_performance_execution_rechecks_selection_before_running_any_test(
    project: Path, change: str
) -> None:
    manifest = project / "selection.json"
    discovery = invoke(project, str(GATE), "--collect", str(manifest))
    assert discovery.returncode == 0, discovery.stdout + discovery.stderr
    if change == "remove":
        code = "def pytest_collection_modifyitems(items):\n    items.pop()\n"
    elif change == "reorder":
        code = "def pytest_collection_modifyitems(items):\n    items.reverse()\n"
    else:
        code = (
            "import pytest\n\ndef pytest_collection_finish(session):\n"
            "    session.items[0].add_marker(pytest.mark.live)\n"
        )
    (project / "conftest.py").write_text(code, encoding="utf-8")
    execution = invoke(project, str(GATE), "--run", str(manifest))
    assert execution.returncode != 0
    assert "Performance selection" in execution.stdout + execution.stderr
    assert not (project / "executed.jsonl").exists()


def test_successful_pytest_exit_cannot_hide_unexecuted_performance_tests(project: Path) -> None:
    seed_coverage(project)
    (project / "conftest.py").write_text(
        "def pytest_runtestloop(session):\n    return True\n", encoding="utf-8"
    )
    result = invoke(project, str(GATE))
    assert result.returncode == 1
    assert "did not execute exactly once" in result.stderr
    assert not (project / "executed.jsonl").exists()


def test_performance_assertion_failure_propagates_even_with_sufficient_coverage(
    project: Path,
) -> None:
    seed_coverage(project)
    (project / "fail-performance").touch()
    result = invoke(project, str(GATE))
    assert result.returncode == 1
    assert "intentional performance assertion" in result.stdout
    assert "100.00%" in result.stdout


def test_performance_gate_retains_the_configured_coverage_floor(project: Path) -> None:
    result = invoke(project, str(GATE))
    assert result.returncode == 1
    assert "2 passed" in result.stdout
    assert "Required test coverage of 85.0% not reached" in result.stdout


@pytest.mark.parametrize("payload", ["invalid", "[]", '["x", "x"]', '["x", null]'])
def test_performance_execution_rejects_invalid_manifests(project: Path, payload: str) -> None:
    manifest = project / "selection.json"
    manifest.write_text(payload, encoding="utf-8")
    result = invoke(project, str(GATE), "--run", str(manifest))
    assert result.returncode == 1
    assert "Performance gate failed" in result.stderr
    assert not (project / "executed.jsonl").exists()
