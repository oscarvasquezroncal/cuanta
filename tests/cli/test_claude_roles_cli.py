from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.domain.config import layer_from_table
from tests.cli.test_engine_guarantees import forge
from tests.cli.test_mandate_from_cli import dry
from tests.fakes import FakeRunner

TEAM = ("mandate", "--type", "refactor", "--what", "Split the cart module", "--profile", "balanced")


def test_a_claude_team_runs_one_launch_per_role_unless_native_is_configured(
    tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    forge(tmp_path)
    monkeypatch.delenv("CUANTA_CLAUDE_TEAM")
    roles = dry(tmp_path, *TEAM)
    assert roles["per_role"] is True and roles["engine"] == "claude"
    monkeypatch.setenv("CUANTA_CLAUDE_TEAM", "native")
    native = dry(tmp_path, *TEAM)
    assert native.get("per_role") is None and native["agents_file"]
    assert not fake_runner.stdins


def test_the_claude_team_mode_is_read_from_the_runs_table() -> None:
    assert layer_from_table({"runs": {"claude_team": "native"}}) == {"claude_team": "native"}
    assert layer_from_table({"runs": {"claude_team": "subagents"}}) == {}
