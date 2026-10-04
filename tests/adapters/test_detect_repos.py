from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cuanta.adapters.forge.assets import init_prompt, refresh_prompt
from cuanta.adapters.forge.installer import VendoredForgeKit
from cuanta.adapters.graph.graphify import GraphifyTool
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalHome, LocalWorkspace
from cuanta.application.detect import DetectProject
from cuanta.application.doctor import graph_check
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.forge import ForgeStage, VerifyStage, forge_allowed_tools, forge_prompt
from cuanta.application.init_project import (
    GraphStage,
    HandoffWriter,
    InitOptions,
    InitProject,
    InitStage,
)
from cuanta.application.mandate_template import MandateTemplates
from cuanta.application.progress import RecordingSink
from cuanta.application.refresh import RefreshProject
from cuanta.domain.detection import DocsState, ForgeState, GraphMode, SizeTier, VerifyTier
from cuanta.domain.forge_template import MANDATE_TEMPLATE
from cuanta.domain.messages import english
from cuanta.domain.progress import Note, Status, StepFinished
from cuanta.ports.graph import GraphResult, GraphTool
from cuanta.ports.system import Completed
from tests.fakes import FakeGraph, FakeRunner, FakeScheduler, VirtualTime, copy_repo
from tests.real_run import FORGE_DONE, forged_bytes, forged_project


def _detector(
    root: Path, runner: FakeRunner | None = None, home: Path | None = None
) -> DetectProject:
    return DetectProject(
        LocalWorkspace(root),
        LocalHome(home or root / "__home__"),
        runner or FakeRunner(),
    )


def test_python_fixture_is_strong(tmp_path: Path) -> None:
    detection = _detector(copy_repo("python_strong", tmp_path)).run()
    stack = detection.stack
    assert stack.language == "python"
    assert stack.language_version == ">=3.12"
    assert stack.framework == "fastapi"
    assert stack.framework_version == "0.115.2"
    assert stack.package_manager == "uv"
    assert stack.test_runner == "pytest"
    assert stack.test_command == "uv run pytest"
    assert "shop = shop.cli:main" in stack.entry_points
    assert detection.verify_tier is VerifyTier.STRONG
    assert detection.verify.evidence.startswith("pytest + mypy")
    assert detection.size_tier is SizeTier.SMALL
    assert detection.file_count == 3
    assert detection.project_name == "shop"


def test_typescript_fixture_is_moderate(tmp_path: Path) -> None:
    detection = _detector(copy_repo("ts_moderate", tmp_path)).run()
    stack = detection.stack
    assert stack.language == "typescript"
    assert stack.language_version == "5.4.5"
    assert stack.framework == "express"
    assert stack.framework_version == "4.19.2"
    assert stack.package_manager == "npm"
    assert stack.test_runner == ""
    assert detection.verify_tier is VerifyTier.MODERATE
    assert "placeholder" in detection.verify.evidence


def test_go_fixture_is_strong(tmp_path: Path) -> None:
    detection = _detector(copy_repo("go_strong", tmp_path)).run()
    stack = detection.stack
    assert stack.language == "go"
    assert stack.language_version == "1.22"
    assert stack.framework == "gin"
    assert stack.framework_version == "v1.10.0"
    assert stack.entry_points == ("cmd/api/main.go",)
    assert detection.verify_tier is VerifyTier.STRONG
    assert detection.verify.evidence == "go test + go build"


def test_js_placeholder_fixture_is_weak(tmp_path: Path) -> None:
    detection = _detector(copy_repo("js_weak", tmp_path)).run()
    assert detection.verify_tier is VerifyTier.WEAK


def test_unknown_repo_is_unverified(tmp_path: Path) -> None:
    (tmp_path / "script.py").write_text("print(1)\n", encoding="utf-8")
    detection = _detector(tmp_path).run()
    assert detection.stack.language == "python"
    assert not detection.stack.verified
    assert detection.verify_tier is VerifyTier.WEAK


def test_docs_forge_vcs_and_exclusions(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    (root / "CLAUDE.md").write_text("# rules\n", encoding="utf-8")
    (root / "src" / "CLAUDE.md").write_text("# local\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "python-senior.md").write_text("x", encoding="utf-8")
    (root / "node_modules" / "lib").mkdir(parents=True)
    (root / "node_modules" / "lib" / "index.js").write_text("x", encoding="utf-8")
    detection = _detector(root).run()
    assert detection.docs_state is DocsState.EXISTS
    assert detection.forge_state is ForgeState.INITIALIZED
    assert detection.vcs
    assert detection.file_count == 3


def test_detection_counts_and_rulebooks_follow_gitignore(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    (root / ".gitignore").write_text("generated/\n", encoding="utf-8")
    generated = root / "generated"
    generated.mkdir()
    for index in range(30):
        (generated / f"model_{index}.py").write_text("", encoding="utf-8")
    (generated / "CLAUDE.md").write_text("# generated\n", encoding="utf-8")
    environment = root / "env312"
    (environment / "lib").mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (environment / "lib" / "site.py").write_text("", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# rules\n", encoding="utf-8")
    detection = _detector(root).run()
    assert detection.file_count == 3
    assert detection.size_tier is SizeTier.SMALL
    assert detection.docs_state is DocsState.PARTIAL


def test_graph_mode_priority(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    mcp = {"mcpServers": {"graph": {"command": "npx", "args": ["code-graph-mcp"]}}}
    (root / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    assert _detector(root).run().graph_mode is GraphMode.MCP
    with_cli = FakeRunner(binaries={"graphify": "/bin/graphify"})
    assert _detector(root, with_cli).run().graph_mode is GraphMode.CLI


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize(
    ("platform", "separator"), [("win32", "; "), ("linux", " && "), ("darwin", " && ")]
)
def test_graphify_trampoline_is_broken_even_with_graph_directory(
    tmp_path: Path,
    returncode: int,
    platform: str,
    separator: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_repo("python_strong", tmp_path)
    (root / "graphify-out").mkdir()
    runner = FakeRunner(
        binaries={"graphify": "/bin/graphify"},
        responses={
            "graphify --help": Completed(
                returncode, "", "uv trampoline failed to canonicalize script path"
            )
        },
    )
    detection = _detector(root, runner).run(with_engines=False)
    assert detection.graph_mode is GraphMode.BROKEN
    assert runner.calls == [("graphify", "--help")]
    monkeypatch.setattr(sys, "platform", platform)
    check = graph_check(detection)[0]
    assert check.status is Status.FAIL
    assert check.fix == (
        f"uv self update{separator}uv tool upgrade --all; or reinstall: "
        f"uv tool uninstall graphifyy{separator}uv tool install graphifyy"
    )


def test_nonzero_graphify_help_is_broken(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    runner = FakeRunner(
        binaries={"graphify": "/bin/graphify"},
        responses={"graphify --help": Completed(2, "", "launcher error")},
    )
    detection = _detector(root, runner).run(with_engines=False)
    assert detection.graph_mode is GraphMode.BROKEN
    assert "launcher error" in detection.graph_evidence


def test_graphify_tool_refuses_broken_launcher_update(tmp_path: Path) -> None:
    runner = FakeRunner(
        binaries={"graphify": "/bin/graphify"},
        responses={
            "graphify --help": Completed(0, "", "uv trampoline failed to canonicalize script path")
        },
    )
    result = GraphifyTool(runner).update(tmp_path)
    assert not result.ok
    assert "uv trampoline failed" in result.detail
    assert runner.calls == [("graphify", "--help")]


def test_broken_graph_removes_forge_graph_permission(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    runner = FakeRunner(
        binaries={"graphify": "/bin/graphify"},
        responses={"graphify --help": Completed(1, "", "uv trampoline failed")},
    )
    detection = _detector(root, runner).run(with_engines=False)
    assert "Bash(graphify *)" not in forge_allowed_tools("none")
    for original in (init_prompt(), refresh_prompt()):
        prompt = forge_prompt(original, detection)
        assert "graphify" not in prompt.lower()
        assert "Do not repeat detection, install or index a graph" in prompt
        assert "This headless run cannot write" in prompt


def test_broken_graph_skips_init_update_with_reason(tmp_path: Path) -> None:
    root = _medium_repo(tmp_path)
    runner = FakeRunner(
        binaries={"graphify": "/bin/graphify"},
        responses={"graphify --help": Completed(1, "", "uv trampoline failed")},
    )
    graph = FakeGraph(present=True)
    use_case, _ = _init(root, graph, runner)
    report = use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    assert report.ok
    assert graph.calls == []
    assert "graphify launcher is broken" in report.context.graph_line
    assert report.context.graph_mode == "none"


def test_broken_graph_refresh_skips_update_with_reason(tmp_path: Path) -> None:
    root = _medium_repo(tmp_path)
    workspace = LocalWorkspace(root)
    runner = FakeRunner(
        binaries={"graphify": "/bin/graphify"},
        responses={"graphify --help": Completed(1, "", "uv trampoline failed")},
    )
    graph = FakeGraph(present=True)
    refresh = RefreshProject(
        workspace=workspace,
        detector=_detector(root, runner),
        graph_stage=GraphStage(workspace, graph),
        kit=VendoredForgeKit(root),
        launcher_factory=lambda: None,
        verify_stage=VerifyStage(workspace, MemoryLedger, FixedClock()),
        progress=RecordingSink(),
    )
    report = refresh.run()
    assert report.detection.graph_mode is GraphMode.BROKEN
    assert report.stages[0][1].status is Status.SKIP
    assert "graphify launcher is broken" in report.stages[0][1].detail
    assert report.context.graph_mode == "none"
    assert graph.calls == []


def test_graph_mode_from_user_claude_json(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    document = {"projects": {str(root): {"mcpServers": {"repo-graph-server": {"command": "x"}}}}}
    (home / ".claude.json").write_text(json.dumps(document), encoding="utf-8")
    detection = _detector(root, home=home).run()
    assert detection.graph_mode is GraphMode.MCP
    assert "~/.claude.json" in detection.graph_evidence


def test_engines_are_probed(tmp_path: Path) -> None:
    runner = FakeRunner(
        binaries={"claude": "/bin/claude", "opencode": "/bin/opencode"},
        responses={
            "claude --version": Completed(0, "2.1.280 (Claude Code)\n", ""),
            "opencode --version": Completed(0, "1.18.27\n", ""),
        },
    )
    detection = _detector(copy_repo("go_strong", tmp_path), runner).run()
    assert [(engine.name, engine.version) for engine in detection.engines] == [
        ("claude", "2.1.280"),
        ("opencode", "1.18.27"),
    ]


def _init(
    root: Path,
    graph: GraphTool,
    runner: FakeRunner | None = None,
    scheduler: FakeScheduler | None = None,
    forge: Callable[[RecordingSink], InitStage] | None = None,
    verify: bool = False,
    monotonic: Callable[[], float] | None = None,
) -> tuple[InitProject, RecordingSink]:
    workspace = LocalWorkspace(root)
    sink = RecordingSink()
    graph_stage = (
        GraphStage(workspace, graph)
        if scheduler is None
        else GraphStage(workspace, graph, scheduler)
    )
    use_case = InitProject(
        workspace=workspace,
        detector=_detector(root, runner),
        graph_stage=graph_stage,
        handoff=HandoffWriter(workspace, FixedClock()),
        progress=sink,
        forge_stage=forge(sink) if forge is not None else None,
        verify_stage=VerifyStage(workspace, MemoryLedger, FixedClock()) if verify else None,
        monotonic=time.monotonic if monotonic is None else monotonic,
    )
    return use_case, sink


def test_init_writes_forge_handoff_and_ignores(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    (root / ".git").mkdir()
    (root / ".gitignore").write_text("node_modules/", encoding="utf-8")
    use_case, _ = _init(root, FakeGraph())
    report = use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    assert report.ok
    state = json.loads((root / ".claude" / "forge-state.json").read_text(encoding="utf-8"))
    assert state == {
        "version": "0.3",
        "started_at": "2026-01-01T00:00:00Z",
        "size_tier": "small",
        "graph_mode": "none",
        "docs_state": "absent",
        "forge_state": "fresh",
        "verify_tier": "strong",
        "phases_completed": ["0", "0.5"],
        "dirs_written": [],
        "dirs_queued": [],
        "dirs_rejected": [],
        "unverified": [],
        "verify_markers": [],
        "producer": "cuanta",
    }
    assert (root / ".gitignore").read_text(encoding="utf-8") == (
        "node_modules/\n.claude/forge-state.json\n"
    )
    assert (root / ".cuanta" / ".gitignore").read_text(encoding="utf-8") == "*\n!config.toml\n"
    assert report.context.graph_line.startswith("skipped (small tier")


def test_init_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    graph = FakeGraph()
    use_case, _ = _init(root, graph)
    report = use_case.run(InitOptions(dry_run=True))
    assert not (root / ".claude").exists()
    assert not (root / ".cuanta").exists()
    assert graph.calls == []
    assert any("forge-state.json" in english(change) for change in report.context.planned)


def _medium_repo(tmp_path: Path) -> Path:
    root = copy_repo("python_strong", tmp_path)
    for index in range(120):
        (root / "src" / "shop" / f"module_{index}.py").write_text("x = 1\n", encoding="utf-8")
    return root


def test_graph_install_failure_degrades_to_none(tmp_path: Path) -> None:
    root = _medium_repo(tmp_path)
    use_case, _ = _init(root, FakeGraph(install_ok=False))
    report = use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    assert report.ok
    assert report.context.graph_line == "FAILED — network down"
    state = json.loads((root / ".claude" / "forge-state.json").read_text(encoding="utf-8"))
    assert state["graph_mode"] == "none"


def test_graph_install_success_sets_cli(tmp_path: Path) -> None:
    root = _medium_repo(tmp_path)
    graph = FakeGraph()
    use_case, _ = _init(root, graph)
    report = use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    assert graph.calls == ["install", "update"]
    assert report.context.graph_line == "installed"


def test_init_resumes_after_failure(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    (root / ".cuanta").mkdir()
    (root / ".cuanta" / "state.json").write_text(
        '{"completed": ["detect", "graph"]}', encoding="utf-8"
    )
    graph = FakeGraph()
    use_case, sink = _init(root, graph)
    report = use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    assert report.resumed_from == "telemetry"
    assert graph.calls == []
    assert any(getattr(event, "status", None) is Status.RESUME for event in sink.events)


@pytest.mark.perf
def test_detect_ten_thousand_files_under_two_seconds(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    for folder in range(100):
        package = root / "src" / f"pkg_{folder}"
        package.mkdir()
        for index in range(100):
            (package / f"m_{index}.py").write_text("", encoding="utf-8")
    heavy = root / "node_modules" / "dep"
    heavy.mkdir(parents=True)
    for index in range(2000):
        (heavy / f"f{index}.js").write_text("", encoding="utf-8")
    started = time.perf_counter()
    detection = _detector(root).run()
    elapsed = time.perf_counter() - started
    assert detection.file_count == 10_003
    assert detection.size_tier is SizeTier.XL
    assert elapsed < 2.0


KEPT = "kept: FORGE_STATE=initialized · --refresh-forge runs Forge again"
SCHEDULED = "updating in the background · .cuanta/graph-refresh.log"


def _counting(launches: list[str]) -> Callable[[], EngineLauncher | None]:
    def factory() -> EngineLauncher | None:
        launches.append("claude")
        return None

    return factory


def _forge(
    root: Path, launches: list[str], templates: bool = False
) -> Callable[[RecordingSink], InitStage]:
    def build(sink: RecordingSink) -> InitStage:
        workspace = LocalWorkspace(root)
        if not templates:
            return ForgeStage(
                VendoredForgeKit(root), _counting(launches), sink, str(root), workspace
            )
        return ForgeStage(
            VendoredForgeKit(root),
            _counting(launches),
            sink,
            str(root),
            workspace,
            templates=MandateTemplates(workspace, VendoredForgeKit(root)),
        )

    return build


def _phases(root: Path) -> list[str]:
    state = json.loads((root / ".claude" / "forge-state.json").read_text(encoding="utf-8"))
    return list(state["phases_completed"])


def test_an_initialized_init_keeps_the_forge_state_and_launches_nothing(tmp_path: Path) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    before = forged_bytes(root)
    launches: list[str] = []
    use_case, sink = _init(root, FakeGraph(), forge=_forge(root, launches), verify=True)
    report = use_case.run(InitOptions(skip_telemetry=True))
    assert launches == []
    forge = dict(report.stages)["forge"]
    assert (forge.status, forge.detail) == (Status.SKIP, KEPT)
    assert forged_bytes(root) == before
    assert _phases(root) == list(FORGE_DONE)
    texts = [finding.text for finding in report.context.verify_lines]
    assert "forge-state: ten phases" in texts
    assert not [text for text in texts if text.startswith("forge-state incomplete")]
    assert report.ok
    notes = [event.text for event in sink.events if isinstance(event, Note)]
    assert not [text for text in notes if "routing to refresh semantics" in text]
    assert (root / ".cuanta" / ".gitignore").is_file()


def test_skip_forge_never_resets_a_completed_forge_state(tmp_path: Path) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    (root / ".git").mkdir()
    before = (root / ".claude" / "forge-state.json").read_bytes()
    use_case, _ = _init(root, FakeGraph())
    report = use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    assert report.ok
    assert (root / ".claude" / "forge-state.json").read_bytes() == before
    assert (root / ".cuanta" / ".gitignore").is_file()
    assert ".claude/forge-state.json" in (root / ".gitignore").read_text(encoding="utf-8")


def test_an_absent_forge_state_still_gets_the_cuanta_handoff(tmp_path: Path) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    (root / ".claude" / "forge-state.json").unlink()
    launches: list[str] = []
    use_case, _ = _init(root, FakeGraph(), forge=_forge(root, launches))
    report = use_case.run(InitOptions(skip_telemetry=True))
    assert launches == []
    assert dict(report.stages)["forge"].detail == KEPT
    state = json.loads((root / ".claude" / "forge-state.json").read_text(encoding="utf-8"))
    assert (state["phases_completed"], state["producer"]) == (["0", "0.5"], "cuanta")


def test_an_initialized_init_updates_the_graph_in_the_background(tmp_path: Path) -> None:
    root = forged_project(_medium_repo(tmp_path), graph_mode="cli")
    runner = FakeRunner(binaries={"graphify": "/bin/graphify"})
    graph = FakeGraph(present=True)
    scheduler = FakeScheduler()
    launches: list[str] = []
    use_case, _ = _init(root, graph, runner, scheduler, _forge(root, launches), verify=True)
    report = use_case.run(InitOptions(skip_telemetry=True))
    assert graph.calls == []
    assert scheduler.calls == 1
    assert launches == []
    result = dict(report.stages)["graph"]
    assert (result.status, result.detail) == (Status.OK, SCHEDULED)
    assert report.context.graph_line == SCHEDULED
    assert report.context.graph_mode == "cli"
    assert report.ok


def test_a_background_graph_that_cannot_start_is_a_warning(tmp_path: Path) -> None:
    root = forged_project(_medium_repo(tmp_path), graph_mode="cli")
    runner = FakeRunner(binaries={"graphify": "/bin/graphify"})
    scheduler = FakeScheduler(ok=False, detail="graphify not on PATH")
    use_case, _ = _init(root, FakeGraph(present=True), runner, scheduler)
    report = use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    result = dict(report.stages)["graph"]
    assert (result.status, result.detail) == (Status.WARN, "FAILED — graphify not on PATH")
    assert report.context.graph_mode == "none"


def test_a_first_graph_install_stays_in_the_foreground_and_updates_in_the_background(
    tmp_path: Path,
) -> None:
    root = forged_project(_medium_repo(tmp_path))
    graph = FakeGraph()
    scheduler = FakeScheduler()
    use_case, _ = _init(root, graph, scheduler=scheduler, forge=_forge(root, []))
    report = use_case.run(InitOptions(skip_telemetry=True))
    assert graph.calls == ["install"]
    assert scheduler.calls == 1
    assert dict(report.stages)["graph"].detail == SCHEDULED
    assert report.context.graph_mode == "cli"


def test_a_fresh_init_keeps_the_graph_in_the_foreground(tmp_path: Path) -> None:
    root = _medium_repo(tmp_path)
    runner = FakeRunner(binaries={"graphify": "/bin/graphify"})
    graph = FakeGraph(present=True)
    scheduler = FakeScheduler()
    use_case, _ = _init(root, graph, runner, scheduler)
    report = use_case.run(InitOptions(skip_telemetry=True))
    assert graph.calls == ["update"]
    assert scheduler.calls == 0
    assert dict(report.stages)["graph"].detail == "updated"


def test_refresh_forge_resumes_into_the_forge_stage(tmp_path: Path) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    (root / ".cuanta").mkdir()
    (root / ".cuanta" / "state.json").write_text(
        '{"completed": ["detect", "graph", "telemetry", "forge"]}', encoding="utf-8"
    )
    launches: list[str] = []
    use_case, sink = _init(root, FakeGraph(), forge=_forge(root, launches))
    report = use_case.run(InitOptions(skip_telemetry=True, refresh_forge=True))
    assert launches == ["claude"]
    assert report.resumed_from == "forge"
    notes = [event.text for event in sink.events if isinstance(event, Note)]
    assert "forge: FORGE_STATE=initialized — routing to refresh semantics" in notes


def test_refresh_forge_runs_forge_on_an_initialized_project(tmp_path: Path) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path), graph_mode="cli")
    launches: list[str] = []
    use_case, _ = _init(root, FakeGraph(), forge=_forge(root, launches))
    report = use_case.run(InitOptions(skip_telemetry=True, refresh_forge=True))
    assert launches == ["claude"]
    assert report.resumed_from is None


def test_a_resumed_init_finishes_the_forge_run_it_started(tmp_path: Path) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    (root / ".cuanta").mkdir()
    (root / ".cuanta" / "state.json").write_text(
        '{"completed": ["detect", "graph", "telemetry"]}', encoding="utf-8"
    )
    launches: list[str] = []
    use_case, _ = _init(root, FakeGraph(), forge=_forge(root, launches))
    report = use_case.run(InitOptions(skip_telemetry=True))
    assert launches == ["claude"]
    assert report.resumed_from == "forge"


def test_a_kept_forge_writes_only_missing_files_and_the_missing_template(
    tmp_path: Path,
) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path), template=False, kit=False)
    tuned = root / ".claude" / "commands" / "init-agents.md"
    tuned.parent.mkdir(parents=True)
    tuned.write_text("my tuned command", encoding="utf-8")
    launches: list[str] = []
    planned, _ = _init(root, FakeGraph(), forge=_forge(root, launches, templates=True))
    dry = planned.run(InitOptions(dry_run=True))
    changes = [english(change) for change in dry.context.planned]
    assert "keep: Forge is initialized, no model runs (--refresh-forge runs Forge again)" in changes
    template_plan = "write: docs/MANDATE_TEMPLATE.md from the vendored Forge"
    assert [change for change in changes if change.startswith(template_plan)]
    assert not [change for change in changes if "init-agents.md" in change]
    assert not [change for change in changes if change.startswith("write: .claude/forge-state")]
    assert not (root / MANDATE_TEMPLATE).exists()
    assert not (root / ".claude" / "skills").exists()
    use_case, sink = _init(
        root, FakeGraph(), forge=_forge(root, launches, templates=True), verify=True
    )
    report = use_case.run(InitOptions(skip_telemetry=True))
    assert launches == []
    forge = dict(report.stages)["forge"]
    assert forge.status is Status.OK
    assert forge.detail == (
        "kept: FORGE_STATE=initialized · restored 18 files · --refresh-forge runs Forge again"
    )
    assert tuned.read_text(encoding="utf-8") == "my tuned command"
    assert [path for path in root.rglob("*") if ".new." in path.name] == []
    assert (root / ".claude" / "skills" / "agent-system-init" / "SKILL.md").is_file()
    assert "=== REQUEST ===" in (root / MANDATE_TEMPLATE).read_text(encoding="utf-8")
    notes = [event.text for event in sink.events if isinstance(event, Note)]
    assert [text for text in notes if text.startswith("docs/MANDATE_TEMPLATE.md was missing")]
    assert report.ok


@dataclass
class _SlowGraph:
    time: VirtualTime
    calls: list[str] = field(default_factory=list)

    def available(self) -> bool:
        return True

    def install(self) -> GraphResult:
        self.calls.append("install")
        return GraphResult(True, "")

    def update(self, root: Path) -> GraphResult:
        self.calls.append("update")
        self.time.advance(52.0)
        return GraphResult(True, "")


def test_init_times_every_stage_and_the_whole_run(tmp_path: Path) -> None:
    root = _medium_repo(tmp_path)
    runner = FakeRunner(binaries={"graphify": "/bin/graphify"})
    clock = VirtualTime(now=100.0)
    use_case, sink = _init(root, _SlowGraph(clock), runner, monotonic=clock.monotonic)
    report = use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    finished = {event.key: event for event in sink.events if isinstance(event, StepFinished)}
    assert (finished["graph"].seconds, finished["graph"].detail) == (52.0, "updated · 52 s")
    assert (finished["detect"].seconds, finished["detect"].detail) == (
        0.0,
        "123 files · medium · 0 s",
    )
    assert finished["forge"].detail == "skipped (--skip-forge) · 0 s"
    stages = dict(report.stages)
    assert (stages["graph"].seconds, stages["graph"].detail) == (52.0, "updated")
    assert stages["verify"].seconds == 0.0
    assert report.seconds == 52.0


def test_a_kept_init_resumed_in_its_forge_stage_still_runs_no_model(tmp_path: Path) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    state = root / ".cuanta" / "state.json"
    state.parent.mkdir()
    state.write_text(
        '{"completed": ["detect", "graph", "telemetry"], "forge_runs": false}', encoding="utf-8"
    )
    launches: list[str] = []
    use_case, _ = _init(root, FakeGraph(), forge=_forge(root, launches))
    report = use_case.run(InitOptions(skip_telemetry=True))
    assert launches == []
    assert report.resumed_from == "forge"
    assert dict(report.stages)["forge"].detail == KEPT


def test_init_records_whether_its_forge_stage_runs_the_model(tmp_path: Path) -> None:
    root = forged_project(copy_repo("python_strong", tmp_path))
    use_case, _ = _init(root, FakeGraph())
    use_case.run(InitOptions(skip_forge=True, skip_telemetry=True))
    recorded = json.loads((root / ".cuanta" / "state.json").read_text(encoding="utf-8"))
    assert recorded["forge_runs"] is False
    fresh = copy_repo("go_strong", tmp_path)
    started, _ = _init(fresh, FakeGraph())
    started.run(InitOptions(skip_telemetry=True))
    recorded = json.loads((fresh / ".cuanta" / "state.json").read_text(encoding="utf-8"))
    assert recorded["forge_runs"] is True
