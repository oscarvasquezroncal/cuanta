from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.bootstrap import Container
from cuanta.ports.ledger import EventQuery
from tests.fakes import FakeRunner, copy_repo
from tests.support import FIXTURES, invoke

FAKE = FIXTURES / "fake_claude.py"


def fake_bin() -> str:
    return f'"{sys.executable}" "{FAKE}"'


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner) -> None:
    monkeypatch.setenv("CUANTA_CLAUDE_BIN", fake_bin())
    monkeypatch.setenv("CUANTA_PORT", "47300")
    original = Container.for_project

    def build(cls: type[Container], project: Path) -> Container:
        container = original(project)
        container.runner = SubprocessRunner()
        return container

    monkeypatch.setattr(Container, "for_project", classmethod(build))


def test_init_completes_every_stage_with_fake_engine(tmp_path: Path, fake_claude: None) -> None:
    root = copy_repo("python_strong", tmp_path)
    (root / ".git").mkdir()
    result = invoke(
        ["init", str(root), "--yes", "--json"],
        env={"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47300"},
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    statuses = {stage["stage"]: stage["status"] for stage in document["stages"]}
    assert statuses == {
        "detect": "ok",
        "graph": "ok",
        "telemetry": "ok",
        "forge": "ok",
        "verify": "ok",
    }
    assert document["ok"] is True
    run_id = document["run_id"]
    assert (root / ".claude" / "skills" / "agent-system-init" / "SKILL.md").is_file()
    assert (root / ".claude" / "commands" / "init-agents.md").is_file()
    assert (root / ".claude" / "agents" / "python-senior.md").is_file()
    assert ".claude/forge-state.json" in (root / ".gitignore").read_text(encoding="utf-8")
    settings = json.loads((root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert settings["env"]["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json"
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    try:
        run = ledger.get_run(run_id)
        assert run is not None
        assert (run.kind, run.status, run.cost_usd) == ("init", "ok", 0.42)
        events = ledger.events(EventQuery(run_id=run_id))
        kinds = {event.kind for event in events}
        assert {"api_request", "tool_result", "result_usage"} <= kinds
        assert ledger.baselines(run_id)
    finally:
        ledger.close()
    assert os.environ.get("CUANTA_RUN_ID") is None


def test_second_init_routes_to_refresh(tmp_path: Path, fake_claude: None) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47310"}
    assert (
        invoke(["init", str(root), "--yes", "--json", "--skip-telemetry"], env=env).exit_code == 0
    )
    second = invoke(["init", str(root), "--yes", "--plain", "--skip-telemetry"], env=env)
    assert second.exit_code == 0, second.stdout
    assert "running refresh semantics" in second.stdout


def test_init_without_claude_skips_forge(tmp_path: Path, fake_runner: FakeRunner) -> None:
    root = copy_repo("python_strong", tmp_path)
    result = invoke(
        ["init", str(root), "--json", "--skip-telemetry"],
        env={"CUANTA_CLAUDE_BIN": str(tmp_path / "missing-claude")},
    )
    document = json.loads(result.stdout)
    statuses = {stage["stage"]: stage["status"] for stage in document["stages"]}
    assert statuses["forge"] == "skip"
    assert statuses["verify"] == "skip"


def test_failed_engine_run_is_resumable(tmp_path: Path, fake_claude: None) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "FAKE_CLAUDE_EXIT": "1"}
    failed = invoke(["init", str(root), "--json", "--skip-telemetry"], env=env)
    assert failed.exit_code == 1
    state = json.loads((root / ".cuanta" / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] == ["detect", "graph", "telemetry"]
    resumed = invoke(
        ["init", str(root), "--json", "--skip-telemetry"], env={"CUANTA_CLAUDE_BIN": fake_bin()}
    )
    assert resumed.exit_code == 0, resumed.stdout
    assert json.loads(resumed.stdout)["resumed_from"] == "forge"


@pytest.mark.live
def test_live_claude_version(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    assert runner.which("claude") is not None
    from cuanta.adapters.engines.claude_code import ClaudeCodeEngine

    assert ClaudeCodeEngine(runner).missing_flags() == ()


def test_verify_fails_when_forge_skips_the_gateway_instructions(
    tmp_path: Path, fake_claude: None
) -> None:
    root = copy_repo("python_strong", tmp_path)
    result = invoke(
        ["init", str(root), "--yes", "--json", "--skip-telemetry"],
        env={
            "CUANTA_CLAUDE_BIN": fake_bin(),
            "CUANTA_PORT": "47300",
            "FAKE_FORGE_NO_GATEWAY": "1",
        },
    )
    assert result.exit_code == 1, result.stdout + result.stderr
    document = json.loads(result.stdout)
    failing = [item["text"] for item in document["verify"] if item["status"] == "fail"]
    assert failing == [
        "docs/MANDATE_TEMPLATE.md lacks the gateway instructions: "
        "cuanta test --json, cuanta cat <capsule> --level L2",
        ".claude/agents/tester.md lacks the gateway instructions: "
        "cuanta test --json, cuanta cat <capsule> --level L2",
    ]
    assert document["ok"] is False
