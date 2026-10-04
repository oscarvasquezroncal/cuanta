from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

from cuanta.adapters.storage.sqlite_ledger import SqliteLedger
from cuanta.adapters.system.process_runner import SubprocessRunner
from cuanta.bootstrap import Container
from cuanta.ports.ledger import EventQuery
from tests.fakes import FakeRunner, copy_repo
from tests.real_run import forged_project
from tests.support import FIXTURES, invoke

FAKE = FIXTURES / "fake_claude.py"
VENDORED_INIT_AGENTS = (
    Path(__file__).parents[2]
    / "src"
    / "cuanta"
    / "assets"
    / "forge"
    / "commands"
    / "init-agents.md"
)
GAPS = "cuanta test --json, cuanta cat <capsule> --level L2"
GATEWAY = (
    "Run the suite with `cuanta test --json`; its output is bounded, never pipe it. "
    "Read failure detail with `cuanta cat <capsule> --level L2`.\n"
)
TUNED_TESTER = "---\nname: tester\ndescription: tuned\n---\nRun pytest by hand.\n"
TUNED_SENIOR = "---\nname: python-senior\n---\nmine\n"
SUGGESTED = ".cuanta/forge-suggested/claude"
ADOPT_NOTE = (
    "Your files were not changed. To adopt a suggestion, copy it over your file, or compare "
    "it in the app's Init view and choose Use new; delete .cuanta/forge-suggested/ to "
    "discard them."
)


def fake_bin() -> str:
    return f'"{sys.executable}" "{FAKE}"'


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner) -> None:
    monkeypatch.setenv("CUANTA_CLAUDE_BIN", fake_bin())
    monkeypatch.setenv("CUANTA_PORT", "47300")
    original = Container.for_project

    def build(cls: type[Container], project: Path, verbose: bool = False) -> Container:
        container = original(project, verbose)
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


def test_second_init_keeps_forge_and_refresh_forge_routes_to_refresh(
    tmp_path: Path, fake_claude: None
) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47310"}
    assert (
        invoke(["init", str(root), "--yes", "--json", "--skip-telemetry"], env=env).exit_code == 0
    )
    second = invoke(["init", str(root), "--yes", "--plain", "--skip-telemetry"], env=env)
    assert second.exit_code == 0, second.stdout
    assert "kept (cuanta init --refresh-forge runs Forge again)" in second.stdout
    assert "running refresh semantics" not in second.stdout
    third = invoke(
        ["init", str(root), "--yes", "--plain", "--skip-telemetry", "--refresh-forge"], env=env
    )
    assert third.exit_code == 0, third.stdout
    assert "running refresh semantics" in third.stdout


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


def test_missing_gateway_instructions_warn_and_init_completes(
    tmp_path: Path, fake_claude: None
) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47300", "FAKE_FORGE_NO_GATEWAY": "1"}
    command = ["init", str(root), "--yes", "--skip-telemetry"]
    result = invoke([*command, "--json"], env=env)
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["ok"] is True
    assert document["next"] == "cuanta test"
    assert [item for item in document["verify"] if item["status"] == "fail"] == []
    warned = [
        (item["text"], item["fix"]) for item in document["verify"] if item["status"] == "warn"
    ]
    assert warned == [
        (
            f"{path} lacks the gateway instructions: {GAPS}",
            f"add {GAPS} to the test step in {path}",
        )
        for path in ("docs/MANDATE_TEMPLATE.md", ".claude/agents/tester.md")
    ]
    statuses = {stage["stage"]: stage["status"] for stage in document["stages"]}
    assert statuses["verify"] == "warn"
    state = json.loads((root / ".cuanta" / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] == ["detect", "graph", "telemetry", "forge", "verify"]
    plain = invoke(["--plain", *command], env=env)
    assert plain.exit_code == 0, plain.stdout
    assert "finished with problems" not in plain.stdout
    assert "next: cuanta doctor" not in plain.stdout
    lines = plain.stdout.splitlines()
    assert f"! .claude/agents/tester.md lacks the gateway instructions: {GAPS}" in lines
    assert f"> fix: add {GAPS} to the test step in .claude/agents/tester.md" in lines
    assert [line for line in lines if re.fullmatch(r"\S+ init complete · \d[\d,]* s", line)]
    assert "! 2 warnings above, each with its fix" in lines
    assert "> next: cuanta test" in lines


def _staged(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / ".cuanta" / "forge-out" / "claude" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_each_suggestion_is_listed_once_with_its_summary(tmp_path: Path, fake_claude: None) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47300"}
    command = ["init", str(root), "--yes", "--skip-telemetry"]
    assert invoke([*command, "--json"], env=env).exit_code == 0
    agents = root / ".claude" / "agents"
    (agents / "tester.md").write_text(TUNED_TESTER, encoding="utf-8")
    (agents / "python-senior.md").write_text(TUNED_SENIOR, encoding="utf-8")
    (root / ".claude" / "commands" / "init-agents.md").write_text("mine\n", encoding="utf-8")
    _staged(
        root,
        {
            "agents/tester.md": TUNED_TESTER + GATEWAY,
            "agents/python-senior.md": "---\nname: python-senior\n---\nforge one\nforge two\n",
        },
    )
    result = invoke(["--plain", *command, "--refresh-forge"], env=env)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert [path for path in (root / ".claude").rglob("*") if ".new" in path.name] == []
    suggested = root / ".cuanta" / "forge-suggested" / "claude"
    assert (suggested / "agents" / "tester.md").read_text(
        encoding="utf-8"
    ) == TUNED_TESTER + GATEWAY
    assert (agents / "tester.md").read_text(encoding="utf-8") == TUNED_TESTER
    assert (agents / "python-senior.md").read_text(encoding="utf-8") == TUNED_SENIOR
    command_lines = len(VENDORED_INIT_AGENTS.read_text(encoding="utf-8").splitlines())
    expected = [
        f"- .claude/agents/python-senior.md: Forge's version is in {SUGGESTED}/agents/"
        "python-senior.md (+2/-1 lines)",
        f"- .claude/agents/tester.md: Forge's version is in {SUGGESTED}/agents/tester.md "
        "(+1/-0 lines)",
        f"- .claude/commands/init-agents.md: Forge's version is in {SUGGESTED}/commands/"
        f"init-agents.md (+{command_lines}/-1 lines)",
    ]
    lines = result.stdout.splitlines()
    assert [line for line in lines if "Forge's version is in" in line] == expected
    notes = [line for line in lines if "To adopt a suggestion" in line]
    assert notes == [f"- {ADOPT_NOTE}"]
    assert "kept yours" not in result.stdout
    assert f"> fix: Forge's version has them: copy {SUGGESTED}/agents/tester.md over " in (
        result.stdout
    )
    again = invoke([*command, "--json"], env=env)
    document = json.loads(again.stdout)
    assert document["new_files"] == [
        f"{SUGGESTED}/agents/python-senior.md",
        f"{SUGGESTED}/agents/tester.md",
        f"{SUGGESTED}/commands/init-agents.md",
    ]


def test_init_moves_the_new_md_files_an_older_init_left(tmp_path: Path, fake_claude: None) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47300"}
    command = ["init", str(root), "--yes", "--skip-telemetry"]
    assert invoke([*command, "--json"], env=env).exit_code == 0
    agents = root / ".claude" / "agents"
    (agents / "tester.md").write_text(TUNED_TESTER, encoding="utf-8")
    (agents / "tester.new.md").write_text(TUNED_TESTER + GATEWAY, encoding="utf-8")
    docs_updater = (agents / "docs-updater.md").read_text(encoding="utf-8")
    (agents / "docs-updater.new.md").write_text(docs_updater, encoding="utf-8")
    (agents / "helper.new.md").write_text("---\nname: helper\n---\nmine\n", encoding="utf-8")
    commands = root / ".claude" / "commands"
    (commands / "init-agents.new.md").write_text("older forge command\n", encoding="utf-8")
    state = {
        "completed": ["detect", "graph", "telemetry", "forge"],
        "run_id": "",
        "forge_runs": True,
    }
    (root / ".cuanta" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    dry = json.loads(invoke([*command, "--json", "--dry-run"], env=env).stdout)
    assert f"move: .claude/agents/tester.new.md → {SUGGESTED}/agents/tester.md" in dry["planned"]
    assert (agents / "tester.new.md").is_file()
    result = invoke([*command, "--json"], env=env)
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["resumed_from"] == "verify"
    assert document["ok"] is True
    beside = sorted(path.name for path in (root / ".claude").rglob("*") if ".new" in path.name)
    assert beside == ["helper.new.md"]
    suggested = root / ".cuanta" / "forge-suggested" / "claude"
    assert (suggested / "agents" / "tester.md").read_text(
        encoding="utf-8"
    ) == TUNED_TESTER + GATEWAY
    assert (suggested / "commands" / "init-agents.md").read_text(encoding="utf-8") == (
        "older forge command\n"
    )
    assert not (suggested / "agents" / "docs-updater.md").exists()
    assert (agents / "docs-updater.md").read_text(encoding="utf-8") == docs_updater
    texts = [item["text"] for item in document["verify"]]
    assert (
        "moved 3 files an older init left beside yours out of .claude/: "
        ".claude/agents/docs-updater.new.md, .claude/agents/tester.new.md, "
        ".claude/commands/init-agents.new.md"
    ) in texts
    assert document["new_files"] == [
        f"{SUGGESTED}/agents/tester.md",
        f"{SUGGESTED}/commands/init-agents.md",
    ]


def test_refresh_lists_each_suggestion_once_and_never_writes_beside(
    tmp_path: Path, fake_claude: None
) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47330"}
    command = ["init", str(root), "--yes", "--skip-telemetry", "--json"]
    assert invoke(command, env=env).exit_code == 0
    (root / ".claude" / "commands" / "init-agents.md").write_text("mine\n", encoding="utf-8")
    result = invoke(["refresh", "--json", "--project", str(root)], env=env)
    assert result.exit_code == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["new_files"] == [f"{SUGGESTED}/commands/init-agents.md"]
    assert [path for path in (root / ".claude").rglob("*") if ".new" in path.name] == []
    summaries = [
        item["text"] for item in report["verify"] if "Forge's version is in" in item["text"]
    ]
    assert len(summaries) == 1
    assert summaries[0].startswith(".claude/commands/init-agents.md: Forge's version is in ")


def test_user_scope_suggestions_wait_under_the_install_root(
    tmp_path: Path, fake_claude: None, isolated_user_dirs: Path
) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47340"}
    command = ["init", str(root), "--yes", "--skip-telemetry", "--json", "--scope", "user"]
    assert invoke(command, env=env).exit_code == 0
    tuned = isolated_user_dirs / ".claude" / "commands" / "init-agents.md"
    tuned.write_text("mine\n", encoding="utf-8")
    result = invoke([*command, "--refresh-forge"], env=env)
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    suggestion = (
        isolated_user_dirs
        / ".cuanta"
        / "forge-suggested"
        / "claude"
        / "commands"
        / "init-agents.md"
    )
    assert document["new_files"] == [str(suggestion)]
    assert suggestion.is_file()
    assert tuned.read_text(encoding="utf-8") == "mine\n"
    beside = [path for path in (isolated_user_dirs / ".claude").rglob("*") if ".new" in path.name]
    assert beside == []
    texts = [item["text"] for item in document["verify"]]
    assert f"Forge's version of a file you changed is in {suggestion}; yours was kept" in texts
    assert ADOPT_NOTE in texts


def test_a_rulebook_over_its_ceiling_warns_and_init_completes(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    lines = ["# Rules", "", "> **No code graph, deliberately.** Structure lives in the code."]
    lines.extend(f"- rule {number}" for number in range(301 - len(lines)))
    (root / "CLAUDE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = invoke(["init", str(root), "--yes", "--json", "--skip-telemetry"])
    assert result.exit_code == 0, result.stdout
    document = json.loads(result.stdout)
    assert document["ok"] is True
    ceiling = next(item for item in document["verify"] if item["text"].startswith("CLAUDE.md"))
    assert (ceiling["status"], ceiling["text"]) == ("warn", "CLAUDE.md 301/300 lines, no warning")
    assert "Over ceiling" in ceiling["fix"]


def test_a_forge_run_that_rewrites_claude_md_says_so_once(
    tmp_path: Path, fake_claude: None
) -> None:
    root = copy_repo("python_strong", tmp_path)
    env = {"CUANTA_CLAUDE_BIN": fake_bin(), "CUANTA_PORT": "47300"}
    command = ["init", str(root), "--yes", "--skip-telemetry"]
    assert invoke([*command, "--json"], env=env).exit_code == 0
    forge = "> **No code graph, deliberately.** Small.\n"
    mine = forge + "".join(f"- rule {number}\n" for number in range(300))
    (root / "CLAUDE.md").write_text(mine, encoding="utf-8")
    rewritten = tmp_path / "rewritten.md"
    rewritten.write_text(forge + "".join(f"- kept {n}\n" for n in range(289)), encoding="utf-8")
    unchanged = invoke(["--plain", *command, "--refresh-forge"], env=env)
    assert "Forge rewrote it in place" not in unchanged.stdout
    (root / "CLAUDE.md").write_text(mine, encoding="utf-8")
    result = invoke(
        ["--plain", *command, "--refresh-forge"], env={**env, "FAKE_FORGE_RULEBOOK": str(rewritten)}
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    notes = [line for line in result.stdout.splitlines() if "Forge rewrote it in place" in line]
    assert notes == [
        "- CLAUDE.md: Forge rewrote it in place (301 → 290 lines); "
        "compare it with your previous version in .cuanta/backups/CLAUDE.md.bak"
    ]
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == rewritten.read_text(encoding="utf-8")
    backup = root / ".cuanta" / "backups" / "CLAUDE.md.bak"
    assert backup.read_text(encoding="utf-8") == mine
