from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from cuanta.domain.detection import (
    FORGE_ARTIFACTS,
    Detection,
    EngineInfo,
    ForgeState,
    GraphMode,
    docs_state,
    forge_state,
    size_tier,
    verify_tier,
)
from cuanta.domain.forge_state import CorruptState, ForgeRunState, parse_state, run_note
from cuanta.domain.graph_policy import matches_graph_server
from cuanta.domain.manifests import (
    LOCKFILES,
    MANIFEST_ORDER,
    PRESENCE_PROBES,
    ProjectFiles,
    detect_stack,
)
from cuanta.ports.system import ProcessRunner
from cuanta.ports.workspace import HomeReader, Workspace

ENGINE_NAMES = ("claude", "codex", "opencode")
FORGE_STATE_FILE = ".claude/forge-state.json"
_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?(?:[-.\w]*)?")


@dataclass(frozen=True, slots=True)
class StateRead:
    state: ForgeRunState | None
    problem: str | None


def read_forge_state(workspace: Workspace) -> StateRead:
    text = workspace.read_text(FORGE_STATE_FILE)
    if text is None:
        return StateRead(None, None)
    try:
        return StateRead(parse_state(json.loads(text)), None)
    except json.JSONDecodeError as error:
        return StateRead(None, f"invalid JSON ({error.msg})")
    except CorruptState as error:
        return StateRead(None, str(error))


def probe_engines(
    runner: ProcessRunner, names: tuple[str, ...] = ENGINE_NAMES
) -> tuple[EngineInfo, ...]:
    found: list[EngineInfo] = []
    for name in names:
        path = runner.which(name)
        if path is None:
            continue
        completed = runner.run([name, "--version"], timeout=20)
        match = _VERSION.search(completed.stdout or completed.stderr)
        found.append(EngineInfo(name=name, path=path, version=match.group(0) if match else ""))
    return tuple(found)


def _mcp_candidates(document: Any, project_key: str | None) -> list[str]:
    candidates: list[str] = []
    if not isinstance(document, dict):
        return candidates
    tables: list[Any] = [document.get("mcpServers")]
    projects = document.get("projects")
    if project_key is not None and isinstance(projects, dict):
        for key, value in projects.items():
            if isinstance(value, dict) and _same_path(key, project_key):
                tables.append(value.get("mcpServers"))
    for table in tables:
        if not isinstance(table, dict):
            continue
        for name, server in table.items():
            candidates.append(str(name))
            if isinstance(server, dict):
                command = server.get("command")
                if isinstance(command, str):
                    candidates.append(command)
                args = server.get("args")
                if isinstance(args, list):
                    candidates.extend(str(arg) for arg in args)
    return candidates


def _same_path(left: str, right: str) -> bool:
    return (
        left.replace("\\", "/").rstrip("/").lower() == right.replace("\\", "/").rstrip("/").lower()
    )


def _load_json(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


class DetectProject:
    def __init__(
        self,
        workspace: Workspace,
        home: HomeReader,
        runner: ProcessRunner,
        extra_exclusions: frozenset[str] = frozenset(),
        engine_names: tuple[str, ...] = ENGINE_NAMES,
    ) -> None:
        self._workspace = workspace
        self._home = home
        self._runner = runner
        self._exclusions = extra_exclusions
        self._engine_names = engine_names

    def graph_mode(self) -> tuple[GraphMode, str]:
        workspace = self._workspace
        if self._runner.which("graphify") is not None:
            completed = self._runner.run(["graphify", "--help"], timeout=5)
            output = f"{completed.stderr}\n{completed.stdout}"
            if not completed.ok or "uv trampoline failed" in output.lower():
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                reason = next(
                    (line for line in lines if "uv trampoline failed" in line.lower()),
                    lines[0] if lines else "",
                )
                failure = reason[:200] or f"graphify --help exited {completed.returncode}"
                return GraphMode.BROKEN, failure
            return GraphMode.CLI, "graphify --help succeeded"
        if workspace.is_dir("graphify-out"):
            return GraphMode.BROKEN, "graphify-out/ present but graphify is not on PATH"
        root_key = str(workspace.root)
        sources = (
            (".mcp.json", _load_json(workspace.read_text(".mcp.json")), None),
            (
                ".claude/settings.json",
                _load_json(workspace.read_text(".claude/settings.json")),
                None,
            ),
            ("~/.claude.json", _load_json(self._home.read_text(".claude.json")), root_key),
        )
        for label, document, project_key in sources:
            match = matches_graph_server(_mcp_candidates(document, project_key))
            if match is not None:
                return GraphMode.MCP, f"{match} in {label}"
        return GraphMode.NONE, ""

    def _forge_state(self, state: StateRead) -> ForgeState:
        workspace = self._workspace
        agents = tuple(
            name for name in workspace.list_names(".claude/agents") if name.endswith(".md")
        )
        artifacts = any(workspace.exists(path) for path in FORGE_ARTIFACTS)
        ground_truth = any(
            name.startswith("GROUND_TRUTH") and name.endswith(".md")
            for name in workspace.list_names("docs")
        )
        completed = state.state is not None and state.state.complete
        return forge_state(agents, artifacts, ground_truth, completed)

    def run(self, with_engines: bool = True) -> Detection:
        workspace = self._workspace
        scan = workspace.scan(self._exclusions)
        texts: dict[str, str] = {}
        for name in (*MANIFEST_ORDER, *LOCKFILES):
            text = workspace.read_text(name)
            if text is not None:
                texts[name] = text
        present = frozenset(
            name for name in (*PRESENCE_PROBES, *MANIFEST_ORDER) if workspace.exists(name)
        )
        files = ProjectFiles(
            texts=texts,
            present=present,
            test_files=scan.test_files,
            entry_candidates=scan.entry_candidates,
            directory_name=workspace.root.name,
        )
        stack, name = detect_stack(files)
        state = read_forge_state(workspace)
        mode, evidence = self.graph_mode()
        return Detection(
            root=str(workspace.root),
            project_name=name,
            stack=stack,
            file_count=scan.file_count,
            size_tier=size_tier(scan.file_count),
            docs_state=docs_state(workspace.exists("CLAUDE.md"), len(scan.nested_claude_md)),
            forge_state=self._forge_state(state),
            verify=verify_tier(stack.signals),
            graph_mode=mode,
            graph_evidence=evidence,
            vcs=workspace.is_dir(".git"),
            engines=probe_engines(self._runner, self._engine_names) if with_engines else (),
            run_note=run_note(state.state, state.problem),
        )
