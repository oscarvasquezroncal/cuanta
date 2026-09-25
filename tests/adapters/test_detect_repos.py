from __future__ import annotations

import json
import time
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
from cuanta.application.forge import VerifyStage, forge_allowed_tools, forge_prompt
from cuanta.application.init_project import GraphStage, HandoffWriter, InitOptions, InitProject
from cuanta.application.progress import RecordingSink
from cuanta.application.refresh import RefreshProject
from cuanta.domain.detection import DocsState, ForgeState, GraphMode, SizeTier, VerifyTier
from cuanta.domain.messages import english
from cuanta.domain.progress import Status
from cuanta.ports.system import Completed
from tests.fakes import FakeGraph, FakeRunner, copy_repo


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


def test_graph_mode_priority(tmp_path: Path) -> None:
    root = copy_repo("python_strong", tmp_path)
    mcp = {"mcpServers": {"graph": {"command": "npx", "args": ["code-graph-mcp"]}}}
    (root / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    assert _detector(root).run().graph_mode is GraphMode.MCP
    with_cli = FakeRunner(binaries={"graphify": "/bin/graphify"})
    assert _detector(root, with_cli).run().graph_mode is GraphMode.CLI


@pytest.mark.parametrize("returncode", [0, 1])
def test_graphify_trampoline_is_broken_even_with_graph_directory(
    tmp_path: Path, returncode: int
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
    check = graph_check(detection)[0]
    assert check.status is Status.FAIL
    assert "uv self update" in check.fix
    assert "uv tool upgrade --all" in check.fix
    assert "uv tool uninstall graphifyy; uv tool install graphifyy" in check.fix


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
    root: Path, graph: FakeGraph, runner: FakeRunner | None = None
) -> tuple[InitProject, RecordingSink]:
    workspace = LocalWorkspace(root)
    sink = RecordingSink()
    use_case = InitProject(
        workspace=workspace,
        detector=_detector(root, runner),
        graph_stage=GraphStage(workspace, graph),
        handoff=HandoffWriter(workspace, FixedClock()),
        progress=sink,
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
