from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.engines.codex import CodexEngine
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, FakeStream
from tests.support import invoke


def mandate_args(root: Path, engine: str) -> list[str]:
    return [
        "mandate",
        "--simple",
        "--engine",
        engine,
        "--type",
        "investigation",
        "--what",
        "Explain the fixture",
        "--why",
        "How does it work?",
        "--out-of-scope",
        "No edits",
        "--project",
        str(root),
    ]


def test_cli_refuses_opencode_investigations_without_spawning(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = invoke([*mandate_args(tmp_path, "opencode"), "--json"])
    assert result.exit_code != 0
    assert "graphify" in result.stdout
    assert "cannot enforce read-only" in result.stdout
    assert not fake_runner.stdins


def test_codex_preview_shows_enforcement_and_explicit_sandbox(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    result = invoke([*mandate_args(tmp_path, "codex"), "--dry-run", "--json"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    command = data["command"]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" not in command
    assert any("Spend cap: checked after the run" in line for line in data["team"])
    assert any("Turn limit: not available" in line for line in data["team"])
    assert not fake_runner.stdins


def test_codex_launch_warns_before_the_result_and_labels_estimated_cost(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    fake_runner.binaries["codex"] = "/fixture/codex"
    fake_runner.responses["codex exec --help"] = Completed(
        0, " ".join(CodexEngine.required_tokens), ""
    )
    fake_runner.streams["codex exec"] = FakeStream(
        [
            '{"type":"thread.started","thread_id":"fixture"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"# Report"}}',
            '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":0,"output_tokens":100}}',
        ]
    )
    result = invoke(
        [*mandate_args(tmp_path, "codex"), "--model", "gpt-6-luna", "--max-budget-usd", "0.1"]
    )
    assert result.exit_code == 0, result.stdout
    assert "cannot enforce the spend cap" in result.stdout
    assert "(estimated)" in result.stdout
    assert result.stdout.index("cannot enforce") < result.stdout.index("# Report")


@pytest.mark.parametrize(
    ("flags", "configured", "expected"),
    [
        ((), 0, 40),
        (("--depth", "quick"), 0, 20),
        (("--depth", "deep"), 0, 80),
        (("--depth", "quick"), 13, 13),
        (("--depth", "deep", "--max-turns", "1"), 13, 1),
    ],
)
def test_cross_cli_passes_resolved_turn_limit_to_every_claude_role(
    tmp_path: Path,
    fake_runner: FakeRunner,
    flags: tuple[str, ...],
    configured: int,
    expected: int,
) -> None:
    fake_runner.streams["claude -p"] = FakeStream(
        ['{"type":"result","subtype":"success","total_cost_usd":0.001,"is_error":false}']
    )
    result = invoke(
        [
            "mandate",
            "--cross-engine",
            "--route",
            "fixed",
            "--type",
            "bug",
            "--what",
            "Fix incorrect addition",
            "--why",
            "The sum is wrong",
            "--out-of-scope",
            "Documentation",
            "--project",
            str(tmp_path),
            *flags,
        ],
        env={"CUANTA_MAX_TURNS": str(configured)},
    )
    assert result.exit_code == 0, result.stdout
    launches = [call for call in fake_runner.calls if call[:2] == ("claude", "-p")]
    assert len(launches) >= 3
    assert all(call[call.index("--max-turns") + 1] == str(expected) for call in launches)
    assert "Read-only: not available" in result.stdout
    assert "Turn limit: enforced" in result.stdout
