from __future__ import annotations

import math
from types import SimpleNamespace
from typing import cast

import pytest

from cuanta.adapters.storage.memory_ledger import MemoryLedger
from cuanta.application.instinct import DecisionMaker
from cuanta.application.routing import RouteAdvisor, RoutePlan, without_role
from cuanta.domain.agents import READ_ONLY_ROLES, role_of, scout_definition, with_scout
from cuanta.domain.config import DEFAULT_SCOUT_THRESHOLD, Config, layer_from_table, merge
from cuanta.domain.mandate import MandateRequest, Shape
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.role_budgets import ROLE_FLOORS, role_split
from cuanta.domain.routing import (
    ALL_ROLES,
    DEFAULT_ROLE_TIERS,
    PRESET_ROLES,
    ROLES,
    SCOUT_ROLES,
    Preset,
    Role,
    RoleRoute,
    RouteMode,
    RoutingPolicy,
    apply_preset,
    default_requests,
    depth_capped,
    plan_route,
)
from cuanta.domain.scout import (
    SCOUT_AGENT,
    SCOUT_TOOLS,
    DocsMode,
    DocsReason,
    ScoutMode,
    ShapeChoice,
    asks_for_docs,
    choose_shape,
    default_shape,
    docs_choice,
    eligible,
    exploration_share,
    parse_docs_mode,
    parse_forced_shape,
    parse_scout_mode,
    scout_refused,
    scout_session_block,
)

CATALOG = (
    ModelEntry("claude", "haiku", "claude-haiku-4-5", "anthropic", tier=Tier.ECONOMY),
    ModelEntry("claude", "sonnet", "claude-sonnet-5", "anthropic", tier=Tier.STANDARD),
    ModelEntry("claude", "opus", "claude-opus-5-5", "anthropic", tier=Tier.PREMIUM),
    ModelEntry("codex", "gpt-6-luna", "gpt-6-luna", "openai", tier=Tier.ECONOMY),
    ModelEntry("codex", "gpt-6-sol", "gpt-6-sol", "openai", tier=Tier.STANDARD),
)
DEFAULTS = {
    "claude": {Tier.ECONOMY: "haiku", Tier.STANDARD: "sonnet", Tier.PREMIUM: "opus"},
    "codex": {Tier.ECONOMY: "gpt-6-luna", Tier.STANDARD: "gpt-6-sol", Tier.PREMIUM: "gpt-6-sol"},
}


def request(what: str, where: str = "", tests: str = "", out_of_scope: str = "") -> MandateRequest:
    return MandateRequest("feature", what, "why", where, tests=tests, out_of_scope=out_of_scope)


def advisor() -> RouteAdvisor:
    decisions = cast("DecisionMaker", SimpleNamespace())
    return RouteAdvisor(decisions, lambda: CATALOG, MemoryLedger(), lambda: "")


def team_plan(engine: str, pins: dict[Role, str] | None = None) -> RoutePlan:
    policy = RoutingPolicy(
        mode=RouteMode.FIXED,
        engines=(engine,),
        tier_defaults=DEFAULTS,
        role_models=pins or {},
    )
    requests = default_requests(policy, ROLES)
    return RoutePlan(
        policy, None, None, requests, plan_route(policy, CATALOG, requests), "heuristic"
    )


def scouted(plan: RoutePlan) -> RoutePlan:
    return advisor().scout_plan(plan)


def test_the_scout_joins_the_roles_on_the_economy_tier_without_changing_the_legacy_team() -> None:
    assert Role.SCOUT not in ROLES
    assert ROLES == (Role.ORCHESTRATOR, Role.ANALYST, Role.SENIOR, Role.TESTER, Role.DOCS)
    assert SCOUT_ROLES == (Role.ORCHESTRATOR, Role.SCOUT, Role.SENIOR, Role.TESTER, Role.DOCS)
    assert (*ROLES, Role.SCOUT) == ALL_ROLES
    assert DEFAULT_ROLE_TIERS[Role.SCOUT] is Tier.ECONOMY
    assert PRESET_ROLES[Preset.SAVE][Role.SCOUT] is Tier.ECONOMY
    assert PRESET_ROLES[Preset.BEST][Role.SCOUT] is Tier.STANDARD
    assert RoutingPolicy().tier_for(Role.SCOUT) is Tier.ECONOMY
    assert apply_preset(RoutingPolicy(), Preset.SAVE).caps.ceiling(Role.SCOUT) is Tier.STANDARD
    capped = depth_capped(RoutingPolicy(roles={Role.SCOUT: Tier.PREMIUM}), Tier.STANDARD)
    assert capped.tier_for(Role.SCOUT) is Tier.STANDARD
    assert ROLE_FLOORS[Role.SCOUT] == ROLE_FLOORS[Role.ANALYST]
    assert english(msg("role.scout")) == "Scout"


@pytest.mark.parametrize(("engine", "model"), [("claude", "haiku"), ("codex", "gpt-6-luna")])
def test_the_scout_plan_swaps_the_analyst_for_an_economy_scout_in_place(
    engine: str, model: str
) -> None:
    plan = scouted(team_plan(engine))
    assert [route.role for route in plan.routes] == list(SCOUT_ROLES)
    assert [item.role for item in plan.requests] == list(SCOUT_ROLES)
    scout = plan.route(Role.SCOUT)
    assert scout is not None and scout.model is not None
    assert (scout.model.id, scout.tier) == (model, Tier.ECONOMY)
    assert english(scout.reason) == (
        "Scout on economy because your policy uses economy for exploration"
    )
    assert scouted(plan) is plan


def test_a_scout_pin_is_honored_and_routing_off_leaves_the_scout_without_a_model() -> None:
    pinned = scouted(team_plan("claude", {Role.SCOUT: "sonnet"}))
    scout = pinned.route(Role.SCOUT)
    assert scout is not None and scout.model is not None and scout.model.id == "sonnet"
    assert scout.reason.key == "route.pinned"
    off = RoutePlan(
        RoutingPolicy(mode=RouteMode.OFF),
        None,
        None,
        (),
        tuple(RoleRoute(role, Tier.STANDARD, None, None, msg("route.off")) for role in ROLES),
        "heuristic",
    )
    found = scouted(off).route(Role.SCOUT)
    assert found is not None and found.model is None
    assert without_role(pinned, Role.DOCS).route(Role.DOCS) is None
    assert without_role(pinned, Role.ANALYST) is pinned


def test_the_builtin_scout_agent_is_read_only_and_added_once() -> None:
    definition = scout_definition()
    assert definition.name == SCOUT_AGENT
    assert role_of(SCOUT_AGENT) is Role.SCOUT
    assert definition.fields["tools"] == list(SCOUT_TOOLS)
    assert "Edit" not in SCOUT_TOOLS and "Write" not in SCOUT_TOOLS
    assert "evidence pack" in definition.prompt and "edit_set" in definition.prompt
    assert "You are the scout" not in definition.prompt
    assert Role.SCOUT in READ_ONLY_ROLES
    added = with_scout(())
    assert [item.name for item in added] == [SCOUT_AGENT]
    assert with_scout(added) == added


@pytest.mark.parametrize(
    "what",
    [
        "Update the README with the new flag",
        "Add a CHANGELOG entry",
        "Escribe la documentación del endpoint",
        "Documentar la API de pagos",
        "Actualiza la guía de instalación",
        "Write docs for the checkout",
        "Refresh the user guide",
    ],
)
def test_the_docs_rule_turns_docs_on_when_the_request_asks_for_them(what: str) -> None:
    assert asks_for_docs(request(what))
    choice = docs_choice(DocsMode.AUTO, request(what), False)
    assert (choice.on, choice.reason) == (True, DocsReason.REQUESTED)


@pytest.mark.parametrize(
    ("what", "where", "out_of_scope"),
    [
        ("Fix the rounding of totals", "src/cart.ts", "Documentation"),
        ("Use document.querySelector for the button", "", "README"),
        ("Arregla el cálculo del IVA", "src/iva.ts", "docs"),
        ("Add a documentary video section", "", ""),
    ],
)
def test_the_docs_rule_keeps_docs_off_when_the_request_does_not_ask(
    what: str, where: str, out_of_scope: str
) -> None:
    asked = request(what, where, out_of_scope=out_of_scope)
    assert not asks_for_docs(asked)
    choice = docs_choice(DocsMode.AUTO, asked, False)
    assert (choice.on, choice.reason) == (False, DocsReason.NOT_REQUESTED)
    assert english(choice.message) == "Docs: off, the request does not ask for docs"


def test_docs_are_off_in_trials_unless_forced_and_the_mode_parses() -> None:
    asked = request("Update the README", where="docs/README.md")
    assert asks_for_docs(request("Tweak", where="docs/setup.md"))
    assert asks_for_docs(request("Tweak", tests="the CHANGELOG lists it"))
    trial = docs_choice(DocsMode.AUTO, asked, True)
    assert (trial.on, trial.reason) == (False, DocsReason.TRIAL)
    assert docs_choice(DocsMode.ON, request("x"), True).reason is DocsReason.FORCED_ON
    assert not docs_choice(DocsMode.OFF, asked, False).on
    assert parse_docs_mode(" ON ") is DocsMode.ON
    assert parse_docs_mode("maybe") is DocsMode.AUTO
    assert english(msg("docs.trial")) == "Docs: off in trials"


def test_the_docs_share_funds_the_repair_turn_when_docs_is_off() -> None:
    costs: dict[Role, float | None] = {
        Role.ANALYST: 1.0,
        Role.SENIOR: 2.0,
        Role.TESTER: 1.0,
        Role.DOCS: 0.5,
    }
    plain = role_split(costs, 1.0, fix=False)
    assert plain.repair_usd == 0.0 and Role.DOCS in plain.shares
    off = role_split(costs, 1.0, fix=False, docs_off=True)
    assert off.from_docs and Role.DOCS not in off.shares
    assert off.repair_usd > 0
    assert math.isclose(sum(off.shares.values()) + off.repair_usd, 1.0)
    without: dict[Role, float | None] = {
        role: cost for role, cost in costs.items() if role is not Role.DOCS
    }
    fixed = role_split(without, 1.0, fix=True)
    assert role_split(costs, 1.0, fix=True, docs_off=True) == fixed


@pytest.mark.parametrize("task_type", ["feature", "bug", "fix"])
def test_the_default_shape_rule_picks_the_scout_above_the_threshold(task_type: str) -> None:
    assert eligible(task_type)
    assert default_shape(task_type, 0.36) is Shape.SCOUT
    assert default_shape(task_type, DEFAULT_SCOUT_THRESHOLD) is Shape.PIPELINE
    assert default_shape(task_type, 0.2) is Shape.PIPELINE
    assert default_shape(task_type, 0.5, threshold=0.6) is Shape.PIPELINE
    assert default_shape(task_type, None) is Shape.PIPELINE
    assert default_shape(task_type, math.nan) is Shape.PIPELINE


@pytest.mark.parametrize("task_type", ["refactor", "investigation"])
def test_the_default_shape_rule_leaves_other_types_on_the_pipeline(task_type: str) -> None:
    assert not eligible(task_type)
    assert default_shape(task_type, 0.9) is Shape.PIPELINE
    assert not eligible("feature", simple=True)


def test_a_forced_shape_wins_and_the_choice_explains_itself() -> None:
    assert parse_forced_shape("scout") is Shape.SCOUT
    assert parse_forced_shape("pipeline") is Shape.PIPELINE
    assert parse_forced_shape("single") is None
    assert parse_forced_shape("") is None
    forced = choose_shape("refactor", Shape.SCOUT, None, mode=ScoutMode.LAUNCH)
    assert forced.scout and forced.forced and forced.launch
    assert english(forced.message or msg("x")) == (
        "Shape: scout and senior (forced); the scout runs as its own read-only launch"
    )
    auto = choose_shape("feature", None, 0.42)
    assert auto.scout and not auto.forced and not auto.launch
    assert english(auto.message or msg("x")) == (
        "Shape: scout and senior, because exploration is 42% of the forecast, above 35%; "
        "the scout runs as a subagent in the session"
    )
    assert choose_shape("feature", Shape.PIPELINE, 0.9).shape is Shape.PIPELINE
    assert choose_shape("feature", None, 0.1).message is None
    assert ShapeChoice(Shape.SCOUT).payload()["scout_mode"] == "native"
    assert ShapeChoice(Shape.PIPELINE).payload()["scout_mode"] is None
    assert scout_refused("investigation", Shape.SCOUT)
    assert not scout_refused("feature", Shape.SCOUT)
    assert exploration_share(35, 100) == 0.35 and exploration_share(1, 0) is None
    assert parse_scout_mode("LAUNCH") is ScoutMode.LAUNCH
    assert parse_scout_mode("elsewhere") is ScoutMode.NATIVE


def test_the_session_block_tells_the_main_agent_to_pass_only_the_pack() -> None:
    on = scout_session_block(True)
    assert "Invoke the `scout` agent once, first" in on
    assert "only the scout's evidence pack, copied verbatim" in on
    assert "cuanta test --affected" in on
    assert "Invoke the docs-updater last" in on
    off = scout_session_block(False)
    assert "Docs are off for this run: do not invoke the docs-updater." in off


def test_the_scout_and_docs_keys_parse_and_reject_bad_values() -> None:
    table = {"runs": {"scout_mode": "launch", "scout_threshold": 0.5, "docs": "on"}}
    config = merge([layer_from_table(table)])
    assert (config.scout_mode, config.scout_threshold, config.docs_mode) == ("launch", 0.5, "on")
    bad = {"runs": {"scout_mode": "remote", "scout_threshold": 1.5, "docs": "sometimes"}}
    kept = merge([layer_from_table(bad)])
    assert (kept.scout_mode, kept.scout_threshold, kept.docs_mode) == (
        Config().scout_mode,
        DEFAULT_SCOUT_THRESHOLD,
        "auto",
    )
    zero = merge([layer_from_table({"runs": {"scout_threshold": 0}})])
    assert zero.scout_threshold == DEFAULT_SCOUT_THRESHOLD
    assert Config().scout_mode == "native"
