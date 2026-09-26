from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.opencode import OpenCodeEngine
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.adapters.system.shell import descendants, process_table
from cuanta.domain.engine import BUDGET_LIMIT_SUBTYPE, EngineRequest
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


def test_opencode_step_cap_reaps_an_engine_and_its_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "cost_stream.py"
    marker = tmp_path / "child.pid"
    script.write_text(
        "import json, os, pathlib, signal, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(os.environ['CHILD_PID_FILE']).write_text(str(child.pid))\n"
        "if os.name != 'nt':\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print(json.dumps({'type': 'step_finish', 'sessionID': 's', "
        "'part': {'cost': 0.2, 'tokens': {'input': 2}}}), flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CUANTA_OPENCODE_BIN", f'"{sys.executable}" "{script}"')
    request = EngineRequest(
        prompt="x",
        cwd=str(tmp_path),
        env={"CHILD_PID_FILE": str(marker)},
        max_budget_usd=0.1,
    )
    started = time.monotonic()
    outcome = OpenCodeEngine(SubprocessRunner()).run(request, lambda _: None)
    assert time.monotonic() - started < 20
    assert not outcome.ok
    assert outcome.result is not None
    assert outcome.result.subtype == BUDGET_LIMIT_SUBTYPE
    assert outcome.result.cost_usd == 0.2
    assert outcome.result.models[0].input_tokens == 2
    child = int(marker.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child)
