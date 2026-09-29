from __future__ import annotations

from dataclasses import replace

import pytest

from cuanta.application.cross_engine import CompletionState, CrossReport, CrossStep
from cuanta.application.governor import Governor
from cuanta.cli.commands.mandate import cross_payload
from cuanta.domain.engine import ToolCall
from cuanta.domain.governor import (
    RESUMED,
    ROTATED,
    SKIPPED,
    Projection,
    Reaction,
    ReactionKind,
    ReactionTaken,
    RolePlan,
    Trigger,
)
from cuanta.domain.governor_report import (
    BEST_EFFORT,
    HOOKS,
    BlockedCalls,
    GovernorSummary,
    governor_metrics,
    governor_payload,
    governor_summary,
    parse_governor,
    saved_usd,
)
from cuanta.domain.routing import Provider, Role


def projection(spent: float, limit: float, final: float | None, estimated: bool) -> Projection:
    return Projection(spent, estimated, limit, 0.01, 0.5, 3, final, 2, 0.8)


def taken(
    kind: ReactionKind,
    trigger: Trigger,
    spent: float,
    limit: float,
    final: float | None = None,
    saving: float = 0.0,
    sent: bool = True,
    outcome: str = "",
) -> ReactionTaken:
    reaction = Reaction(
        Role.SENIOR,
        kind,
        trigger,
        12.3456,
        projection(spent, limit, final, kind is ReactionKind.CODEX_STOP),
        saving,
    )
    return ReactionTaken(reaction, "RUN1", sent, outcome)


def test_only_realized_reactions_count_as_saved() -> None:
    stop = taken(ReactionKind.CODEX_STOP, Trigger.SHARE, 0.41, 0.48, 1.82, outcome=RESUMED)
    assert saved_usd(stop) == pytest.approx(1.34)
    assert saved_usd(taken(ReactionKind.CODEX_STOP, Trigger.SHARE, 0.41, 0.48, 0.45)) == 0.0
    assert saved_usd(taken(ReactionKind.CODEX_STOP, Trigger.SHARE, 0.41, 0.48)) is None
    assert (
        saved_usd(taken(ReactionKind.CODEX_STOP, Trigger.SHARE, 0.4, 0.5, 2.0, sent=False)) is None
    )
    rotated = taken(ReactionKind.ROTATE, Trigger.FRESH_SESSION, 0.1, 1.0, saving=0.22)
    assert saved_usd(rotated) is None
    assert saved_usd(ReactionTaken(rotated.reaction, "RUN1", True, ROTATED)) == 0.22
    assert saved_usd(ReactionTaken(rotated.reaction, "RUN1", True, SKIPPED)) is None
    assert saved_usd(taken(ReactionKind.FINISH_NOW, Trigger.HEADROOM, 0.8, 0.92, 1.5)) is None


def gsap_stop(edit_items: frozenset[int]) -> ReactionTaken:
    edits = tuple(f"src/gsap-{index}.ts" for index in range(6))
    plan = RolePlan(
        Role.SENIOR,
        Provider.CODEX,
        "gpt-6-sol",
        0.4785,
        18,
        edit_paths=edits,
        usd_per_item=0.2574 / 18,
    )
    governor = Governor([plan], lambda: 0.0)
    for item in range(1, 60):
        name = "file_change" if item in edit_items else "mcp_tool_call"
        found = governor.observe(Role.SENIOR, ToolCall(name, f"item_{item}"))
        if found:
            return ReactionTaken(found[0], "RUN1", True, RESUMED)
    raise AssertionError("the governor never stopped the role")


def test_a_codex_stop_without_a_credible_end_saves_an_unknown_amount() -> None:
    stuck = gsap_stop(frozenset())
    assert stuck.reaction.kind is ReactionKind.CODEX_STOP
    assert stuck.reaction.projection.remaining_steps == 1
    assert stuck.reaction.projection.at_completion_usd is None
    assert saved_usd(stuck) is None
    paced = gsap_stop(frozenset({10, 12}))
    assert paced.reaction.projection.remaining_steps > 1
    assert (saved_usd(paced) or 0.0) > 0.4


def test_a_governed_run_is_recorded_even_without_reactions() -> None:
    assert governor_metrics([], recorded=True) == {"reactions": [], "read_discipline": {}}
    hooked = governor_metrics([], hooks=True)
    assert hooked == {"reactions": [], "read_discipline": {}, HOOKS: True}
    summary = parse_governor(hooked)
    assert summary.recorded and summary.hooks and not summary.shown
    assert not parse_governor(None).recorded and not parse_governor({}).recorded


def test_stored_metrics_round_trip_to_the_result_summary() -> None:
    items = [
        taken(ReactionKind.FINISH_NOW, Trigger.HEADROOM, 0.825, 0.92),
        taken(ReactionKind.CODEX_STOP, Trigger.SHARE, 0.41, 0.48, 1.82, outcome=RESUMED),
    ]
    assert governor_metrics([]) == {} and governor_metrics([], {"senior": ""}) == {}
    stored = governor_metrics(items, {"analyst": HOOKS, "senior": BEST_EFFORT, "docs": ""})
    assert stored["read_discipline"] == {"analyst": HOOKS, "senior": BEST_EFFORT}
    reactions = stored["reactions"]
    assert isinstance(reactions, list)
    assert reactions[0] == {
        "kind": "finish_now",
        "role": "senior",
        "trigger": "headroom",
        "at_s": 12.346,
        "run_id": "RUN1",
        "sent": True,
        "spent_usd": 0.825,
        "limit_usd": 0.92,
        "estimated": False,
        "saved_usd": None,
        "outcome": None,
    }
    blocked = BlockedCalls(2, 1, 1, 2_500)
    summary = parse_governor(stored, blocked)
    live = governor_summary(items, {"analyst": HOOKS, "senior": BEST_EFFORT}, blocked)
    assert summary == replace(live, recorded=True) and not summary.hooks
    assert summary.shown and summary.enforced == ("analyst",)
    assert summary.best_effort == ("senior",)
    assert summary.saved_usd == pytest.approx(1.34)
    assert summary.reactions[1].estimated and summary.reactions[1].outcome == RESUMED
    payload = governor_payload(summary)
    assert payload["blocked"] == {
        "reads": 2,
        "searches": 1,
        "tests": 1,
        "total": 4,
        "tokens_estimate": 2_500,
    }
    assert payload["saved_estimated"] is True
    assert governor_payload(GovernorSummary())["blocked"] is None
    assert not parse_governor(None).shown
    assert parse_governor({"reactions": [{"kind": 3}, "x"], "read_discipline": []}) == (
        GovernorSummary(recorded=True)
    )


def test_the_team_json_gains_a_governor_object() -> None:
    step = CrossStep(
        Role.SENIOR,
        "codex",
        "gpt-6-sol",
        "RUN1",
        True,
        0.8,
        "",
        "estimated",
        1.0,
        1.0,
        stopped=True,
        read_discipline=BEST_EFFORT,
    )
    stop = taken(ReactionKind.CODEX_STOP, Trigger.HEADROOM, 0.8, 1.0, 1.9, outcome=RESUMED)
    report = CrossReport((step,), True, 0.8, state=CompletionState.COMPLETE, governor=(stop,))
    payload = cross_payload(report)
    governor = payload["governor"]
    assert isinstance(governor, dict)
    assert governor["read_discipline"] == {"senior": BEST_EFFORT}
    assert governor["blocked"] is None
    assert governor["saved_usd"] == pytest.approx(0.9)
    steps = payload["steps"]
    assert isinstance(steps, list)
    assert steps[0]["stopped"] is True and steps[0]["read_discipline"] == BEST_EFFORT
    counted = cross_payload(report, BlockedCalls(1, 0, 0, 900))
    assert isinstance(counted["governor"], dict)
    assert counted["governor"]["blocked"]["tokens_estimate"] == 900
