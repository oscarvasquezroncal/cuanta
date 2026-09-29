from __future__ import annotations

from dataclasses import replace

import pytest

from cuanta.application.governor import Governor
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.engine import ModelUsage, RunResult, SessionStarted, StepUsage, ToolCall
from cuanta.domain.envelope import Buckets, FixedPrefix, RoleForecast, StopRules
from cuanta.domain.governor import (
    FINISH_HEADROOM_STEPS,
    FINISH_SHARE,
    GROWTH_MIN_REQUESTS,
    WRAP_UP_STEPS,
    Reaction,
    ReactionKind,
    RolePlan,
    RoleProgress,
    Trigger,
    ahead_usd,
    codex_spend,
    decide,
    project,
    project_path,
    remaining_steps,
    role_plan,
    rotation_saving,
    step_usd,
    stop_trigger,
)
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.pricing import Price, PriceTable
from cuanta.domain.role_budgets import native_cap
from cuanta.domain.routing import Provider, Role

SONNET = Price(3.0, 15.0, 3.75, 0.30)
HAIKU = Price(1.0, 5.0, 1.25, 0.10)
EDITS = ("src/a.ts", "src/b.ts", "src/c.ts", "src/d.ts")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def claude_plan(
    role: Role = Role.SENIOR,
    share: float = 1.0,
    *,
    planned_requests: int = 4,
    edit_paths: tuple[str, ...] = (),
    read_paths: tuple[str, ...] = (),
    price: Price | None = SONNET,
    hard_cap_usd: float = 0.0,
    fixed_tokens: int = 0,
    cards_tokens: int = 0,
    rotatable: bool = False,
) -> RolePlan:
    return RolePlan(
        role,
        Provider.CLAUDE,
        "sonnet",
        share,
        planned_requests,
        edit_paths=edit_paths,
        read_paths=read_paths,
        price=price,
        hard_cap_usd=hard_cap_usd,
        fixed_tokens=fixed_tokens,
        cards_tokens=cards_tokens,
        rotatable=rotatable,
    )


def step(
    message_id: str,
    cache_read: int,
    cache_write: int = 0,
    output: int = 500,
    parent: str = "",
    model: str = "claude-sonnet-5",
) -> StepUsage:
    usage = ModelUsage(
        model, output_tokens=output, cache_read_tokens=cache_read, cache_write_tokens=cache_write
    )
    return StepUsage(usage, parent, message_id)


def tool(name: str, path: str = "", parent: str = "", **inputs: object) -> ToolCall:
    found: dict[str, object] = {"file_path": path} if path else {}
    return ToolCall(name, f"{name}-{path}", {**found, **inputs}, parent)


def forecast(role: Role, requests: int, p50: float | None) -> RoleForecast:
    return RoleForecast(
        role=role,
        model="gpt-6-sol",
        buckets=Buckets(),
        requests=requests,
        p50_usd=p50,
        p90_usd=p50,
        share=0.0,
        stops=StopRules(1, 1, 1),
        fixed=FixedPrefix(20_000, False),
        factor=1.0,
    )


def test_a_request_is_priced_with_its_cache_write_split() -> None:
    usage = ModelUsage(
        "claude-sonnet-5",
        input_tokens=100,
        output_tokens=200,
        cache_read_tokens=1_000,
        cache_write_tokens=3_000,
    )
    split = StepUsage(usage, "", "m", write_5m_tokens=1_000, write_1h_tokens=2_000)
    assert step_usd(split, SONNET, Provider.CLAUDE) == pytest.approx(0.01935)
    whole = StepUsage(usage, "", "m")
    assert step_usd(whole, SONNET, Provider.CLAUDE, 3_600) == pytest.approx(0.0216)


def test_the_cost_ahead_grows_with_the_context() -> None:
    plan = claude_plan()
    progress = RoleProgress(
        requests=3, context_tokens=40_000, growth_tokens=2_000, output_tokens=500
    )
    assert ahead_usd(plan, progress, 1) == pytest.approx(0.027)
    assert ahead_usd(plan, progress, 3) == pytest.approx(0.0828)
    assert ahead_usd(plan, progress, 0) == 0.0
    unpriced = claude_plan(price=None)
    assert ahead_usd(unpriced, RoleProgress(requests=2, spent_usd=0.1), 3) == pytest.approx(0.15)
    assert ahead_usd(unpriced, RoleProgress(requests=0), 3) is None


def test_remaining_requests_follow_the_edit_pace_after_the_first_edit() -> None:
    reader = claude_plan(Role.ANALYST, planned_requests=6)
    assert remaining_steps(reader, RoleProgress(requests=2)) == 4
    assert remaining_steps(reader, RoleProgress(requests=9)) == 0
    writer = claude_plan(edit_paths=EDITS)
    assert remaining_steps(writer, RoleProgress(requests=1)) == 3
    assert remaining_steps(writer, RoleProgress(requests=7)) == 1
    assert remaining_steps(writer, RoleProgress(requests=3, edits=1, first_edit_step=2)) == 6
    assert remaining_steps(writer, RoleProgress(requests=3, edits=2, first_edit_step=3)) == 1
    assert remaining_steps(writer, RoleProgress(requests=3, edits=4, first_edit_step=3)) == 0


def test_the_projection_says_when_the_cap_arrives_before_the_plan_is_done() -> None:
    plan = claude_plan(share=0.40, edit_paths=EDITS)
    shape = RoleProgress(
        requests=3,
        context_tokens=40_000,
        growth_tokens=2_000.0,
        output_tokens=500,
        edits=1,
        first_edit_step=3,
    )
    fits = project(plan, replace(shape, spent_usd=0.20))
    assert fits.remaining_steps == 3 and fits.completion == 0.25
    assert fits.at_completion_usd == pytest.approx(0.2828)
    assert not fits.cap_first
    late = project(plan, replace(shape, spent_usd=0.33))
    assert late.cap_first
    assert late.steps_to_cap == 2
    assert late.completion_at_cap == pytest.approx(0.75)
    assert late.next_usd == pytest.approx(0.027)
    unknown = project(plan, RoleProgress(requests=1, spent_usd=None))
    assert unknown.at_completion_usd is None and unknown.steps_to_cap is None
    assert not unknown.cap_first


def test_codex_spend_is_estimated_from_items_and_elapsed_time() -> None:
    plan = RolePlan(Role.SENIOR, Provider.CODEX, "gpt-6-sol", 0.5, 18)
    assert codex_spend(plan, 10, 30.0) is None
    rated = RolePlan(
        Role.SENIOR, Provider.CODEX, "gpt-6-sol", 0.5, 18, usd_per_item=0.01, usd_per_second=0.001
    )
    assert codex_spend(rated, 10, 30.0) == pytest.approx(0.1)
    assert codex_spend(rated, 1, 300.0) == pytest.approx(0.3)


def test_a_role_plan_comes_from_the_envelope_forecast_and_the_change_plan() -> None:
    change = ChangePlan(edit=(EditTarget("src/a.ts", 0.9),), read=("src/r.ts",))
    senior = role_plan(forecast(Role.SENIOR, 18, 0.2484), Provider.CODEX, 0.4785, change)
    assert senior.usd_per_item == pytest.approx(0.0138)
    assert senior.edit_paths == ("src/a.ts",)
    assert senior.read_paths == ("src/a.ts", "src/r.ts")
    assert senior.fixed_tokens == 20_000 and senior.planned_requests == 18
    analyst = role_plan(forecast(Role.ANALYST, 6, 0.3), Provider.CLAUDE, 0.3, change)
    assert analyst.edit_paths == () and analyst.read_paths == ("src/a.ts", "src/r.ts")
    assert analyst.usd_per_item == 0.0
    tester = role_plan(forecast(Role.TESTER, 5, None), Provider.CODEX, 0.2, change)
    assert tester.edit_paths == () and tester.read_paths == () and tester.usd_per_item == 0.0
    history = role_plan(forecast(Role.SENIOR, 18, 0.2484), Provider.CODEX, 0.5, usd_per_item=0.02)
    assert history.usd_per_item == 0.02


def test_paths_are_made_relative_to_the_project_on_both_platforms() -> None:
    assert project_path("C:\\Work\\App\\src\\A.ts", "C:/work/app") == "src/a.ts"
    assert project_path("D:/other/x.ts", "C:/work/app") == ""
    assert project_path("./src/a.ts", "C:/work/app") == "src/a.ts"
    assert project_path("/srv/app/src/A.ts", "/srv/app") == "src/A.ts"
    assert project_path("/elsewhere/a.ts", "/srv/app") == ""


def test_the_rotation_decision_in_both_directions() -> None:
    plan = claude_plan(share=2.0, planned_requests=20, fixed_tokens=20_000, cards_tokens=3_000)
    large = RoleProgress(requests=5, context_tokens=150_000)
    assert rotation_saving(plan, large, 15) == pytest.approx(0.43338)
    assert (rotation_saving(plan, RoleProgress(requests=5, context_tokens=25_000), 15) or 0) < 0
    assert (rotation_saving(plan, large, 1) or 0) < 0
    assert rotation_saving(claude_plan(fixed_tokens=0), large, 15) is None
    rotatable = claude_plan(
        share=2.0, planned_requests=20, fixed_tokens=20_000, cards_tokens=3_000, rotatable=True
    )
    spent = RoleProgress(requests=5, spent_usd=0.3, context_tokens=150_000)
    decision = decide(rotatable, spent, frozenset())
    assert decision is not None and decision.kind is ReactionKind.ROTATE
    assert decision.trigger is Trigger.FRESH_SESSION
    assert decision.saving_usd == pytest.approx(0.43338)
    assert decide(rotatable, spent, frozenset({ReactionKind.ROTATE})) is None
    assert decide(plan, spent, frozenset()) is None
    small = RoleProgress(requests=5, spent_usd=0.3, context_tokens=25_000)
    assert decide(rotatable, small, frozenset()) is None


def test_a_role_in_its_last_share_finishes_instead_of_rotating() -> None:
    plan = claude_plan(
        share=1.0, planned_requests=20, fixed_tokens=20_000, cards_tokens=3_000, rotatable=True
    )
    progress = RoleProgress(requests=5, spent_usd=FINISH_SHARE, context_tokens=150_000)
    decision = decide(plan, progress, frozenset())
    assert decision is not None
    assert (decision.kind, decision.trigger) == (ReactionKind.FINISH_NOW, Trigger.SHARE)


def test_the_finish_turn_is_sent_once_at_85_percent_of_the_share() -> None:
    clock = FakeClock()
    governor = Governor([claude_plan(Role.ANALYST, planned_requests=2)], clock)
    governor.start(Role.ANALYST)
    governor.observe(Role.ANALYST, SessionStarted("s", "claude-sonnet-5"))
    found: list[tuple[int, Reaction]] = []
    for index in range(30):
        clock.now += 10
        reactions = governor.observe(Role.ANALYST, step(f"m{index}", 100_000, output=1_000))
        found.extend((index + 1, reaction) for reaction in reactions)
    assert len(found) == 1
    request, reaction = found[0]
    assert request == 19
    assert (reaction.kind, reaction.trigger) == (ReactionKind.FINISH_NOW, Trigger.SHARE)
    assert reaction.projection.spent_usd == pytest.approx(0.855)
    assert reaction.at_s == pytest.approx(190.0)
    assert governor.reactions == (reaction,)


def test_the_finish_turn_leaves_two_requests_before_the_native_cap() -> None:
    governor = Governor(
        [claude_plan(Role.ANALYST, hard_cap_usd=0.5, planned_requests=2)], FakeClock()
    )
    found = [
        (index + 1, reaction)
        for index in range(20)
        for reaction in governor.observe(Role.ANALYST, step(f"m{index}", 100_000, output=1_000))
    ]
    assert [(request, reaction.trigger) for request, reaction in found] == [(10, Trigger.HEADROOM)]


def edit_run(
    share: float, requests: int, edits_after: dict[int, tuple[str, ...]]
) -> list[tuple[int, Trigger, int | None]]:
    governor = Governor([claude_plan(share=share, edit_paths=EDITS)], FakeClock(), "/srv/app")
    found: list[tuple[int, Trigger, int | None]] = []
    for index in range(1, requests + 1):
        reactions = list(governor.observe(Role.SENIOR, step(f"m{index}", 40_000, 2_000)))
        for path in edits_after.get(index, ()):
            reactions.extend(governor.observe(Role.SENIOR, tool("Edit", f"/srv/app/{path}")))
        found.extend(
            (index, reaction.trigger, reaction.projection.steps_to_cap) for reaction in reactions
        )
    return found


def test_a_slow_edit_pace_finishes_on_the_projection_near_the_cap_and_a_fast_one_does_not() -> None:
    assert edit_run(0.20, 5, {2: ("src/a.ts",)}) == [(4, Trigger.PROJECTION, 4)]
    assert edit_run(0.20, 5, {2: EDITS}) == []


def test_the_projection_waits_for_three_requests_of_growth_history() -> None:
    assert GROWTH_MIN_REQUESTS == 3
    assert edit_run(0.10, 3, {1: ("src/a.ts",)}) == [(3, Trigger.HEADROOM, 0)]


def gsap_stream(prefix: int, growths: list[int]) -> list[StepUsage]:
    found = [step("m1", 0, prefix)]
    context = prefix
    for index, growth in enumerate(growths, start=2):
        found.append(step(f"m{index}", context, growth))
        context += growth
    return found


def test_the_x3_gsap_senior_finishes_only_when_its_cap_is_a_few_requests_away() -> None:
    edits = tuple(f"src/gsap-{index}.ts" for index in range(6))
    plan = claude_plan(share=0.3853, planned_requests=18, edit_paths=edits, hard_cap_usd=0.3105)
    governor = Governor([plan], FakeClock(), "/srv/app")
    stream = gsap_stream(25_000, [3_000] * 17)
    spent = [0.0]
    found: list[tuple[int, Reaction]] = []
    for index, event in enumerate(stream, start=1):
        spent.append(spent[-1] + step_usd(event, SONNET, Provider.CLAUDE))
        found.extend((index, reaction) for reaction in governor.observe(Role.SENIOR, event))
    assert spent[-1] == pytest.approx(0.6699, abs=1e-4)
    assert len(found) == 1
    request, reaction = found[0]
    reach = reaction.projection.steps_to_cap
    assert (reaction.kind, reaction.trigger) == (ReactionKind.FINISH_NOW, Trigger.PROJECTION)
    assert request == 4 and reach == FINISH_HEADROOM_STEPS + WRAP_UP_STEPS
    assert spent[request + reach] <= plan.limit_usd < spent[request + reach + 1]
    assert reaction.projection.spent_usd == pytest.approx(spent[request])


def test_front_loaded_growth_never_ends_a_role_whose_run_fits_its_cap() -> None:
    edits = tuple(f"src/page-{index}.ts" for index in range(6))
    cap = native_cap(0.80, 0.0748)
    plan = claude_plan(share=0.80, planned_requests=18, edit_paths=edits, hard_cap_usd=cap)
    governor = Governor([plan], FakeClock(), "/srv/app")
    stream = gsap_stream(20_000, [8_000] * 3 + [1_500] * 14)
    spent = sum(step_usd(event, SONNET, Provider.CLAUDE) for event in stream)
    assert spent == pytest.approx(0.6297, abs=1e-4) and spent < cap
    assert [governor.observe(Role.SENIOR, event) for event in stream] == [()] * len(stream)


def test_reads_and_edits_are_checked_against_the_plan_sets() -> None:
    plan = claude_plan(edit_paths=("src/b.ts",), read_paths=("src/a.ts",))
    governor = Governor([plan], FakeClock(), "C:\\Work\\App")
    events = (
        tool("Read", "C:\\work\\app\\src\\a.ts"),
        tool("Read", "C:/Work/App/src/c.ts"),
        tool("Read", "D:/other/x.ts"),
        ToolCall("mcp__cuanta__page", "p1", {"path": "src/d.ts"}),
        tool("Read", "C:/work/app/src/b.ts"),
        tool("Edit", "C:/work/app/src/b.ts"),
        tool("Write", "C:/work/app/src/z.ts"),
        tool("Grep", pattern="x"),
    )
    for event in events:
        governor.observe(Role.SENIOR, event)
    progress = governor.progress(Role.SENIOR)
    assert (progress.reads, progress.leaked_reads) == (4, 2)
    assert (progress.edits, progress.stray_edits, progress.edit_calls) == (1, 1, 2)
    assert progress.items == 8
    assert governor.projection(Role.SENIOR).leaked_reads == 2


def test_subagent_events_are_attributed_by_their_parent_tool_use() -> None:
    plan = claude_plan(Role.ORCHESTRATOR, share=5.0, planned_requests=40)
    governor = Governor([plan], FakeClock(), "/srv/app", PriceTable({"claude-haiku-4-5": HAIKU}))
    events = (
        step("main-1", 30_000),
        step("main-1", 30_000),
        ToolCall("Agent", "t1", {"subagent_type": "architecture-analyst"}),
        step("sub-1", 14_000, parent="t1", model="claude-haiku-4-5"),
        tool("Read", "/srv/app/src/a.ts", parent="t1"),
        step("sub-2", 20_000, parent="t1", model="claude-haiku-4-5"),
    )
    for event in events:
        governor.observe(Role.ORCHESTRATOR, event)
    session = governor.progress(Role.ORCHESTRATOR)
    assert session.requests == 3
    assert session.context_tokens == 20_000 and session.growth_tokens == pytest.approx(6_000)
    main_usd = (30_000 * 0.30 + 500 * 15.0) / 1e6
    sub_usd = (14_000 * 0.10 + 500 * 5.0 + 20_000 * 0.10 + 500 * 5.0) / 1e6
    assert session.spent_usd == pytest.approx(main_usd + sub_usd)
    analyst = governor.subagents(Role.ORCHESTRATOR)[Role.ANALYST]
    assert (analyst.requests, analyst.reads, analyst.items) == (2, 1, 1)
    assert analyst.spent_usd == pytest.approx(sub_usd)
    assert analyst.tokens_per_request == pytest.approx(17_500)


def test_telemetry_costs_are_used_without_counting_requests_twice() -> None:
    governor = Governor([claude_plan(share=5.0)], FakeClock())
    governor.observe(Role.SENIOR, step("m1", 100_000, output=1_000))
    request = LedgerEvent(kind="api_request", model="claude-sonnet-5", cost_usd=0.5)
    governor.observe(Role.SENIOR, request)
    governor.observe(Role.SENIOR, LedgerEvent(kind="tool_result", cost_usd=9.0))
    progress = governor.progress(Role.SENIOR)
    assert progress.requests == 1 and progress.spent_usd == pytest.approx(0.5)
    telemetry = Governor([claude_plan(share=5.0)], FakeClock())
    priced = LedgerEvent(
        kind="api_request", model="claude-sonnet-5", cache_read_tokens=10_000, output_tokens=100
    )
    telemetry.observe(Role.SENIOR, priced)
    only = telemetry.progress(Role.SENIOR)
    assert only.requests == 1 and only.context_tokens == 10_000
    assert only.spent_usd == pytest.approx((10_000 * 0.30 + 100 * 15.0) / 1e6)
    assert only.cache_read_share == 1.0


def test_an_unpriced_model_leaves_the_spend_unknown_and_takes_no_action() -> None:
    governor = Governor([claude_plan(share=0.01, price=None)], FakeClock())
    assert governor.observe(Role.SENIOR, step("m1", 100_000)) == ()
    assert governor.progress(Role.SENIOR).spent_usd is None
    assert governor.projection(Role.SENIOR).spent_usd is None


def test_a_finished_role_takes_no_more_reactions_and_its_clock_stops() -> None:
    clock = FakeClock()
    governor = Governor([claude_plan(share=0.05)], clock)
    governor.start(Role.SENIOR)
    clock.now += 12
    governor.observe(Role.SENIOR, RunResult(True, "success", 0.01, 1, "s"))
    clock.now += 100
    assert governor.observe(Role.SENIOR, step("m1", 1_000_000)) == ()
    assert governor.check(Role.SENIOR) == ()
    assert governor.progress(Role.SENIOR).elapsed_s == pytest.approx(12.0)


def test_a_role_without_a_plan_is_refused() -> None:
    governor = Governor([claude_plan()], FakeClock())
    with pytest.raises(ValueError, match="no plan for the tester role"):
        governor.observe(Role.TESTER, step("m1", 1))


def test_a_codex_role_shaped_like_the_x3_gsap_senior_stops_at_its_share() -> None:
    clock = FakeClock()
    plan = role_plan(forecast(Role.SENIOR, 18, 0.2484), Provider.CODEX, 0.4785)
    governor = Governor([plan], clock, "/srv/app")
    governor.start(Role.SENIOR)
    found: list[tuple[int, Reaction]] = []
    for index in range(90):
        clock.now += 15.2
        name = "file_change" if index in {40, 55} else "mcp_tool_call"
        for reaction in governor.observe(Role.SENIOR, ToolCall(name, f"item_{index}")):
            found.append((index + 1, reaction))
    assert len(found) == 1
    item, reaction = found[0]
    assert item == 30
    assert (reaction.kind, reaction.trigger) == (ReactionKind.CODEX_STOP, Trigger.SHARE)
    assert reaction.projection.estimated
    assert reaction.projection.spent_usd == pytest.approx(30 * 0.2484 / 18)
    assert (reaction.projection.spent_usd or 0) < plan.share_usd < 1.8189
    assert reaction.at_s == pytest.approx(30 * 15.2)


def test_a_silent_codex_role_is_stopped_by_elapsed_time() -> None:
    clock = FakeClock()
    plan = RolePlan(Role.SENIOR, Provider.CODEX, "gpt-6-sol", 0.4785, 18, usd_per_second=0.00133)
    governor = Governor([plan], clock)
    governor.start(Role.SENIOR)
    clock.now += 300
    assert governor.check(Role.SENIOR) == ()
    clock.now += 10
    stop = governor.check(Role.SENIOR)
    assert [(reaction.kind, reaction.trigger) for reaction in stop] == [
        (ReactionKind.CODEX_STOP, Trigger.SHARE)
    ]
    assert governor.check(Role.SENIOR) == ()
    assert governor.progress(Role.SENIOR).estimated


def test_a_rotation_happens_at_most_once_and_the_new_session_is_measured_afresh() -> None:
    plan = claude_plan(
        share=2.0, planned_requests=20, fixed_tokens=20_000, cards_tokens=3_000, rotatable=True
    )
    governor = Governor([plan], FakeClock())
    first = governor.observe(Role.SENIOR, step("m1", 150_000))
    assert [(reaction.kind, reaction.trigger) for reaction in first] == [
        (ReactionKind.ROTATE, Trigger.FRESH_SESSION)
    ]
    assert first[0].saving_usd > 0
    governor.restart(Role.SENIOR)
    assert governor.progress(Role.SENIOR).context_tokens == 0
    assert governor.observe(Role.SENIOR, step("m2", 150_000)) == ()
    progress = governor.progress(Role.SENIOR)
    assert progress.requests == 2 and progress.context_tokens == 150_000


def test_edge_projections_and_telemetry_from_a_named_subagent() -> None:
    rated = RolePlan(
        Role.SENIOR, Provider.CODEX, "gpt-6-sol", 0.5, 0, usd_per_item=0.01, usd_per_second=0.001
    )
    busy = RoleProgress(items=10, elapsed_s=300.0, spent_usd=0.3)
    assert ahead_usd(rated, busy, 2) == pytest.approx(0.06)
    unplanned = project(rated, busy)
    assert unplanned.completion == 0.0 and unplanned.remaining_steps == 0
    assert stop_trigger(rated, replace(unplanned, spent_usd=None)) is None
    governor = Governor([claude_plan(Role.ORCHESTRATOR, share=5.0)], FakeClock())
    assert governor.progress(Role.ORCHESTRATOR).elapsed_s == 0.0
    request = LedgerEvent(kind="api_request", agent="tester", cost_usd=0.02, cache_read_tokens=9)
    governor.observe(Role.ORCHESTRATOR, request)
    tester = governor.subagents(Role.ORCHESTRATOR)[Role.TESTER]
    assert tester.spent_usd == pytest.approx(0.02) and tester.context_tokens == 9
    governor.restart(Role.ORCHESTRATOR)
    assert governor.subagents(Role.ORCHESTRATOR)[Role.TESTER].context_tokens == 0
