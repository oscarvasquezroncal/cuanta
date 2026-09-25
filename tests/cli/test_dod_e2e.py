from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.bootstrap import Container
from cuanta.domain.graph_policy import GRAPHLESS_OVERRIDE, graphless_prompt
from cuanta.domain.mandate import REQUEST_MARKER, extract_block
from cuanta.ports.ledger import EventQuery
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner, copy_repo
from tests.support import FIXTURES, invoke

FAKE = FIXTURES / "fake_claude.py"
pytestmark = pytest.mark.xdist_group("local-listener")
TEMPLATE = (
    Path(__file__).parents[2]
    / "src/cuanta/assets/forge/skills/agent-system-init/templates/MANDATE_TEMPLATE.template.md"
)


def fake_bin() -> str:
    return f'"{sys.executable}" "{FAKE}"'


@pytest.fixture(params=[True, False], ids=["graph-cli", "graph-none"])
def graph_available(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


@pytest.fixture
def env(
    monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner, graph_available: bool
) -> dict[str, str]:
    original = Container.for_project
    runner = SubprocessRunner()
    original_run = runner.run

    def which(name: str) -> str | None:
        return "/fixture/graphify" if name == "graphify" and graph_available else None

    def run(
        args: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Completed:
        if args and args[0] == "graphify":
            assert graph_available
            assert list(args) == ["graphify", "--help"]
            return Completed(0, "Usage: graphify", "")
        return original_run(args, cwd=cwd, env=env, timeout=timeout)

    monkeypatch.setattr(runner, "which", which)
    monkeypatch.setattr(runner, "run", run)

    def build(cls: type[Container], project: Path) -> Container:
        container = original(project)
        container.runner = runner
        return container

    monkeypatch.setattr(Container, "for_project", classmethod(build))
    values = {
        "CUANTA_CLAUDE_BIN": fake_bin(),
        "CUANTA_PORT": "47400",
        "FAKE_FORGE_TEMPLATE": str(TEMPLATE),
        "FAKE_CLAUDE_FIX": "src/calc/__init__.py|left - right|left + right",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def _configure(root: Path) -> None:
    command = f"'{sys.executable}' -m pytest -p no:cacheprovider".replace("\\", "/")
    config = root / ".cuanta" / "config.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(f'[test]\nrunner = "pytest"\ncommand = "{command}"\n', encoding="utf-8")


def _init(root: Path, env: dict[str, str]) -> None:
    result = invoke(["init", str(root), "--yes", "--json"], env=env)
    assert result.exit_code == 0, result.stdout + result.stderr


def test_init_test_pounce_spectrum(
    tmp_path: Path, env: dict[str, str], graph_available: bool
) -> None:
    root = copy_repo("bugfix", tmp_path)
    project = ["--project", str(root)]
    init = invoke(["init", str(root), "--yes", "--json"], env=env)
    assert init.exit_code == 0, init.stdout
    _configure(root)
    red = invoke(["test", "--json", *project], env=env)
    assert red.exit_code == 1
    assert json.loads(red.stdout)["status"] == "red"
    dry = invoke(
        ["pounce", "--from-failure", "--out-of-scope", "tests", "--dry-run", "--json", *project],
        env=env,
    )
    assert dry.exit_code == 0, dry.stdout
    composed = json.loads(dry.stdout)
    block = extract_block((root / "docs" / "MANDATE_TEMPLATE.md").read_text(encoding="utf-8"))
    prefix = block[: block.index(REQUEST_MARKER)]
    if not graph_available:
        prefix = graphless_prompt(prefix)
        assert "graphify" not in composed["prompt"].lower()
    assert composed["prompt"].startswith(prefix)
    assert "cuanta cat cap:" in composed["prompt"]
    assert composed["command"][composed["command"].index("--allowedTools") + 1].startswith(
        "Read,Grep"
    )
    pounce = invoke(
        [
            "pounce",
            "--from-failure",
            "--out-of-scope",
            "tests",
            "--hu",
            "hu-001",
            "--json",
            *project,
        ],
        env=env,
    )
    assert pounce.exit_code == 0, pounce.stdout + pounce.stderr
    run = json.loads(pounce.stdout)
    assert run["changed_files"] == ["src/calc/__init__.py"]
    assert run["hu"] == "HU-001"
    folder = root / ".cuanta" / "runs" / run["run_id"]
    assert (folder / "run.json").is_file()
    assert (folder / "report.md").is_file()
    green = invoke(["test", "--json", *project], env=env)
    assert json.loads(green.stdout)["status"] == "green"
    spectrum = invoke(["spectrum", run["run_id"], "--json", *project], env=env)
    assert spectrum.exit_code == 0, spectrum.stdout
    report = json.loads(spectrum.stdout)
    assert report["totals"]["total"] > 0
    assert report["source"] == "telemetry"
    hu = json.loads(invoke(["spectrum", "HU-001", "--json", *project], env=env).stdout)
    assert hu["runs"] == [run["run_id"]]
    ledger = SqliteLedger(root / ".cuanta" / "ledger.db")
    try:
        decisions = ledger.decisions(run_id="")
        assert decisions
        assert decisions[0].backend == "heuristic"
        assert decisions[0].outcome == "ok"
        assert ledger.snapshots(run["run_id"], "start")
        assert ledger.events(EventQuery(run_id=run["run_id"]))
    finally:
        ledger.close()


def test_mandate_requires_template_and_fields(tmp_path: Path, env: dict[str, str]) -> None:
    root = copy_repo("bugfix", tmp_path)
    missing_template = invoke(
        [
            "mandate",
            "--type",
            "bug",
            "--what",
            "x",
            "--why",
            "y",
            "--out-of-scope",
            "z",
            "--json",
            "--project",
            str(root),
        ]
    )
    assert missing_template.exit_code == 1
    assert "cuanta init" in json.loads(missing_template.stdout)["error"]["hint"]
    (root / "docs").mkdir()
    (root / "docs" / "MANDATE_TEMPLATE.md").write_text(
        TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    no_scope = invoke(
        ["mandate", "--type", "bug", "--what", "x", "--why", "y", "--json", "--project", str(root)]
    )
    assert no_scope.exit_code == 1
    assert "--out-of-scope" in json.loads(no_scope.stdout)["error"]["message"]
    bad_type = invoke(
        [
            "mandate",
            "--type",
            "chore",
            "--what",
            "x",
            "--why",
            "y",
            "--out-of-scope",
            "z",
            "--json",
            "--project",
            str(root),
        ]
    )
    assert bad_type.exit_code == 1


def test_from_failure_needs_red_result(tmp_path: Path, env: dict[str, str]) -> None:
    root = copy_repo("bugfix", tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "MANDATE_TEMPLATE.md").write_text(
        TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = invoke(
        ["pounce", "--from-failure", "--out-of-scope", "z", "--json", "--project", str(root)]
    )
    assert result.exit_code == 1
    assert "cuanta test" in json.loads(result.stdout)["error"]["hint"]


def test_a_200_kb_evidence_file_launches_through_stdin_and_a_capsule(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_repo("bugfix", tmp_path)
    project = ["--project", str(root)]
    _init(root, env)
    log = tmp_path / "build.log"
    lines = [
        f"line {index:06d} ERROR something failed in module_{index % 97}" for index in range(4_500)
    ]
    log.write_text("\n".join(lines), encoding="utf-8")
    assert log.stat().st_size > 200_000
    captured = tmp_path / "prompt.txt"
    monkeypatch.setenv("FAKE_CLAUDE_PROMPT_OUT", str(captured))
    pounce = invoke(
        [
            "pounce",
            "--type",
            "bug",
            "--what",
            "fix the failing build",
            "--evidence",
            str(log),
            "--out-of-scope",
            "tests",
            "--json",
            *project,
        ],
        env={**env, "FAKE_CLAUDE_PROMPT_OUT": str(captured)},
    )
    assert pounce.exit_code == 0, pounce.stdout + pounce.stderr
    prompt = captured.read_text(encoding="utf-8")
    assert len(prompt) < 30_000
    assert lines[0] in prompt
    assert lines[-1] in prompt
    assert "cuanta cat cap:" in prompt
    capsules = list((root / ".cuanta" / "capsules").glob("*.log"))
    assert any(path.stat().st_size > 200_000 for path in capsules)


def _pounce(root: Path, env: dict[str, str], *extra: str) -> dict[str, Any]:
    result = invoke(
        [
            "pounce",
            "--type",
            "bug",
            "--what",
            "fix the subtraction in calc",
            "--why",
            "add(2, 1) returns 1",
            "--out-of-scope",
            "tests",
            "--json",
            "--project",
            str(root),
            *extra,
        ],
        env=env,
    )
    assert result.exit_code in {0, 1}, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    return document


def test_routed_mandate_passes_agents_by_file_and_audits_each_agent(
    tmp_path: Path,
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    graph_available: bool,
) -> None:
    root = copy_repo("bugfix", tmp_path)
    _init(root, env)
    sent = tmp_path / "agents.json"
    monkeypatch.setenv("FAKE_CLAUDE_AGENTS_OUT", str(sent))
    run = _pounce(root, {**env, "FAKE_CLAUDE_AGENTS_OUT": str(sent)})
    agents = json.loads(sent.read_text(encoding="utf-8"))
    assert set(agents) == {"architecture-analyst", "python-senior", "tester", "docs-updater"}
    installed = (root / ".claude" / "agents" / "tester.md").read_text(encoding="utf-8")
    tester_prompt = agents["tester"]["prompt"]
    if not graph_available:
        assert tester_prompt.startswith(GRAPHLESS_OVERRIDE + "\n\n")
        tester_prompt = tester_prompt.removeprefix(GRAPHLESS_OVERRIDE + "\n\n")
    assert tester_prompt in installed
    assert agents["python-senior"]["model"] == "claude-opus-5-5"
    assert agents["docs-updater"]["model"] == "claude-haiku-4-5"
    audit = {row["agent"]: row for row in run["audit"]}
    assert set(audit) == {*agents, "main"}
    assert all(row["status"] == "match" for row in audit.values()), audit
    spectrum = json.loads(
        invoke(["spectrum", str(run["run_id"]), "--json", "--project", str(root)], env=env).stdout
    )
    assert {row["agent"] for row in spectrum["audit"]} == set(audit)


def test_audit_explains_a_model_that_did_not_apply(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_repo("bugfix", tmp_path)
    _init(root, env)
    monkeypatch.setenv("FAKE_CLAUDE_AGENT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "haiku")
    run = _pounce(root, {**env, "FAKE_CLAUDE_AGENT_MODEL": "claude-haiku-4-5"})
    senior = next(row for row in run["audit"] if row["agent"] == "python-senior")
    assert senior["status"] == "mismatch"
    assert "CLAUDE_CODE_SUBAGENT_MODEL is set" in senior["cause"]


def test_routing_off_sends_no_agents(tmp_path: Path, env: dict[str, str]) -> None:
    root = copy_repo("bugfix", tmp_path)
    _init(root, env)
    dry = invoke(
        [
            "pounce",
            "--type",
            "bug",
            "--what",
            "fix",
            "--why",
            "x",
            "--out-of-scope",
            "tests",
            "--route",
            "off",
            "--dry-run",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    document = json.loads(dry.stdout)
    assert document["agents_file"] is None
    assert "--agents" not in document["command"]
    routed = json.loads(
        invoke(
            [
                "pounce",
                "--type",
                "bug",
                "--what",
                "fix",
                "--why",
                "x",
                "--out-of-scope",
                "tests",
                "--preset",
                "save",
                "--dry-run",
                "--json",
                "--project",
                str(root),
            ],
            env=env,
        ).stdout
    )
    assert "--agents" in routed["command"]
    assert any("senior" in line for line in routed["team"])


def test_depth_caps_the_spend_and_states_a_read_budget(
    tmp_path: Path, env: dict[str, str], graph_available: bool
) -> None:
    root = copy_repo("bugfix", tmp_path)
    _init(root, env)
    dry = invoke(
        [
            "pounce",
            "--type",
            "investigation",
            "--what",
            "how does add work",
            "--why",
            "curious",
            "--out-of-scope",
            "everything",
            "--depth",
            "quick",
            "--dry-run",
            "--json",
            "--project",
            str(root),
        ],
        env=env,
    )
    document = json.loads(dry.stdout)
    command = " ".join(document["command"])
    prompt = document["prompt"]
    assert "--max-budget-usd" in command
    assert "0.25" in command
    assert "--append-system-prompt" in command
    assert "READ BUDGET (quick)" in command
    assert "READ BUDGET" not in prompt
    assert "--effort low" in command
    assert "Agent,Task" in command
    assert "--agents" not in command
    assert "ONCE" not in prompt
    if graph_available:
        assert "You are architecture-analyst." in command
        assert "You are the codebase analyst for this repository" not in command
    else:
        assert "You are the codebase analyst for this repository" in command
        assert "You are architecture-analyst." not in command
        assert "graphify" not in command.lower()
    assert "Deliverable: a written report" in prompt
    assert "regression fixture" not in prompt
    bad = invoke(["pounce", "--depth", "huge", "--dry-run", "--project", str(root)], env=env)
    assert bad.exit_code != 0
    shaped = invoke(["pounce", "--shape", "twisted", "--dry-run", "--project", str(root)], env=env)
    assert shaped.exit_code != 0


def _launch_files(root: Path) -> dict[str, str]:
    folder = root / ".cuanta" / "tmp"
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.glob("*.json"))
    }


def test_two_dry_runs_produce_byte_identical_launch_files(
    tmp_path: Path, env: dict[str, str]
) -> None:
    root = copy_repo("bugfix", tmp_path)
    _init(root, env)
    argv = [
        "pounce",
        "--type",
        "bug",
        "--what",
        "fix add",
        "--why",
        "it is wrong",
        "--out-of-scope",
        "docs",
        "--preset",
        "save",
        "--depth",
        "quick",
        "--dry-run",
        "--json",
        "--project",
        str(root),
    ]
    first = json.loads(invoke(argv, env=env).stdout)
    before = _launch_files(root)
    for path in (root / ".cuanta" / "tmp").glob("*.json"):
        path.unlink()
    second = json.loads(invoke(argv, env=env).stdout)
    after = _launch_files(root)
    assert first["command"] == second["command"]
    assert first["prompt"] == second["prompt"]
    assert before == after
    assert any(name.startswith("agents-") for name in after)
    assert any(name.startswith("lean-settings-") for name in after)
    assert any(name.startswith("lean-mcp-") for name in after)
