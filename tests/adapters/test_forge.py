from __future__ import annotations

import re
from pathlib import Path

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine, build_command
from cuanta.adapters.engines.claude_stream import parse_line
from cuanta.adapters.forge.assets import (
    COMMANDS,
    REQUIRED_SKILL_FILES,
    RESUME_LINE,
    init_prompt,
    refresh_prompt,
    strip_frontmatter,
    vendored_version,
    verify_manifest,
)
from cuanta.adapters.forge.installer import ForgeInstaller, sibling_new
from cuanta.domain.engine import (
    AssistantText,
    EngineRequest,
    RunResult,
    SessionStarted,
    ToolCall,
    phase_markers,
)
from cuanta.domain.forge_verify import ForgeTree, check_ceilings, placeholders, verify_tree
from cuanta.domain.progress import Status
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner

FORGE = Path(__file__).parents[2] / "src" / "cuanta" / "assets" / "forge"
ENGINES = Path(__file__).parents[1] / "fixtures" / "engines"


def _installer_list(path: Path, pattern: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    block = re.search(pattern, text, re.DOTALL)
    assert block is not None
    return [item.replace("\\", "/") for item in re.findall(r"[\w./\\-]+\.md", block.group(1))]


def test_vendored_forge_matches_installer_lists() -> None:
    bash = _installer_list(FORGE / "install.sh", r'REQUIRED_SKILL_FILES="(.*?)"')
    pwsh = _installer_list(FORGE / "install.ps1", r"\$RequiredSkillFiles\s*=\s*@\((.*?)\)")
    assert sorted(bash) == sorted(REQUIRED_SKILL_FILES)
    assert sorted(pwsh) == sorted(REQUIRED_SKILL_FILES)
    commands = re.search(r'COMMANDS="([^"]+)"', (FORGE / "install.sh").read_text(encoding="utf-8"))
    assert commands is not None
    assert tuple(commands.group(1).split()) == COMMANDS
    assert verify_manifest() == ()
    for name in REQUIRED_SKILL_FILES:
        assert (FORGE / "skills" / "agent-system-init" / name).is_file()


def test_vendored_version_is_pinned() -> None:
    version = vendored_version()
    assert re.fullmatch(r"[0-9a-f]{40}", version["commit"])
    assert (FORGE / "LICENSE").is_file()


def test_prompts() -> None:
    prompt = init_prompt()
    assert not prompt.startswith("---")
    assert f"\n\n{RESUME_LINE}\n\n" in prompt
    assert "agent-system-init" in prompt
    assert "REFRESH" in refresh_prompt()
    assert strip_frontmatter("---\na: b\n---\n\nbody\n") == "body"
    assert strip_frontmatter("plain") == "plain"


def test_installer_never_overwrites(tmp_path: Path) -> None:
    installer = ForgeInstaller(tmp_path)
    first = installer.install()
    assert len(first.written) == len(REQUIRED_SKILL_FILES) + len(COMMANDS)
    again = installer.install()
    assert again.written == [] and again.new_files == []
    tuned = tmp_path / ".claude" / "commands" / "init-agents.md"
    tuned.write_text("my tuned command", encoding="utf-8")
    third = installer.install()
    assert third.new_files == [str(tuned.with_name("init-agents.new.md"))]
    assert tuned.read_text(encoding="utf-8") == "my tuned command"
    assert sibling_new(Path("x/SKILL.md")).name == "SKILL.new.md"


def test_installer_dry_run_writes_nothing(tmp_path: Path) -> None:
    report = ForgeInstaller(tmp_path).install(dry_run=True)
    assert report.written
    assert not (tmp_path / ".claude").exists()


def test_claude_stream_fixture_parses() -> None:
    events = [
        event
        for line in (ENGINES / "claude_stream.jsonl").read_text(encoding="utf-8").splitlines()
        for event in parse_line(line)
    ]
    assert isinstance(events[0], SessionStarted)
    tools = [event for event in events if isinstance(event, ToolCall)]
    assert [tool.name for tool in tools] == ["Read"]
    result = events[-1]
    assert isinstance(result, RunResult)
    assert result.ok
    assert result.cost_usd is not None
    assert result.cost_usd > 0
    assert result.models[0].model == "claude-haiku-4-5-20251001"
    assert result.models[0].cache_read_tokens > 0
    assert any(isinstance(event, AssistantText) for event in events)
    assert parse_line("garbage") == []
    failed = parse_line('{"type": "result", "subtype": "error_max_turns", "is_error": true}')[0]
    assert isinstance(failed, RunResult)
    assert failed.ok is False


def test_prompts_ask_for_staging_outside_protected_claude_dir() -> None:
    from cuanta.adapters.forge.assets import STAGING_LINE

    assert init_prompt().endswith(STAGING_LINE)
    assert RESUME_LINE in init_prompt()
    assert refresh_prompt().endswith(STAGING_LINE)
    assert ".cuanta/forge-out/claude/forge-state.json" in STAGING_LINE
    assert "forge-out/.claude" not in STAGING_LINE


def test_promote_staged_applies_new_md_policy(tmp_path: Path) -> None:
    from cuanta.adapters.system.workspace import LocalWorkspace
    from cuanta.application.forge import promote_staged

    staged = tmp_path / ".cuanta" / "forge-out" / "claude"
    (staged / "agents").mkdir(parents=True)
    (staged / "agents" / "tester.md").write_text("new tester", encoding="utf-8")
    (staged / "agents" / "docs-updater.md").write_text("same", encoding="utf-8")
    (staged / "agents" / "python-senior.md").write_text("fresh", encoding="utf-8")
    (staged / "forge-state.json").write_text('{"phases_completed": ["0"]}', encoding="utf-8")
    (tmp_path / ".cuanta" / "forge-out" / "stray.md").write_text("x", encoding="utf-8")
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "tester.md").write_text("tuned by hand", encoding="utf-8")
    (agents / "docs-updater.md").write_text("same", encoding="utf-8")
    (tmp_path / ".claude" / "forge-state.json").write_text("{}", encoding="utf-8")
    promoted, kept = promote_staged(LocalWorkspace(tmp_path))
    assert sorted(promoted) == [".claude/agents/python-senior.md", ".claude/forge-state.json"]
    assert kept == [".claude/agents/tester.new.md"]
    assert (agents / "tester.md").read_text(encoding="utf-8") == "tuned by hand"
    assert (agents / "tester.new.md").read_text(encoding="utf-8") == "new tester"
    assert "phases_completed" in (tmp_path / ".claude" / "forge-state.json").read_text(
        encoding="utf-8"
    )
    assert not (staged / "agents" / "tester.md").exists()
    assert not (tmp_path / ".claude" / "stray.md").exists()


def test_harness_heading_is_case_insensitive() -> None:
    tree = ForgeTree(
        texts={"AGENTS_GUIDE.md": "## 7. The harness — what this repo now has"},
        agent_names=(),
        docs_names=(),
        per_directory={},
        graph_wired=True,
        phases_completed=(),
        new_files=(),
    )
    findings = verify_tree(tree, {})
    assert not any("§Harness" in finding.text for finding in findings)


def test_permission_denials_and_registration() -> None:
    from cuanta.application.forge import (
        FORGE_ALLOWED_TOOLS,
        registration,
    )
    from cuanta.application.mandate import BASE_TOOLS

    assert "Skill" in FORGE_ALLOWED_TOOLS
    assert "Skill" in BASE_TOOLS
    line = (
        '{"type":"result","subtype":"success","is_error":false,"permission_denials":'
        '[{"tool_name":"Skill","tool_use_id":"t1","tool_input":{}},"Bash"]}'
    )
    result = parse_line(line)[0]
    assert isinstance(result, RunResult)
    assert result.denials == ("Skill", "Bash")
    assert registration(result.denials, 0) == (
        "deferred — the Skill tool was denied; Forge read SKILL.md off disk instead"
    )
    assert registration((), 0) == "ok · Skill tool allowed"
    assert registration((), 2) == "ok · Skill tool used 2x"


def test_subagent_detection() -> None:
    call = ToolCall("Task", "t", {"subagent_type": "tester"})
    assert call.spawned_agent == "tester"
    assert ToolCall("Read", "t", {"subagent_type": "x"}).spawned_agent == ""
    assert phase_markers("Phase 2A done; phase 0.5 skipped; Phase 9 no; Phase 2A again") == (
        "2A",
        "0.5",
    )


def test_build_command_matches_verified_flags() -> None:
    request = EngineRequest(
        prompt="go",
        cwd=".",
        env={},
        allowed_tools=("Read", "Bash(graphify *)"),
        disallowed_tools=("Bash(git *)",),
        model="sonnet",
        max_budget_usd=2.5,
    )
    assert build_command(("claude",), request) == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        "Read,Bash(graphify *)",
        "--disallowedTools",
        "Bash(git *)",
        "--model",
        "sonnet",
        "--max-budget-usd",
        "2.50",
    ]


def test_missing_flags_capability_check() -> None:
    runner = FakeRunner(
        binaries={"claude": "/bin/claude"},
        responses={"claude --help": Completed(0, "--print --verbose", "")},
    )
    missing = ClaudeCodeEngine(runner).missing_flags()
    assert "--permission-mode" in missing
    assert "dontAsk" in missing
    assert "--print" not in missing


def test_forge_verify_rules() -> None:
    assert placeholders({"a.md": "x {{PROJECT_NAME}} y"}) == ("a.md:{{PROJECT_NAME}}",)
    over = "\n".join(["x"] * 301)
    assert check_ceilings(over, {})[0].status is Status.FAIL
    assert check_ceilings("# t\n⚠ Over ceiling: 301/300\n" + over, {})[0].status is Status.WARN
    assert check_ceilings("ok", {"src/CLAUDE.md": "\n".join(["y"] * 41)})[1].status is Status.FAIL
    tree = ForgeTree(
        texts={"CLAUDE.md": "rules"},
        agent_names=("tester.md",),
        docs_names=(),
        per_directory={},
        graph_wired=False,
        phases_completed=("0",),
        new_files=("docs/x.new.md",),
    )
    findings = verify_tree(tree, {})
    statuses = [finding.status for finding in findings]
    assert Status.FAIL in statuses
    assert any("kept yours" in finding.text for finding in findings)


def test_forge_progress_ignores_phase_markers_that_go_backwards() -> None:
    from cuanta.application.forge import ForgeProgress
    from cuanta.domain.engine import AssistantText
    from cuanta.domain.progress import Note, ProgressEvent

    published: list[ProgressEvent] = []

    class Sink:
        def publish(self, event: ProgressEvent) -> None:
            published.append(event)

    progress = ForgeProgress(Sink())
    for text in (
        "Starting Phase 0 now",
        "Phase 0.5 graph bootstrap",
        "Phase 1 audit, then Phase 2A",
        "Phase 4 guide written",
        "Summary: Phase 0 detected python, Phase 0.5 wired the graph, Phase 3 made agents",
        "Phase 5 and Phase 6 done",
    ):
        progress(AssistantText(text))
    assert progress.phases == ["0", "0.5", "1", "2A", "4", "5", "6"]
    texts = [event.text for event in published if isinstance(event, Note)]
    assert texts[-3:] == ["forge · Phase 4", "forge · Phase 5", "forge · Phase 6"]
