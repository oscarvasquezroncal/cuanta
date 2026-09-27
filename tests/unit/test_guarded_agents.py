from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.application.mandate_flow import MandateFlow, MandateOptions
from cuanta.application.route_apply import RouteOptions
from cuanta.domain.agents import AgentsPlan, guarded_agents
from cuanta.domain.change_plan import EXECUTION, WRITERS, ChangePlan, strict_tools
from cuanta.domain.detection import Stack
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.routing import Role
from tests.fakes import FakeRunner
from tests.unit.test_engine_profiles import flow, launcher
from tests.unit.test_route_apply import routing


def dangerous_agents() -> AgentsPlan:
    spec: dict[str, object] = {
        "description": "specialist",
        "prompt": "Complete your role",
        "model": "claude-sonnet-5",
        "tools": ["Read", "Edit", "Write", "Bash", "PowerShell", "mcp__external__run"],
        "disallowedTools": ["Read(secrets/**)"],
        "permissionMode": "bypassPermissions",
        "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "writer"}]}]},
        "skills": ["external-execution"],
        "mcpServers": ["external"],
    }
    return AgentsPlan(
        {"architecture-analyst": spec, "python-senior": dict(spec), "tester": dict(spec)},
        {"architecture-analyst": Role.ANALYST, "python-senior": Role.SENIOR, "tester": Role.TESTER},
    )


@pytest.mark.parametrize("readonly", [False, True])
def test_guarded_agent_json_preserves_denies_and_removes_execution_overrides(
    readonly: bool,
) -> None:
    original = dangerous_agents()
    plan = ChangePlan(guard=("src/hero/**",), read_only=readonly)
    protected = guarded_agents(original, plan)
    written = json.loads(protected.to_json())
    for name, spec in written.items():
        analyst = name == "architecture-analyst"
        assert spec["tools"] == (["Read"] if readonly or analyst else ["Read", "Edit", "Write"])
        assert "Read(secrets/**)" in spec["disallowedTools"]
        assert set(EXECUTION) <= set(spec["disallowedTools"])
        assert not {"hooks", "skills", "mcpServers", "permissionMode"} & spec.keys()
        if readonly or analyst:
            assert set(WRITERS) <= set(spec["disallowedTools"])
        else:
            assert {f"{writer}(src/hero/**)" for writer in WRITERS} <= set(spec["disallowedTools"])
        assert spec["model"] == "claude-sonnet-5"
    assert original.agents["python-senior"]["permissionMode"] == "bypassPermissions"
    assert protected.roles == original.roles


def test_inherited_agent_tools_resolve_to_the_strict_allowlist() -> None:
    original = AgentsPlan({"tester": {"prompt": "Test"}}, {"tester": Role.TESTER})
    plan = ChangePlan(read_only=True)
    protected = guarded_agents(original, plan)
    assert protected.agents["tester"]["tools"] == list(strict_tools(plan))
    assert guarded_agents(original, ChangePlan()) is original


def test_routing_protection_writes_stable_guarded_json_without_changing_source(
    tmp_path: Path,
) -> None:
    folder = tmp_path / ".claude" / "agents"
    folder.mkdir(parents=True)
    source = (
        "---\nname: python-senior\ndescription: senior\ntools: Read, Write, Bash\n"
        "permissionMode: bypassPermissions\nskills: external-execution\n---\nFinish the request.\n"
    )
    path = folder / "python-senior.md"
    path.write_text(source, encoding="utf-8")
    advisor = routing(tmp_path, {})
    request = MandateRequest(
        type="feature", what="add export", tests="export test", out_of_scope="hero"
    )
    applied = advisor.apply(request, RouteOptions(mode="fixed"), "claude")
    plan = ChangePlan(guard=("src/hero/**",))
    protected = advisor.protect(applied, plan)
    assert protected.agents_file != applied.agents_file
    assert advisor.protect(applied, plan).agents_file == protected.agents_file
    written = json.loads(Path(protected.agents_file).read_text(encoding="utf-8"))
    assert written["python-senior"]["tools"] == ["Read", "Write"]
    assert "Edit(src/hero/**)" in written["python-senior"]["disallowedTools"]
    assert "permissionMode" not in written["python-senior"]
    assert path.read_text(encoding="utf-8") == source
    assert advisor.protect(applied, ChangePlan()) is applied


def test_native_prepare_compiles_once_and_passes_the_same_plan_to_guarded_agents(
    tmp_path: Path,
) -> None:
    folder = tmp_path / ".claude" / "agents"
    folder.mkdir(parents=True)
    for name in ("architecture-analyst", "python-senior"):
        (folder / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: specialist\ntools: Read, Write, Bash\n---\nFinish.\n",
            encoding="utf-8",
        )
    engine = ClaudeCodeEngine(FakeRunner())
    calls: list[MandateRequest] = []
    plan = ChangePlan(guard=("src/hero/**",))

    def compile_plan(request: MandateRequest) -> ChangePlan:
        calls.append(request)
        return plan

    service = MandateFlow(
        flow(tmp_path, engine).service,
        lambda _: engine,
        launcher,
        Stack,
        lambda _: ({}, None),
        str(tmp_path),
        "claude",
        0.0,
        routing=routing(tmp_path, {}),
        change_plan=compile_plan,
    )
    request = MandateRequest(
        type="feature", what="add export", tests="export test", out_of_scope="hero"
    )
    prepared = service.prepare(
        request, 0, MandateOptions(route=RouteOptions(mode="fixed")), preview=True
    )
    assert calls == [request]
    assert prepared.spec.change_plan is plan
    command = prepared.composed.command
    agent_path = command[command.index("--agents") + 1]
    assert agent_path == prepared.spec.agents_file
    written = json.loads(Path(agent_path).read_text(encoding="utf-8"))
    assert written["architecture-analyst"]["tools"] == ["Read"]
    assert written["python-senior"]["tools"] == ["Read", "Write"]
    assert "Bash" in written["python-senior"]["disallowedTools"]
