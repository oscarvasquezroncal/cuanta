from __future__ import annotations

from pathlib import Path

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.application.cross_engine import request_block
from cuanta.application.mandate_flow import MandateFlow, MandateOptions, preview_of
from cuanta.application.route_apply import RouteOptions
from cuanta.application.routing import RoutePlan
from cuanta.domain.detection import Stack
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy
from cuanta.tui.services import role_preview
from tests.fakes import FakeRunner
from tests.unit.test_engine_profiles import flow, launcher
from tests.unit.test_route_apply import AGENT, routing

FEATURE = MandateRequest(
    "feature", "Extend checkout", "users ask", tests="totals stay right", out_of_scope="ui"
)
GPT_TEAM = {
    Role.ORCHESTRATOR: ("gpt-6-sol", Tier.STANDARD),
    Role.ANALYST: ("gpt-6-sol", Tier.STANDARD),
    Role.SENIOR: ("gpt-6-sol", Tier.PREMIUM),
    Role.TESTER: ("gpt-6-sol", Tier.STANDARD),
}


def claude_team(root: Path) -> MandateFlow:
    folder = root / ".claude" / "agents"
    folder.mkdir(parents=True)
    for name in ("architecture-analyst", "python-senior", "tester", "docs-updater"):
        (folder / f"{name}.md").write_text(AGENT.format(name=name), encoding="utf-8")
    engine = ClaudeCodeEngine(FakeRunner())
    return MandateFlow(
        flow(root, engine).service,
        lambda _: engine,
        launcher,
        Stack,
        lambda _: ({}, None),
        str(root),
        "claude",
        0.0,
        routing=routing(root, {}),
    )


def test_the_claude_team_preview_shows_each_role_model(tmp_path: Path) -> None:
    service = claude_team(tmp_path)
    options = MandateOptions(route=RouteOptions(mode="fixed"))
    shown = preview_of(service.prepare(FEATURE, 0, options, preview=True))
    assert not shown.per_role
    assert shown.command.startswith("claude -p")
    assert {route.role: route.model.id for route in shown.roles if route.model} == {
        Role.ORCHESTRATOR: "sonnet",
        Role.ANALYST: "sonnet",
        Role.SENIOR: "opus",
        Role.TESTER: "sonnet",
        Role.DOCS: "haiku",
    }


def test_a_single_session_preview_lists_no_roles(tmp_path: Path) -> None:
    service = claude_team(tmp_path)
    investigation = MandateRequest(
        "investigation", "Explain the cart", "onboarding", tests="report", out_of_scope="code"
    )
    routed = MandateOptions(route=RouteOptions(mode="fixed"))
    assert preview_of(service.prepare(investigation, 0, routed, preview=True)).roles == ()
    off = MandateOptions(route=RouteOptions(mode="off"))
    assert preview_of(service.prepare(FEATURE, 0, off, preview=True)).roles == ()


def test_the_gpt_team_preview_lists_one_model_per_launched_role() -> None:
    routes = tuple(
        RoleRoute(
            role,
            GPT_TEAM[role][1] if role in GPT_TEAM else Tier.ECONOMY,
            GPT_TEAM[role][1] if role in GPT_TEAM else None,
            ModelEntry("codex", GPT_TEAM[role][0], GPT_TEAM[role][0], "openai")
            if role in GPT_TEAM
            else None,
            msg("route.policy", tier="standard"),
        )
        for role in ROLES
    )
    plan = RoutePlan(RoutingPolicy(engines=("codex",)), None, None, (), routes, "heuristic")
    shown = role_preview(FEATURE, plan, "codex")
    assert shown.per_role
    assert shown.engine == "codex"
    assert shown.command == ""
    assert shown.prompt == request_block(FEATURE)
    assert [(route.role, route.model.id if route.model else "") for route in shown.roles] == [
        (Role.ANALYST, "gpt-6-sol"),
        (Role.SENIOR, "gpt-6-sol"),
        (Role.TESTER, "gpt-6-sol"),
    ]
