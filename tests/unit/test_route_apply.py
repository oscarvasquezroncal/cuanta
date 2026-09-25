from __future__ import annotations

import json
from pathlib import Path

from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.workspace import LocalHome, LocalWorkspace
from cuanta.application.detect import DetectProject
from cuanta.application.instinct import DecisionMaker
from cuanta.application.route_apply import MandateRouting, RouteOptions
from cuanta.application.routing import RouteAdvisor
from cuanta.domain.detection import GraphMode
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.models import ModelEntry, Tier, TierSource
from cuanta.domain.routing import RoutingPolicy
from cuanta.ports.system import Completed
from tests.fakes import FakeRunner

NOW = "2026-09-23T00:00:00Z"
CATALOG = tuple(
    ModelEntry(engine, name, name, "p", resolved=resolved, tier=tier, tier_source=TierSource.ANCHOR)
    for engine, name, resolved, tier in (
        ("claude", "haiku", "claude-haiku-4-5", Tier.ECONOMY),
        ("claude", "sonnet", "claude-sonnet-5", Tier.STANDARD),
        ("claude", "opus", "claude-opus-5-5", Tier.PREMIUM),
        ("codex", "gpt-5.6-luna", "gpt-5.6-luna", Tier.ECONOMY),
        ("codex", "gpt-5.6-sol", "gpt-5.6-sol", Tier.PREMIUM),
    )
)
AGENT = "---\nname: {name}\ndescription: d\ntools: Read\nmodel: sonnet\n---\nBody of {name}.\n"
REQUEST = MandateRequest(type="feature", what="add export", why="users ask", out_of_scope="ui")


def routing(root: Path, environ: dict[str, str]) -> MandateRouting:
    ledger = MemoryLedger()
    decisions = DecisionMaker(HeuristicInstinct(), ledger, lambda: NOW)
    advisor = RouteAdvisor(decisions, lambda: CATALOG, ledger, lambda: NOW)
    return MandateRouting(
        advisor=advisor,
        policy=RoutingPolicy,
        workspace=LocalWorkspace(root),
        ledger=ledger,
        environ=environ,
        settings_env=dict,
        blast_radius=lambda text: 0,
        clock_iso=lambda: NOW,
    )


def test_claude_routes_write_an_agents_file(tmp_path: Path) -> None:
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name in ("architecture-analyst", "python-senior", "tester", "docs-updater", "helper"):
        (agents / f"{name}.md").write_text(AGENT.format(name=name), encoding="utf-8")
    applied = routing(tmp_path, {}).apply(REQUEST, RouteOptions(mode="fixed"), "claude")
    assert applied.orchestrator == "claude-sonnet-5"
    written = json.loads(Path(applied.agents_file).read_text(encoding="utf-8"))
    assert set(written) == {"architecture-analyst", "python-senior", "tester", "docs-updater"}
    assert written["python-senior"]["model"] == "claude-opus-5-5"
    assert written["docs-updater"]["model"] == "claude-haiku-4-5"
    assert written["tester"]["prompt"] == "Body of tester."
    planned = applied.planned()
    assert planned["main"] == ("orchestrator", "claude-sonnet-5")


def test_broken_graph_runner_scrubs_runtime_agents_without_editing_source(tmp_path: Path) -> None:
    source = (
        "---\nname: architecture-analyst\ndescription: graphify analyst\n"
        "tools: Read, Grep, Glob, Bash(graphify *)\nmodel: sonnet\n"
        "initialPrompt: Run graphify query before reading source.\n---\n"
        '## Output contract\n\nReturn ONLY this JSON: {"status": "ok"}.\n\n'
        "## Navigation\n\nRun graphify query for every symbol.\n\n"
        "Cite source files for each finding.\n"
    )
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    original = agents / "architecture-analyst.md"
    original.write_text(source, encoding="utf-8")
    runner = FakeRunner(
        binaries={"graphify": "/bin/graphify"},
        responses={"graphify --help": Completed(1, "", "uv trampoline failed")},
    )
    detector = DetectProject(LocalWorkspace(tmp_path), LocalHome(tmp_path / "home"), runner)
    mode, _ = detector.graph_mode()
    assert mode is GraphMode.BROKEN
    applied = routing(tmp_path, {}).apply(
        REQUEST, RouteOptions(mode="fixed"), "claude", graph_available=False
    )
    written = json.loads(Path(applied.agents_file).read_text(encoding="utf-8"))
    analyst = written["architecture-analyst"]
    assert "graphify" not in json.dumps(analyst).lower()
    assert "Return ONLY this JSON" in analyst["prompt"]
    assert "Cite source files" in analyst["prompt"]
    assert "Bash(graphify *)" not in analyst["tools"]
    assert original.read_text(encoding="utf-8") == source


def test_other_engines_get_a_single_model(tmp_path: Path) -> None:
    applied = routing(tmp_path, {}).apply(REQUEST, RouteOptions(mode="fixed"), "codex")
    assert applied.single == "gpt-5.6-sol"
    assert applied.agents_file == ""
    assert applied.planned() == {"main": ("single", "gpt-5.6-sol")}


def test_subagent_env_override_is_reported_and_optionally_unset(tmp_path: Path) -> None:
    environ = {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}
    kept = routing(tmp_path, environ).apply(REQUEST, RouteOptions(mode="fixed"), "claude")
    assert kept.env_override == "CLAUDE_CODE_SUBAGENT_MODEL"
    assert kept.unset == ()
    dropped = routing(tmp_path, environ).apply(
        REQUEST, RouteOptions(mode="fixed", keep_env_model=False), "claude"
    )
    assert dropped.unset == ("CLAUDE_CODE_SUBAGENT_MODEL",)


def test_routing_off_changes_nothing(tmp_path: Path) -> None:
    applied = routing(tmp_path, {}).apply(REQUEST, RouteOptions(mode="off"), "claude")
    assert not applied.active
    assert (applied.agents_file, applied.orchestrator, applied.single) == ("", "", "")
