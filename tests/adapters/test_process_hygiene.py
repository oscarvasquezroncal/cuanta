from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.adapters.system.shell import descendants, process_table
from cuanta.domain.engine import EngineRequest
from tests.support import FIXTURES

FAKE = FIXTURES / "fake_claude.py"


def _alive(pid: int) -> bool:
    return pid in process_table()


def test_engine_run_kills_orphaned_grandchildren(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "orphan.pid"
    monkeypatch.setenv("CUANTA_CLAUDE_BIN", f'"{sys.executable}" "{FAKE}"')
    request = EngineRequest(
        prompt="=== REQUEST === hello",
        cwd=str(tmp_path),
        env={"FAKE_CLAUDE_ORPHAN": str(marker)},
    )
    started = time.monotonic()
    outcome = ClaudeCodeEngine(SubprocessRunner()).run(request, lambda event: None)
    assert outcome.ok
    assert time.monotonic() - started < 15
    orphan = int(marker.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while _alive(orphan) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(orphan)


def test_cancel_terminates_a_running_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "slow.py"
    script.write_text(
        "import sys, time\nprint('{}', flush=True)\ntime.sleep(60)\n", encoding="utf-8"
    )
    monkeypatch.setenv("CUANTA_CLAUDE_BIN", f'"{sys.executable}" "{script}"')
    engine = ClaudeCodeEngine(SubprocessRunner())
    started = time.monotonic()

    def on_event(_: object) -> None:
        return None

    timer = threading.Timer(1.0, engine.cancel)
    timer.start()
    outcome = engine.run(EngineRequest(prompt="x", cwd=str(tmp_path), env={}), on_event)
    timer.join()
    assert time.monotonic() - started < 20
    assert not outcome.ok
    assert descendants(os.getpid()) == set() or all(
        not _alive(pid) for pid in descendants(os.getpid())
    )
