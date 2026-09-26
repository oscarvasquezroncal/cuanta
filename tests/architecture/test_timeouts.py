from __future__ import annotations

import faulthandler
import importlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests import timeouts
from tests.timeouts import (
    LONG_S,
    STDERR,
    pytest_timeout_cancel_timer,
    pytest_timeout_set_timer,
    remember_stderr,
    unbounded_live,
)

ROOT = Path(__file__).resolve().parents[2]
LONG_TESTS = (
    ("tests.cli.test_bench_cli", "test_mini_bench_completes_with_the_fake_engine"),
    ("tests.cli.test_ui_web_e2e", "test_ui_web_serves_the_first_frame"),
)
INHERITED = ("PYTEST_", "COV_CORE_", "COVERAGE_")
HUNG_CONFTEST = (
    "from tests.timeouts import pytest_timeout_cancel_timer as pytest_timeout_cancel_timer\n"
    "from tests.timeouts import pytest_timeout_set_timer as pytest_timeout_set_timer\n"
    "from tests.timeouts import remember_stderr\n"
    "\n"
    "\n"
    "def pytest_configure() -> None:\n"
    "    remember_stderr()\n"
)
HUNG_TEST = "import time\n\n\ndef test_hangs() -> None:\n    time.sleep(60)\n"


@dataclass(frozen=True)
class Settings:
    timeout: float
    method: str
    disable_debugger_detection: bool = False


@dataclass
class FakeItem:
    marks: list[pytest.Mark]

    def get_closest_marker(self, name: str) -> pytest.Mark | None:
        return next((mark for mark in reversed(self.marks) if mark.name == name), None)

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.marks.append(marker.mark)


def test_pyproject_sets_a_per_test_timeout_and_stops_on_crashed_workers() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = project["tool"]["pytest"]["ini_options"]
    assert options["timeout"] == 120
    assert "--max-worker-restart=0" in shlex.split(options["addopts"])
    assert "timeout_method" not in options
    assert "faulthandler_timeout" not in options
    dev = project["dependency-groups"]["dev"]
    assert any(requirement.startswith("pytest-timeout") for requirement in dev)


def test_the_timeout_plugin_is_active(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.pluginmanager.hasplugin("timeout")
    assert pytestconfig.getini("timeout") == "120"


def test_redirected_stderr_keeps_a_file_for_timeout_stacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryFile(mode="w+") as original:
        remembered: list[int] = []
        monkeypatch.setattr(timeouts, "STDERR", remembered)
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        monkeypatch.setattr(sys, "__stderr__", original)
        remember_stderr()
        assert len(remembered) == 1
        try:
            os.write(remembered[0], b"trace")
            original.seek(0)
            assert original.read() == "trace"
        finally:
            os.close(remembered[0])


def test_missing_stderr_file_defers_to_the_timeout_plugin(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(timeouts, "STDERR", [])
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(sys, "__stderr__", None)
    remember_stderr()
    assert timeouts.STDERR == []
    assert pytest_timeout_set_timer(request.node, Settings(5.0, "thread")) is None


@pytest.mark.parametrize(("module", "name"), LONG_TESTS)
def test_long_tests_declare_the_long_limit(module: str, name: str) -> None:
    test = getattr(importlib.import_module(module), name)
    assert pytest.mark.timeout(LONG_S).mark in getattr(test, "pytestmark", [])


def test_live_tests_run_without_a_timeout() -> None:
    live = FakeItem([pytest.mark.live.mark])
    pinned = FakeItem([pytest.mark.live.mark, pytest.mark.timeout(30).mark])
    offline = FakeItem([pytest.mark.perf.mark])
    unbounded_live([live, pinned, offline])
    assert live.marks == [pytest.mark.live.mark, pytest.mark.timeout(0).mark]
    assert pinned.marks == [pytest.mark.live.mark, pytest.mark.timeout(30).mark]
    assert offline.marks == [pytest.mark.perf.mark]


def test_thread_timeouts_dump_stacks_through_faulthandler_and_exit(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    armed: list[tuple[float, dict[str, object]]] = []
    cancelled: list[bool] = []

    def arm(timeout: float, **options: object) -> None:
        armed.append((timeout, options))

    def cancel() -> None:
        cancelled.append(True)

    monkeypatch.setattr(faulthandler, "dump_traceback_later", arm)
    monkeypatch.setattr(faulthandler, "cancel_dump_traceback_later", cancel)
    item = request.node
    assert pytest_timeout_set_timer(item, Settings(5.0, "thread")) is True
    assert armed == [(5.0, {"exit": True, "file": STDERR[0]})]
    assert pytest_timeout_set_timer(item, Settings(5.0, "signal")) is None
    assert len(armed) == 1
    assert pytest_timeout_cancel_timer(item) is None
    assert cancelled == [True]


def test_debuggers_keep_the_plugin_timer(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    armed: list[float] = []

    def arm(timeout: float, **options: object) -> None:
        armed.append(timeout)

    plugin: Any = request.config.pluginmanager.getplugin("timeout")
    monkeypatch.setattr(faulthandler, "dump_traceback_later", arm)
    monkeypatch.setattr(plugin, "is_debugging", lambda: True)
    assert pytest_timeout_set_timer(request.node, Settings(5.0, "thread")) is None
    assert armed == []
    assert pytest_timeout_set_timer(request.node, Settings(5.0, "thread", True)) is True
    assert armed == [5.0]


@pytest.mark.timeout(LONG_S)
def test_a_hung_test_fails_fast_with_its_stack_under_xdist(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(HUNG_CONFTEST, encoding="utf-8")
    (tmp_path / "test_hang.py").write_text(HUNG_TEST, encoding="utf-8")
    environ = {key: value for key, value in os.environ.items() if not key.startswith(INHERITED)}
    environ["PYTHONPATH"] = str(ROOT)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-n",
        "2",
        "--dist=loadgroup",
        "--max-worker-restart=0",
        "--timeout=2",
        "--timeout_method=thread",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:cov",
        "-rA",
    ]
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=tmp_path,
        env=environ,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    elapsed = time.monotonic() - started
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    assert "crashed while running 'test_hang.py::test_hangs'" in completed.stdout, output
    assert "in test_hangs" in output, output
    assert elapsed < 60, output
