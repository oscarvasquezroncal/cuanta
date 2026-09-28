from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.models.tiers import load_tier_table
from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.cross_files import CodexFileGuard
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.config import Config
from cuanta.domain.depth import TIER_CAPS, Depth
from cuanta.domain.ledger import Run
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier, place, tier_defaults
from cuanta.domain.real_costs import attempts, report_attempts
from cuanta.domain.routing import (
    DEFAULT_ROLE_TIERS,
    Provider,
    Role,
    RoleRoute,
    RoutingPolicy,
    default_requests,
    depth_capped,
    parse_provider,
    pin_issues,
    plan_route,
)
from cuanta.domain.team import (
    ProviderAttempt,
    advice_message,
    provider_attempts,
    provider_title,
    recommend_provider,
    runs_per_role,
    team_cards,
)
from cuanta.tui.i18n import Catalog

CATALOG = (
    ModelEntry(
        "claude", "sonnet", "Sonnet", "anthropic", resolved="claude-sonnet-5", tier=Tier.STANDARD
    ),
    ModelEntry(
        "claude", "opus", "Opus", "anthropic", resolved="claude-opus-5-5", tier=Tier.PREMIUM
    ),
    ModelEntry("codex", "gpt-6-sol", "Sol", "openai", resolved="gpt-6-sol", tier=Tier.PREMIUM),
    ModelEntry(
        "codex", "gpt-5.6-terra", "Terra", "openai", resolved="gpt-5.6-terra", tier=Tier.STANDARD
    ),
)
LISTED = (
    ("claude", "haiku", "claude-haiku-4-5", 1.0),
    ("claude", "sonnet", "claude-sonnet-5", 3.0),
    ("claude", "opus", "claude-opus-5-5", 5.0),
    ("claude", "fable", "claude-fable-5-1", 15.0),
    ("codex", "gpt-6-luna", "gpt-6-luna", 0.5),
    ("codex", "gpt-6-sol", "gpt-6-sol", 2.0),
    ("codex", "gpt-6-astra", "gpt-6-astra", 12.0),
    ("codex", "gpt-5.6-luna", "gpt-5.6-luna", 0.3),
    ("codex", "gpt-5.6-terra", "gpt-5.6-terra", 1.5),
    ("codex", "gpt-5.6-sol", "gpt-5.6-sol", 4.0),
)
TABLE = load_tier_table()
PLACED = place(
    tuple(
        ModelEntry(engine, name, name, "p", input_price=price, resolved=resolved)
        for engine, name, resolved, price in LISTED
    ),
    TABLE,
    {},
)


def routes(policy: RoutingPolicy) -> tuple[RoleRoute, ...]:
    return plan_route(policy, CATALOG, default_requests(policy))


def team(provider: Provider, depth: Depth = Depth.NORMAL, **pins: str) -> dict[Role, str]:
    policy = RoutingPolicy(
        engines=(provider.value,),
        tier_defaults=TABLE.defaults,
        role_models={Role(role): model for role, model in pins.items()},
    )
    capped = depth_capped(policy, TIER_CAPS[depth])
    chosen = plan_route(capped, PLACED, default_requests(capped))
    return {route.role: route.model.id for route in chosen if route.model is not None}


def test_a_team_has_one_provider_and_claude_is_the_default() -> None:
    assert parse_provider(Config().engine) is Provider.CLAUDE
    assert parse_provider(" Codex ") is Provider.CODEX
    assert parse_provider("claude") is Provider.CLAUDE
    assert parse_provider("opencode") is None
    assert parse_provider("claude-plans-codex-writes") is None
    assert english(provider_title(Provider.CLAUDE)) == "Claude team"
    assert english(provider_title(Provider.CODEX)) == "GPT team"
    assert Catalog("es").message(provider_title(Provider.CLAUDE)) == "Equipo Claude"
    assert Catalog("es").message(provider_title(Provider.CODEX)) == "Equipo GPT"


def test_only_a_codex_pipeline_runs_one_launch_per_role() -> None:
    assert runs_per_role("codex", True)
    assert not runs_per_role("codex", False)
    assert not runs_per_role("claude", True)
    assert not runs_per_role("opencode", True)
    assert not runs_per_role("", True)


def test_role_tiers_follow_the_team_spec() -> None:
    assert DEFAULT_ROLE_TIERS[Role.DOCS] is Tier.ECONOMY
    assert DEFAULT_ROLE_TIERS[Role.ANALYST] is Tier.STANDARD
    assert DEFAULT_ROLE_TIERS[Role.TESTER] is Tier.STANDARD
    assert DEFAULT_ROLE_TIERS[Role.SENIOR] is Tier.PREMIUM
    assert TIER_CAPS[Depth.QUICK] is Tier.STANDARD


def test_the_shipped_tier_table_has_current_defaults_for_both_providers() -> None:
    assert TABLE.verified_on == "2026-09-27"
    assert TABLE.defaults == {
        "claude": {Tier.ECONOMY: "haiku", Tier.STANDARD: "sonnet", Tier.PREMIUM: "opus"},
        "codex": {
            Tier.ECONOMY: "gpt-6-luna",
            Tier.STANDARD: "gpt-6-sol",
            Tier.PREMIUM: "gpt-6-sol",
        },
    }
    assert TABLE.anchor("gpt-6-sol") is Tier.STANDARD
    assert TABLE.anchor("gpt-6-luna") is Tier.ECONOMY
    assert TABLE.anchor("gpt-5.6-sol") is Tier.PREMIUM
    assert TABLE.anchor("gpt-6-astra") is Tier.FRONTIER
    assert TABLE.anchor("gpt-5.6-terra") is None


def test_tier_defaults_never_hold_frontier_or_malformed_rows() -> None:
    parsed = tier_defaults(
        {
            "Claude": {"frontier": "fable", "premium": " opus ", "bogus": "x", "economy": 3},
            "codex": "gpt-6-sol",
            "opencode": {"standard": " "},
        }
    )
    assert parsed == {"claude": {Tier.PREMIUM: "opus"}}
    assert tier_defaults(None) == {}


@pytest.mark.parametrize(
    ("provider", "depth", "expected"),
    [
        (
            Provider.CLAUDE,
            Depth.NORMAL,
            {"orchestrator": "sonnet", "analyst": "sonnet", "senior": "opus"},
        ),
        (
            Provider.CLAUDE,
            Depth.QUICK,
            {"orchestrator": "sonnet", "analyst": "sonnet", "senior": "sonnet"},
        ),
        (
            Provider.CLAUDE,
            Depth.DEEP,
            {"orchestrator": "sonnet", "analyst": "sonnet", "senior": "opus"},
        ),
        (
            Provider.CODEX,
            Depth.NORMAL,
            {"orchestrator": "gpt-6-sol", "analyst": "gpt-6-sol", "senior": "gpt-6-sol"},
        ),
        (
            Provider.CODEX,
            Depth.QUICK,
            {"orchestrator": "gpt-6-sol", "analyst": "gpt-6-sol", "senior": "gpt-6-sol"},
        ),
        (
            Provider.CODEX,
            Depth.DEEP,
            {"orchestrator": "gpt-6-sol", "analyst": "gpt-6-sol", "senior": "gpt-6-sol"},
        ),
    ],
)
def test_each_tier_routes_to_the_provider_default(
    provider: Provider, depth: Depth, expected: dict[str, str]
) -> None:
    chosen = team(provider, depth)
    economy = "haiku" if provider is Provider.CLAUDE else "gpt-6-luna"
    standard = "sonnet" if provider is Provider.CLAUDE else "gpt-6-sol"
    assert {role.value: model for role, model in chosen.items()} == {
        **expected,
        "tester": standard,
        "docs": economy,
    }
    assert all(not model.startswith("gpt-5.6") for model in chosen.values())


def test_without_the_defaults_a_codex_team_lands_on_the_older_generation() -> None:
    policy = RoutingPolicy(engines=("codex",))
    chosen = plan_route(policy, PLACED, default_requests(policy))
    senior = next(route for route in chosen if route.role is Role.SENIOR)
    assert senior.model is not None and senior.model.id == "gpt-5.6-sol"


def test_a_missing_default_falls_back_to_the_candidate_search() -> None:
    catalog = tuple(entry for entry in PLACED if entry.id != "gpt-6-sol")
    policy = RoutingPolicy(engines=("codex",), tier_defaults=TABLE.defaults)
    chosen = {
        route.role: route.model.id
        for route in plan_route(policy, catalog, default_requests(policy))
        if route.model is not None
    }
    assert chosen[Role.SENIOR] == "gpt-5.6-sol"
    assert chosen[Role.DOCS] == "gpt-6-luna"


def test_pins_inside_the_provider_are_honored_including_older_generations() -> None:
    chosen = team(Provider.CODEX, senior="gpt-5.6-sol", docs="codex:gpt-6-luna")
    assert chosen[Role.SENIOR] == "gpt-5.6-sol"
    assert chosen[Role.DOCS] == "gpt-6-luna"
    pins = {Role.SENIOR: "gpt-5.6-sol", Role.DOCS: "codex:gpt-6-luna"}
    policy = RoutingPolicy(engines=("codex",), tier_defaults=TABLE.defaults, role_models=pins)
    planned = plan_route(policy, PLACED, default_requests(policy))
    assert pin_issues(pins, PLACED, planned, ("codex",)) == ()


@pytest.mark.parametrize(
    ("provider", "pin", "model"),
    [
        (Provider.CLAUDE, "codex:gpt-6-sol", "codex:gpt-6-sol"),
        (Provider.CODEX, "opus", "claude:opus"),
    ],
)
def test_pins_outside_the_provider_are_refused_with_the_reason(
    provider: Provider, pin: str, model: str
) -> None:
    pins = {Role.TESTER: pin}
    policy = RoutingPolicy(
        engines=(provider.value,), tier_defaults=TABLE.defaults, role_models=pins
    )
    planned = plan_route(policy, PLACED, default_requests(policy))
    issues = pin_issues(pins, PLACED, planned, (provider.value,))
    assert [issue.key for issue in issues] == ["route.pin_provider"]
    assert english(issues[0]) == (
        f"tester: {model} belongs to another provider; a team uses one provider ({provider.value})"
    )
    assert Catalog("es").message(issues[0]) == (
        f"tester: {model} es de otro proveedor; un equipo usa un solo proveedor ({provider.value})"
    )


def test_pins_that_cannot_be_honored_are_rejected_with_reasons() -> None:
    chosen = routes(RoutingPolicy(engines=("claude",)))
    issues = pin_issues(
        {
            Role.ANALYST: "gpt-9",
            Role.SENIOR: "codex:gpt-6-sol",
            Role.DOCS: "opus",
        },
        CATALOG,
        chosen,
        ("claude",),
    )
    texts = [english(issue) for issue in issues]
    assert (
        texts[0]
        == "analyst is pinned to gpt-9, which is not in the model catalog; run cuanta models"
    )
    assert texts[1] == (
        "senior: codex:gpt-6-sol belongs to another provider; a team uses one provider (claude)"
    )
    assert texts[2] == "docs is pinned to claude:opus, but the route chose another model"
    single = pin_issues({Role.SENIOR: "opus"}, CATALOG, chosen, ("opencode",))
    assert [issue.key for issue in single] == ["route.pin_engine"]
    for language in ("en", "es"):
        text = Catalog(language).message(single[0])
        assert "mix" not in text and "mezcl" not in text and "--cross-engine" not in text


def test_a_codex_tester_pin_is_not_refused_for_builds() -> None:
    pins = {Role.TESTER: "codex:gpt-6-sol"}
    policy = RoutingPolicy(engines=("codex",), tier_defaults=TABLE.defaults, role_models=pins)
    planned = plan_route(policy, PLACED, default_requests(policy))
    tester = next(route for route in planned if route.role is Role.TESTER)
    assert tester.engine == "codex"
    assert pin_issues(pins, PLACED, planned, ("codex",)) == ()


def test_team_cards_explain_engine_share_guarantees_context_and_warnings() -> None:
    policy = RoutingPolicy(engines=("codex",), tier_defaults=TABLE.defaults)
    chosen = plan_route(policy, PLACED, default_requests(policy))
    cards = team_cards(
        chosen,
        {Role.ANALYST: 0.3, Role.SENIOR: 0.5},
        1.3,
        lambda engine: engine == "claude",
        frozenset({"codex"}),
    )
    by_role = {card.role: card for card in cards}
    assert Role.ORCHESTRATOR not in by_role
    assert {card.engine for card in cards} == {"codex"}
    senior = by_role[Role.SENIOR]
    assert english(senior.title) == "senior: codex gpt-6-sol, share $0.5000"
    assert english(senior.context) == "Context: a text pack and the anchored handoff chain"
    assert [warning.key for warning in senior.warnings] == [
        "guarantee.codex_builds",
        "guarantee.cap_warning",
    ]
    tester = by_role[Role.TESTER]
    assert english(tester.title) == "tester: codex gpt-6-sol"
    assert english(by_role[Role.DOCS].title) == "docs: codex gpt-6-luna"
    assert all(" · " not in english(item.message) for item in senior.guarantees)
    native = team_cards(routes(RoutingPolicy(engines=("claude",))), {}, 0.0, lambda _: True)
    assert all(card.warnings == () for card in native)
    assert english(native[0].context).startswith("Context: index tools")


def test_costs_group_cross_attempts_by_completion() -> None:
    runs = [
        Run("A", "cross", scope="analyst", status="ok", cost_usd=0.2, outcome="accepted"),
        Run("B", "cross", scope="analyst", status="ok", cost_usd=0.4),
        Run("C", "mandate", status="ok", cost_usd=0.1),
    ]
    items = attempts(runs, "")
    report = report_attempts(items, "", None, {"A": "complete_skipped", "B": "partial", "C": ""})
    assert [(row.key, row.runs, row.accepted) for row in report.by_completion] == [
        ("complete_skipped", 1, 1),
        ("partial", 1, 0),
    ]
    assert report_attempts(items, "").by_completion == ()
    assert english(msg("completion.complete_skipped")) == "complete, optional roles skipped"


def test_codex_file_guard_prepares_planned_files_and_finds_unreadable_ones(tmp_path: Path) -> None:
    workspace = LocalWorkspace(tmp_path)
    workspace.write_text("src/kept.ts", "old\n")
    guard = CodexFileGuard(workspace, frozenset())
    plan = ChangePlan(
        edit=(
            EditTarget("src/new.ts", 0.8),
            EditTarget("src/empty.ts", 0.8),
            EditTarget("src/kept.ts", 1.0),
            EditTarget("src/*.css", 0.5),
            EditTarget("pkg/__init__.py", 0.8),
            EditTarget("metadata.metadataBase", 0.8),
            EditTarget("middleware.ts", 0.8),
        )
    )
    created = guard.prepare(plan)
    assert created == ("src/new.ts", "src/empty.ts", "pkg/__init__.py", "middleware.ts")
    assert (tmp_path / "src/new.ts").read_text(encoding="utf-8") == ""
    (tmp_path / "src/new.ts").write_text("filled\n", encoding="utf-8")
    assert guard.settle(created) == ("src/empty.ts", "middleware.ts")
    assert (tmp_path / "pkg/__init__.py").exists()
    assert not (tmp_path / "src/empty.ts").exists()
    assert guard.prepare(None) == ()
    assert guard.unreadable() == ()


def test_verification_rounds_are_read_back_for_the_result_views() -> None:
    from cuanta.application.results import VerifyRound, verify_rounds

    meta = [
        {
            "role": "senior",
            "attempt": 1,
            "seconds": 42.5,
            "commands": [
                {"command": "npx tsc --noEmit", "exit_code": 0, "timed_out": False},
                {"command": "npm run build", "exit_code": 1, "timed_out": False},
            ],
        },
        {"role": "senior", "attempt": 2, "seconds": 30, "commands": [{"exit_code": 0}]},
        "noise",
    ]
    assert verify_rounds(meta) == (
        VerifyRound("senior", 1, 1, 2, 42.5),
        VerifyRound("senior", 2, 1, 1, 30.0),
    )
    assert verify_rounds(None) == ()


def test_pins_accept_engine_and_resolved_model_names() -> None:
    from cuanta.domain.routing import pinned

    assert pinned(CATALOG, "claude:claude-sonnet-5") is CATALOG[0]
    assert pinned(CATALOG, "CLAUDE:Opus") is CATALOG[1]
    assert pinned(CATALOG, "codex:gpt-6-sol") is CATALOG[2]
    assert pinned(CATALOG, "codex:claude-sonnet-5") is None
    assert pinned(CATALOG, "claude-opus-5-5") is CATALOG[1]


def test_the_recommended_provider_follows_cost_per_accepted_change() -> None:
    def pipeline(
        root: str, engines: tuple[str, str], costs: tuple[float | None, ...], outcome: str
    ) -> list[Run]:
        return [
            Run(
                root,
                "cross",
                engine=engines[0],
                scope="analyst",
                task_type="feature",
                cost_usd=costs[0],
                outcome=outcome,
            ),
            Run(
                f"{root}s",
                "cross",
                engine=engines[1],
                scope="senior",
                task_type="feature",
                cost_usd=costs[1],
                parent_id=root,
            ),
        ]

    def native(run_id: str, cost: float | None, outcome: str, engine: str = "claude") -> Run:
        return Run(
            run_id, "mandate", engine=engine, task_type="feature", cost_usd=cost, outcome=outcome
        )

    runs = [
        *pipeline("G1", ("codex", "codex"), (0.3, 0.4), "accepted"),
        *pipeline("G2", ("codex", "codex"), (0.2, 0.1), "rejected"),
        *pipeline("X1", ("claude", "codex"), (0.1, 0.1), "accepted"),
        *pipeline("P1", ("codex", "codex"), (0.1, 0.1), ""),
        native("N1", 0.9, "accepted"),
        native("N2", 0.5, "accepted"),
        native("S1", 0.01, "accepted"),
        native("C1", 0.01, "accepted", "codex"),
        Run(
            "O1",
            "mandate",
            engine="opencode",
            task_type="feature",
            cost_usd=0.1,
            outcome="accepted",
        ),
    ]
    shapes = {
        "N1": "pipeline",
        "N2": "pipeline",
        "S1": "single",
        "O1": "pipeline",
        "C1": "pipeline",
    }
    found = provider_attempts(runs, shapes)
    assert sorted((item.provider.value, item.cost_usd, item.accepted) for item in found) == [
        ("claude", 0.5, True),
        ("claude", 0.9, True),
        ("codex", pytest.approx(0.3), False),
        ("codex", pytest.approx(0.7), True),
    ]
    advice = recommend_provider(found, "feature")
    assert advice is not None
    assert (advice.provider, advice.attempts, advice.accepted) == (Provider.CLAUDE, 2, 2)
    assert advice.per_accepted == pytest.approx(0.7)
    assert recommend_provider(found, "bug") is None
    unknown = (ProviderAttempt(Provider.CODEX, "bug", None, True),)
    assert recommend_provider(unknown, "bug") is None
    assert english(advice_message(advice, "feature")) == (
        "Measured feature runs recommend the Claude team: $0.7000 per accepted change (attempts: 2)"
    )
    assert Catalog("es").message(advice_message(advice, "feature")) == (
        "Las corridas feature medidas recomiendan el Equipo Claude: $0.7000 por cambio "
        "aceptado (intentos: 2)"
    )
