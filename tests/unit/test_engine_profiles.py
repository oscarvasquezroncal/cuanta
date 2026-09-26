from __future__ import annotations

from itertools import count
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.engines.codex import CodexEngine
from cuanta.adapters.engines.opencode import OpenCodeEngine
from cuanta.adapters.instinct.heuristic import HeuristicInstinct
from cuanta.adapters.storage.capsule_store import FileCapsuleStore
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.adapters.system.clock import FixedClock
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.cross_engine import CROSS_ORDER, CrossEnginePipeline
from cuanta.application.engine_run import EngineLauncher
from cuanta.application.instinct import DecisionMaker
from cuanta.application.mandate import MandateService
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.progress import RecordingSink
from cuanta.application.routing import RoutePlan
from cuanta.domain.detection import Stack
from cuanta.domain.errors import DomainFailure
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.routing import Role, RoleRoute, RoutingPolicy
from cuanta.ports.engine import Engine
from tests.fakes import FakeRunner, FakeStream

REQUEST = MandateRequest(type="bug", what="fix add", why="incorrect sum", out_of_scope="docs")
TEMPLATE = "```\n=== REQUEST ===\n```\n"


def launcher(engine: Engine) -> EngineLauncher:
    ids = count(1)
    return EngineLauncher(
        engine,
        MemoryLedger(),
        FixedClock(),
        lambda: f"RUN{next(ids)}",
        lambda size: b"\x01" * size,
        "project",
        4318,
        None,
    )


def flow(root: Path, engine: Engine) -> MandateFlow:
    workspace = LocalWorkspace(root)
    workspace.write_text("docs/MANDATE_TEMPLATE.md", TEMPLATE)
    ledger = MemoryLedger()
    clock = FixedClock()
    decisions = DecisionMaker(HeuristicInstinct(), ledger, clock.now_iso)
    service = MandateService(workspace, ledger, decisions, clock.now_iso)
    return MandateFlow(
        service,
        lambda _: engine,
        launcher,
        Stack,
        lambda _: ({}, None),
        str(root),
        engine.name,
        0.0,
    )


@pytest.mark.parametrize("kind", ["bug", "investigation"])
@pytest.mark.parametrize("shape", ["", "single", "pipeline"])
@pytest.mark.parametrize("simple", [False, True])
def test_mandate_shapes_reach_codex_with_explicit_sandbox(
    tmp_path: Path, kind: str, shape: str, simple: bool
) -> None:
    engine = CodexEngine(FakeRunner())
    request = MandateRequest(kind, "inspect addition", "incorrect sum", out_of_scope="docs")
    prepared = flow(tmp_path, engine).prepare(
        request, 0, MandateOptions(shape=shape, simple=simple)
    )
    expected = kind == "investigation"
    sent = prepared.launcher.request(prepared.spec, "R", "trace", None)
    assert sent.read_only is expected
    command = engine.command(sent)
    assert command[command.index("--sandbox") + 1] == (
        "read-only" if expected else "workspace-write"
    )
    assert not sent.temporary_copy
    assert "--skip-git-repo-check" not in command


@pytest.mark.parametrize("owned_copy", [False, True])
@pytest.mark.parametrize("git_directory", [False, True])
def test_only_the_owned_copy_marker_skips_git_validation(
    tmp_path: Path, owned_copy: bool, git_directory: bool
) -> None:
    root = tmp_path / "cuanta-bench-temporary-copy"
    root.mkdir()
    if git_directory:
        (root / ".git").mkdir()
    engine = CodexEngine(FakeRunner())
    prepared = flow(root, engine).prepare(
        REQUEST, 0, MandateOptions(simple=True, temporary_copy=owned_copy)
    )
    sent = prepared.launcher.request(prepared.spec, "R", "trace", None)
    assert sent.temporary_copy is owned_copy
    assert ("--skip-git-repo-check" in engine.command(sent)) is owned_copy


@pytest.mark.parametrize("shape", ["", "single", "pipeline"])
def test_opencode_investigations_are_refused_before_launch(tmp_path: Path, shape: str) -> None:
    runner = FakeRunner()
    engine = OpenCodeEngine(runner)
    request = MandateRequest(
        "investigation", "inspect addition", "why is sum wrong", out_of_scope="docs"
    )
    with pytest.raises(DomainFailure, match="OpenCode"):
        flow(tmp_path, engine).prepare(request, 0, MandateOptions(shape=shape))
    assert runner.calls == []


def all_roles(engine: str = "codex") -> RoutePlan:
    routes = tuple(
        RoleRoute(
            role,
            Tier.STANDARD,
            Tier.STANDARD,
            ModelEntry(engine, f"model-{role.value}", "model", "provider"),
            msg("route.policy", tier="standard"),
        )
        for role in CROSS_ORDER
    )
    return RoutePlan(RoutingPolicy(), None, None, (), routes, "heuristic")


@pytest.mark.parametrize("kind", ["bug", "investigation"])
def test_cross_roles_apply_codex_readonly_before_invoking_the_engine(
    tmp_path: Path, kind: str
) -> None:
    runner = FakeRunner(streams={"codex": FakeStream([])})
    engine_launcher = launcher(CodexEngine(runner))
    pipeline = CrossEnginePipeline(
        lambda _: engine_launcher,
        tuple,
        FileCapsuleStore(tmp_path),
        str(tmp_path),
        0.0,
        max_turns=1,
    )
    request = MandateRequest(kind, "inspect addition", "incorrect sum", out_of_scope="docs")
    report = pipeline.run(request, all_roles(), RecordingSink())
    assert report.ok
    assert tuple(step.role for step in report.steps) == CROSS_ORDER
    assert len(runner.calls) == len(CROSS_ORDER)
    for role, command in zip(CROSS_ORDER, runner.calls, strict=True):
        expected = (
            "read-only" if kind == "investigation" or role is Role.ANALYST else "workspace-write"
        )
        assert command[command.index("--sandbox") + 1] == expected
        assert "--skip-git-repo-check" not in command
        assert "--max-turns" not in command


def test_cross_opencode_analyst_is_refused_before_any_role_launch(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine_launcher = launcher(OpenCodeEngine(runner))
    pipeline = CrossEnginePipeline(
        lambda _: engine_launcher,
        tuple,
        FileCapsuleStore(tmp_path),
        str(tmp_path),
        0.0,
    )
    report = pipeline.run(REQUEST, all_roles("opencode"), RecordingSink())
    assert not report.ok
    assert report.steps == ()
    assert report.stopped == msg("guarantee.opencode_refused")
    assert runner.calls == []


@pytest.mark.parametrize("kind", ["bug", "investigation"])
def test_cross_claude_readonly_roles_cannot_inherit_write_tools(tmp_path: Path, kind: str) -> None:
    runner = FakeRunner(
        streams={"claude": FakeStream(['{"type":"result","subtype":"success","is_error":false}'])}
    )
    engine_launcher = launcher(ClaudeCodeEngine(runner))
    pipeline = CrossEnginePipeline(
        lambda _: engine_launcher,
        tuple,
        FileCapsuleStore(tmp_path),
        str(tmp_path),
        0.0,
    )
    request = MandateRequest(kind, "inspect addition", "incorrect sum", out_of_scope="docs")
    report = pipeline.run(request, all_roles("claude"), RecordingSink())
    assert report.ok
    assert len(runner.calls) == len(CROSS_ORDER)
    for role, command in zip(CROSS_ORDER, runner.calls, strict=True):
        assert command[command.index("--max-turns") + 1] == "40"
        allowed = command[command.index("--allowedTools") + 1].split(",")
        if kind == "investigation" or role is Role.ANALYST:
            assert "Write" not in allowed and "Edit" not in allowed
            assert command[command.index("--tools") + 1] == "Read,Grep,Glob"
        else:
            assert "Write" in allowed and "Edit" in allowed
