from __future__ import annotations

import json
from pathlib import Path

from cuanta.adapters.engines.claude_code import ClaudeCodeEngine
from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.cross_engine import request_block
from cuanta.application.mandate_flow import MandateFlow, MandateOptions, preview_of
from cuanta.application.route_apply import MandateRouting, RouteOptions
from cuanta.application.routing import RoutePlan
from cuanta.cli.commands.mandate import team_lines
from cuanta.domain.audit import AuditStatus
from cuanta.domain.detection import Stack
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.messages import msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.routing import ROLES, Role, RoleRoute, RoutingPolicy
from cuanta.tui.services import role_preview
from tests.fakes import FakeRunner
from tests.real_run import REAL_MODEL
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


def claude_team(root: Path, subject: MandateRouting | None = None) -> MandateFlow:
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
        routing=subject if subject is not None else routing(root, {}),
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


def test_a_pure_team_plans_its_model_for_every_role_and_audits_clean(tmp_path: Path) -> None:
    ledger = MemoryLedger()
    subject = routing(tmp_path, {}, ledger)
    service = claude_team(tmp_path, subject)
    options = MandateOptions(
        profile="balanced",
        model=REAL_MODEL,
        variant="ultracode",
        pure=True,
        shape="scout",
        route=RouteOptions(mode="fixed", preset="best"),
    )
    prepared = service.prepare(FEATURE, 0, options, preview=True)
    applied = prepared.applied
    assert applied is not None
    routed = [route for route in applied.plan.routes if route.model is not None]
    assert Role.SCOUT in {route.role for route in routed}
    assert {route.model.resolved for route in routed if route.model is not None} == {REAL_MODEL}
    assert {route.reason.key for route in routed} == {"route.pure"}
    agents = json.loads(Path(prepared.spec.agents_file).read_text(encoding="utf-8"))
    assert "scout" in agents
    assert {spec["model"] for spec in agents.values()} == {REAL_MODEL}
    assert prepared.spec.pure and prepared.spec.model == REAL_MODEL
    assert prepared.spec.unset_env == ()
    lines = team_lines(prepared)
    assert f"team · scout → opus (premium) · pure: --pure runs every role on {REAL_MODEL}" in lines
    assert not any("because your policy uses" in line for line in lines)
    ledger.add_events(
        [
            LedgerEvent(
                run_id="R",
                agent="scout",
                kind="api_request",
                model=REAL_MODEL,
                ts="2026-10-02T16:12:00Z",
            ),
            LedgerEvent(
                run_id="R", kind="api_request", model=REAL_MODEL, ts="2026-10-02T16:12:01Z"
            ),
        ]
    )
    rows = {row.agent: row for row in subject.audit("R", applied, ("scout",))}
    assert rows["scout"].status is AuditStatus.MATCH and rows["main"].ok
    assert not [row for row in rows.values() if row.status is AuditStatus.MISMATCH]
    subject.record("R", "feature", applied)
    assert ledger.routing_decisions() == ()
