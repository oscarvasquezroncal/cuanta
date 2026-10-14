from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.telemetry import listener_control
from cuanta.bootstrap import Container
from tests.cli.test_engine_guarantees import codex_ready, cross_args
from tests.fakes import FakeRunner
from tests.support import invoke


def test_a_locked_listener_record_leaves_the_team_run_unchanged(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_ready(fake_runner)
    monkeypatch.setattr(Container, "build_blocked", lambda self: frozenset({"codex"}))
    monkeypatch.setattr(listener_control, "UNLINK_PAUSE_S", 0.0)
    locked: list[str] = []
    unlink = Path.unlink

    def refuse(path: Path, missing_ok: bool = False) -> None:
        if path.name.startswith("listener-"):
            locked.append(path.name)
            raise PermissionError(13, "in use by another process", str(path))
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    result = invoke(
        cross_args(tmp_path, "--engine", "codex", "--route", "fixed", "--docs", "on", "--plain")
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    launches = [call for call in fake_runner.calls if call[:2] == ("codex", "exec")]
    assert len(launches) == 4
    left = sorted(path.name for path in (tmp_path / ".cuanta").glob("listener-*.json"))
    assert len(left) == 4 and set(locked) == set(left)
    assert not (tmp_path / ".cuanta" / "listener.json").exists()
    listed = json.loads(invoke(["runs", "list", "--json", "--project", str(tmp_path)]).stdout)
    assert {f"listener-{run['id']}.json" for run in listed["runs"]} >= set(left)
