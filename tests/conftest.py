from __future__ import annotations

import gc
import os
import signal
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis.configuration import set_hypothesis_home_dir

from cuanta.adapters.system.shell import descendants
from tests import timeouts
from tests.timeouts import remember_stderr, unbounded_live

SETTLE_S = 3.0
pytest_enter_pdb = timeouts.pytest_enter_pdb
pytest_timeout_cancel_timer = timeouts.pytest_timeout_cancel_timer
pytest_timeout_set_timer = timeouts.pytest_timeout_set_timer
set_hypothesis_home_dir(
    os.environ.get("HYPOTHESIS_STORAGE_DIRECTORY")
    or Path(tempfile.gettempdir()) / "cuanta-hypothesis"
)


def pytest_configure(config: pytest.Config) -> None:
    remember_stderr()
    if config.getoption("--snapshot-report") == "snapshot_report.html":
        config.option.snapshot_report = str(
            Path(tempfile.gettempdir()) / f"cuanta-snapshot-report-{os.getpid()}.html"
        )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    unbounded_live(items)


@pytest.fixture(autouse=True)
def isolated_user_dirs(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("CUANTA_CONFIG_DIR", str(home / "config"))
    monkeypatch.setenv("CUANTA_HOME", str(home))
    for variable in (
        "CUANTA_THEME",
        "CUANTA_ENGINE",
        "CUANTA_RUN_ID",
        "CUANTA_INSTINCT",
        "CUANTA_MAX_TURNS",
        "NO_COLOR",
        "COLORFGBG",
        "TYPESAFE_BASE_URL",
        "TYPESAFE_API_BASE",
        "TYPESAFE_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
    return home


def _settle(before: set[int]) -> set[int]:
    deadline = time.monotonic() + SETTLE_S
    leftover = descendants(os.getpid()) - before
    while leftover and time.monotonic() < deadline:
        time.sleep(0.1)
        leftover = descendants(os.getpid()) - before
    return leftover


@pytest.fixture(autouse=True)
def no_orphan_processes() -> Iterator[None]:
    before = descendants(os.getpid())
    yield
    gc.collect()
    leftover = _settle(before)
    for pid in leftover:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    assert leftover == set(), f"child processes outlived the test: {sorted(leftover)}"
