from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.adapters.system.workspace import LocalWorkspace
from cuanta.application.cross_files import CodexFileGuard
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.ledger import Run
from cuanta.domain.messages import english, msg
from cuanta.domain.models import ModelEntry, Tier
from cuanta.domain.real_costs import attempts, report_attempts
from cuanta.domain.routing import (
    MIX_ENGINES,
    Mix,
    Role,
    RoleRoute,
    RoutingPolicy,
    default_requests,
    parse_mix,
    pin_issues,
    plan_route,
    with_mix,
)
from cuanta.domain.team import mix_title, team_cards

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


def routes(policy: RoutingPolicy) -> tuple[RoleRoute, ...]:
    return plan_route(policy, CATALOG, default_requests(policy))


@pytest.mark.parametrize(
    ("mix", "analyst", "senior"),
    [
        (Mix.CLAUDE_ONLY, "claude", "claude"),
        (Mix.CLAUDE_PLANS, "claude", "codex"),
        (Mix.CODEX_PLANS, "codex", "claude"),
    ],
)
def test_mix_presets_set_the_engine_of_each_role(mix: Mix, analyst: str, senior: str) -> None:
    found = {route.role: route.engine for route in routes(with_mix(RoutingPolicy(), mix))}
    assert found[Role.ANALYST] == analyst and found[Role.SENIOR] == senior
    assert found[Role.TESTER] == "claude" and found[Role.DOCS] == "claude"
    assert MIX_ENGINES[mix][Role.TESTER] == "claude"
    assert parse_mix(mix.value) is mix
    assert parse_mix("nope") is None
    assert with_mix(RoutingPolicy(), None) == RoutingPolicy()
    assert english(mix_title(mix)) in {
        "Claude only",
        "Claude plans, Codex writes",
        "Codex plans, Claude writes",
    }


def test_pins_are_honored_across_engines_when_they_route() -> None:
    policy = with_mix(
        RoutingPolicy(role_models={Role.SENIOR: "codex:gpt-6-sol", Role.ANALYST: "sonnet"}),
        Mix.CLAUDE_PLANS,
    )
    chosen = routes(policy)
    senior = next(route for route in chosen if route.role is Role.SENIOR)
    assert senior.model is not None and senior.model.key == "codex:gpt-6-sol"
    pins = {Role.SENIOR: "codex:gpt-6-sol", Role.ANALYST: "sonnet"}
    assert pin_issues(pins, CATALOG, chosen, ("claude", "codex")) == ()


def test_pins_that_cannot_be_honored_are_rejected_with_reasons() -> None:
    chosen = routes(RoutingPolicy(engines=("claude",)))
    issues = pin_issues(
        {
            Role.ANALYST: "gpt-9",
            Role.SENIOR: "codex:gpt-6-sol",
            Role.TESTER: "codex:gpt-5.6-terra",
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
    assert texts[1].startswith(
        "senior is pinned to codex:gpt-6-sol, but this launch can only use claude"
    )
    assert texts[2].startswith("tester is pinned to codex:gpt-5.6-terra")
    assert texts[3] == "docs is pinned to claude:opus, but the route chose another model"
    blocked = pin_issues(
        {Role.TESTER: "codex:gpt-5.6-terra"},
        CATALOG,
        routes(with_mix(RoutingPolicy(role_models={Role.TESTER: "codex:gpt-5.6-terra"}), None)),
        ("claude", "codex"),
        {"codex"},
    )
    assert [issue.key for issue in blocked] == ["route.pin_build"]


def test_team_cards_explain_engine_share_guarantees_context_and_warnings() -> None:
    chosen = routes(with_mix(RoutingPolicy(), Mix.CLAUDE_PLANS))
    cards = team_cards(
        chosen,
        {Role.ANALYST: 0.3, Role.SENIOR: 0.5},
        1.3,
        lambda engine: engine == "claude",
        frozenset({"codex"}),
    )
    by_role = {card.role: card for card in cards}
    assert Role.ORCHESTRATOR not in by_role
    senior = by_role[Role.SENIOR]
    assert english(senior.title) == "senior: codex gpt-6-sol, share $0.5000"
    assert english(senior.context) == "Context: a text pack and the anchored handoff chain"
    assert [warning.key for warning in senior.warnings] == [
        "guarantee.codex_builds",
        "guarantee.cap_warning",
    ]
    tester = by_role[Role.TESTER]
    assert english(tester.title) == "tester: claude claude-opus-5-5"
    assert tester.warnings == ()
    assert english(by_role[Role.ANALYST].context).startswith("Context: index tools")
    assert all(" · " not in english(item.message) for item in senior.guarantees)


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


def test_the_recommended_mix_follows_cost_per_accepted_change() -> None:
    from cuanta.domain.team import advice_message, mix_attempts, recommend_mix

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

    runs = [
        *pipeline("A1", ("claude", "claude"), (0.4, 0.5), "accepted"),
        *pipeline("A2", ("claude", "claude"), (0.4, 0.5), "rejected"),
        *pipeline("B1", ("claude", "codex"), (0.3, 0.4), "accepted"),
        *pipeline("C1", ("codex", "claude"), (0.2, None), "accepted"),
        *pipeline("P1", ("claude", "claude"), (0.1, 0.1), ""),
        Run("M", "mandate", engine="claude", task_type="feature", cost_usd=0.1, outcome="accepted"),
    ]
    attempts = mix_attempts(runs)
    assert len(attempts) == 4
    advice = recommend_mix(attempts, "feature")
    assert advice is not None
    assert (advice.mix, advice.attempts, advice.accepted) == (Mix.CLAUDE_PLANS, 1, 1)
    assert advice.per_accepted == pytest.approx(0.7)
    assert recommend_mix(attempts, "bug") is None
    text = english(advice_message(advice, "feature"))
    assert text == (
        "Measured feature runs recommend Claude plans, Codex writes: $0.7000 per accepted change "
        "(attempts: 1)"
    )
