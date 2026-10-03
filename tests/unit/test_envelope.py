from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cuanta.domain.depth import TOKENS_PER_READ, Depth, profile
from cuanta.domain.envelope import (
    CLAUDE_FIXED,
    CLAUDE_LAUNCH_REQUESTS,
    CLAUDE_MAIN_FIXED,
    CLAUDE_MAIN_REQUESTS,
    CLAUDE_SINGLE_REQUESTS,
    CLAUDE_SUBAGENT_FIXED,
    CLAUDE_SUBAGENT_REQUESTS,
    CODEX_DOCS_FILES,
    CODEX_FIXED,
    CODEX_OUTPUT_PER_REQUEST,
    CODEX_REREAD_FILES,
    DEFAULT_HANDOFF,
    DEFAULT_SPREAD,
    DISPATCH_TOKENS,
    DOCS_EDITS,
    DOCS_FILES,
    DOCS_WRITE_TOKENS,
    ENVELOPE_SOURCE,
    JEV_SOURCE,
    MAIN_READ_FILES,
    OUTPUT_PER_REQUEST,
    REPAIR_WARMTH,
    SCOUT_PACK_TOKENS,
    SCOUT_SHAPE,
    SENIOR_READ_FILES,
    TEST_WRITE_TOKENS,
    TESTER_READ_FILES,
    WRITE_TOKENS_PER_EDIT,
    Buckets,
    EnvelopeInputs,
    FixedPrefix,
    RoleInput,
    RoleModel,
    RoleSample,
    SuggestionKind,
    Verdict,
    claude_requests,
    envelope,
    envelope_features,
    fixed_prefix,
    forecast_record,
    is_fix,
    output_per_request,
    p90_spread,
    repair_possible,
    role_factor,
    role_usd,
    verdict,
    write_rate,
)
from cuanta.domain.mandate import PartKind, RequestParts
from cuanta.domain.messages import msg
from cuanta.domain.pricing import Price, dollars
from cuanta.domain.routing import Provider, Role

CHEAP = Price(0.1, 0.5, 0.125, 0.01)
MID = Price(1.0, 5.0, 1.25, 0.1)
DEAR = Price(5.0, 25.0, 6.25, 0.5)


def _role(
    role: Role,
    price: Price | None = MID,
    fixed_tokens: int = 0,
    history: tuple[RoleSample, ...] = (),
    cheaper: RoleModel | None = None,
) -> RoleInput:
    return RoleInput(role, RoleModel(f"{role.value}-model", price), fixed_tokens, history, cheaper)


def _inputs() -> EnvelopeInputs:
    return EnvelopeInputs(
        task_type="feature",
        depth=profile(Depth.NORMAL, "feature"),
        shape="pipeline",
        provider=Provider.CLAUDE,
        roles=(_role(Role.ANALYST), _role(Role.SENIOR, DEAR), _role(Role.TESTER)),
        cap_usd=10.0,
        edit_tokens=(2_000, 0),
        read_tokens=(1_000,) * 6,
        warmth=0.5,
    )


def test_verdict_boundaries() -> None:
    assert verdict(None, None, 1.0) is Verdict.UNKNOWN
    assert verdict(0.5, None, 1.0) is Verdict.UNKNOWN
    assert verdict(5.0, 8.0, 0.0) is Verdict.COMFORTABLE
    assert verdict(1.01, 1.6, 1.0) is Verdict.INFEASIBLE
    assert verdict(1.0, 1.0, 1.0) is Verdict.TIGHT
    assert verdict(0.5, 1.01, 1.0) is Verdict.TIGHT
    assert verdict(0.5, 0.85 + 1e-9, 1.0) is Verdict.TIGHT
    assert verdict(0.5, 0.85 - 1e-9, 1.0) is Verdict.COMFORTABLE
    assert verdict(0.1, 0.2, 1.0) is Verdict.COMFORTABLE


def test_p90_uses_the_default_spread_below_five_valid_samples() -> None:
    assert p90_spread(()) == (DEFAULT_SPREAD, 0)
    assert p90_spread((1.1, 1.2, 1.3, 1.4)) == (DEFAULT_SPREAD, 0)
    assert p90_spread((float("nan"), -1.0, 0.0, 1.2, 1.3, 1.4, 1.5)) == (DEFAULT_SPREAD, 0)


def test_p90_uses_the_ninetieth_percentile_ratio_from_five_samples() -> None:
    assert p90_spread((1.0, 1.2, 1.1, 1.3, 2.0)) == (2.0, 5)
    ten = tuple(1.0 + step / 10 for step in range(10))
    spread, samples = p90_spread(ten)
    assert spread == pytest.approx(1.8)
    assert samples == 10


def test_p90_is_never_below_p50() -> None:
    assert p90_spread((0.5,) * 5) == (1.0, 5)
    low = envelope(replace(_inputs(), calibration=(0.4, 0.5, 0.6, 0.7, 0.8)))
    assert low.p90_usd == low.p50_usd
    high = envelope(replace(_inputs(), calibration=(2.0,) * 5))
    assert high.p50_usd is not None
    assert high.p90_usd == pytest.approx(high.p50_usd * 2.0)
    default = envelope(_inputs())
    assert default.p50_usd is not None
    assert default.p90_usd == pytest.approx(default.p50_usd * DEFAULT_SPREAD)
    assert (default.spread, default.spread_samples) == (DEFAULT_SPREAD, 0)
    for item in default.roles:
        assert item.p50_usd is not None
        assert item.p90_usd == pytest.approx(item.p50_usd * DEFAULT_SPREAD)


def test_fixed_prefix_prefers_measurements_and_marks_fallbacks() -> None:
    assert fixed_prefix(Provider.CLAUDE, Role.ANALYST) == FixedPrefix(19_500, False)
    assert fixed_prefix(Provider.CLAUDE, Role.DOCS) == FixedPrefix(34_800, False)
    assert fixed_prefix(Provider.CLAUDE, Role.ORCHESTRATOR) == FixedPrefix(19_700, False)
    assert fixed_prefix(Provider.CLAUDE, Role.ORCHESTRATOR, main=True) == FixedPrefix(
        CLAUDE_MAIN_FIXED, False
    )
    assert fixed_prefix(Provider.CODEX, Role.ORCHESTRATOR, main=True) == FixedPrefix(
        CODEX_FIXED, False
    )
    assert fixed_prefix(Provider.CODEX, Role.SENIOR) == FixedPrefix(CODEX_FIXED, False)
    assert fixed_prefix(Provider.CODEX, Role.ANALYST, 12_345) == FixedPrefix(12_345, True)
    assert fixed_prefix(Provider.CLAUDE, Role.SENIOR, 47_000, subagent=True) == FixedPrefix(
        CLAUDE_SUBAGENT_FIXED[Role.SENIOR], False
    )
    assert fixed_prefix(Provider.CODEX, Role.SENIOR, subagent=True) == FixedPrefix(
        CODEX_FIXED, False
    )
    assert all(CLAUDE_SUBAGENT_FIXED[role] < CLAUDE_FIXED[role] for role in CLAUDE_SUBAGENT_FIXED)
    result = envelope(
        replace(_inputs(), roles=(_role(Role.ANALYST, fixed_tokens=10_000), _role(Role.SENIOR)))
    )
    analyst, senior = result.roles
    assert analyst.fixed == FixedPrefix(10_000, True)
    assert senior.fixed == FixedPrefix(CLAUDE_FIXED[Role.SENIOR], False)
    assert analyst.buckets.start == 10_000 + profile(Depth.NORMAL, "feature").pack_tokens
    assert senior.buckets.start == CLAUDE_FIXED[Role.SENIOR]


def test_role_usd_prices_warm_context_growth_rereads_and_output() -> None:
    price = Price(1.0, 10.0, 2.0, 0.1)
    buckets = Buckets(start=1_000, exploration=300, writing=100, handoff=50)
    claude = role_usd(price, Provider.CLAUDE, buckets, 3, 0.5, 20)
    codex = role_usd(price, Provider.CODEX, buckets, 3, 0.5, 20)
    assert claude == pytest.approx((1_050 * 1.05 + 600 + 240 + 1_200) / 1_000_000)
    assert codex == pytest.approx((1_050 * 0.55 + 300 + 240 + 1_200) / 1_000_000)
    assert role_usd(None, Provider.CLAUDE, buckets, 3, 0.5) is None
    bare = Price(1.0, 10.0, None, None)
    assert role_usd(bare, Provider.CLAUDE, buckets, 1, 0.0) == pytest.approx(
        (1_050 + 300 + 100 * 10) / 1_000_000
    )


def test_claude_writes_use_the_one_hour_rate_when_the_cache_lives_past_five_minutes() -> None:
    assert write_rate(DEAR, Provider.CLAUDE) == 6.25
    assert write_rate(DEAR, Provider.CLAUDE, 300) == 6.25
    assert write_rate(DEAR, Provider.CLAUDE, 3_480) == 10.0
    assert write_rate(DEAR, Provider.CODEX, 3_480) == 5.0
    assert write_rate(Price(5.0, 25.0, None, 0.5), Provider.CLAUDE, 3_480) == 5.0
    short = envelope(_inputs())
    long = envelope(replace(_inputs(), cache_ttl_s=3_480))
    assert short.p50_usd is not None and long.p50_usd is not None
    assert long.p50_usd > short.p50_usd
    buckets = Buckets(start=1_000)
    hour = role_usd(DEAR, Provider.CLAUDE, buckets, 1, 0.0, ttl_s=3_480)
    assert hour == pytest.approx(1_000 * 10.0 / 1_000_000)


def test_buckets_follow_the_pipeline_shape() -> None:
    result = envelope(_inputs())
    analyst, senior, tester = result.roles
    assert [item.role for item in result.roles] == [Role.ANALYST, Role.SENIOR, Role.TESTER]
    assert analyst.buckets.exploration == 2_000 + TOKENS_PER_READ + 6 * 1_000
    assert analyst.buckets.handoff == 0
    assert senior.buckets.exploration == 2_000 + TOKENS_PER_READ
    assert senior.buckets.handoff == 160
    assert tester.buckets.exploration == 2_000 + TOKENS_PER_READ
    assert tester.buckets.handoff == 450
    assert tester.buckets.verification > 0
    assert result.buckets == analyst.buckets.plus(senior.buckets).plus(tester.buckets)
    assert result.requests == sum(item.requests for item in result.roles)
    assert sum(item.share for item in result.roles) == pytest.approx(1.0)
    assert result.p50_usd == pytest.approx(sum(item.p50_usd or 0.0 for item in result.roles))
    assert result.margin_usd == pytest.approx(10.0 - (result.p90_usd or 0.0))
    assert result.verdict is Verdict.COMFORTABLE
    assert result.suggestions == ()


def test_single_shape_is_one_context_without_handoff() -> None:
    result = envelope(replace(_inputs(), shape="single", roles=(_role(Role.ORCHESTRATOR),)))
    (only,) = result.roles
    assert only.role is Role.ORCHESTRATOR
    assert only.buckets.handoff == 0
    assert only.buckets.exploration == 2_000 + TOKENS_PER_READ + 6 * 1_000
    assert only.buckets.verification > 0


def test_orchestrator_is_left_out_of_a_pipeline() -> None:
    roles = (_role(Role.ORCHESTRATOR), _role(Role.ANALYST), _role(Role.SENIOR))
    result = envelope(replace(_inputs(), roles=roles))
    assert [item.role for item in result.roles] == [Role.ANALYST, Role.SENIOR]


def test_a_native_pipeline_adds_its_main_session_and_plans_subagents() -> None:
    roles = (_role(Role.ORCHESTRATOR), _role(Role.ANALYST), _role(Role.SENIOR))
    separate = envelope(replace(_inputs(), roles=roles))
    native = envelope(replace(_inputs(), roles=roles, native=True))
    main, *chain = native.roles
    assert main.role is Role.ORCHESTRATOR and not main.repair
    assert [item.role for item in chain] == [Role.ANALYST, Role.SENIOR]
    assert [item.buckets.exploration for item in chain] == [
        item.buckets.exploration for item in separate.roles
    ]
    assert [item.requests for item in chain] == [
        CLAUDE_SUBAGENT_REQUESTS[Role.ANALYST],
        CLAUDE_SUBAGENT_REQUESTS[Role.SENIOR],
    ]
    assert [item.fixed for item in chain] == [
        FixedPrefix(CLAUDE_SUBAGENT_FIXED[Role.ANALYST], False),
        FixedPrefix(CLAUDE_SUBAGENT_FIXED[Role.SENIOR], False),
    ]
    assert main.fixed == FixedPrefix(CLAUDE_MAIN_FIXED, False)
    assert main.buckets.start == CLAUDE_MAIN_FIXED + profile(Depth.NORMAL, "feature").pack_tokens
    assert main.buckets.handoff == 2 * DEFAULT_HANDOFF
    explore = (2_000, TOKENS_PER_READ, *(1_000,) * 6)
    assert main.buckets.exploration == sum(explore[:MAIN_READ_FILES])
    assert main.requests == CLAUDE_MAIN_REQUESTS
    assert main.buckets.writing == main.requests * OUTPUT_PER_REQUEST + 2 * DISPATCH_TOKENS
    assert native.p50_usd == pytest.approx(sum(item.p50_usd or 0.0 for item in native.roles))
    measured = (
        _role(Role.ORCHESTRATOR, fixed_tokens=33_000),
        _role(Role.ANALYST, fixed_tokens=21_000),
        roles[2],
    )
    found = envelope(replace(_inputs(), roles=measured, native=True))
    assert found.roles[0].fixed == FixedPrefix(33_000, True)
    assert found.roles[1].fixed == FixedPrefix(CLAUDE_SUBAGENT_FIXED[Role.ANALYST], False)
    launched = envelope(replace(_inputs(), roles=measured))
    assert launched.roles[0].fixed == FixedPrefix(21_000, True)
    single = envelope(
        replace(_inputs(), shape="single", roles=(_role(Role.ORCHESTRATOR),), native=True)
    )
    assert [item.role for item in single.roles] == [Role.ORCHESTRATOR]
    assert single.roles[0].fixed == FixedPrefix(CLAUDE_FIXED[Role.ORCHESTRATOR], False)
    assert single.roles[0].requests == CLAUDE_SINGLE_REQUESTS


def test_claude_requests_are_the_measured_counts_and_codex_requests_follow_the_plan() -> None:
    small = envelope(_inputs())
    wide = replace(_inputs(), edit_tokens=(500,) * 6, read_tokens=(1_000,) * 14)
    large = envelope(wide)
    launch = [CLAUDE_LAUNCH_REQUESTS[role] for role in (Role.ANALYST, Role.SENIOR, Role.TESTER)]
    assert [item.requests for item in small.roles] == launch
    assert [item.requests for item in large.roles] == launch
    assert small.p50_usd is not None and large.p50_usd is not None
    assert large.p50_usd > small.p50_usd
    codex_small = envelope(replace(_inputs(), provider=Provider.CODEX))
    codex_large = envelope(replace(wide, provider=Provider.CODEX))
    for before, after in zip(codex_small.roles, codex_large.roles, strict=True):
        assert after.requests >= before.requests
    assert codex_large.roles[0].requests > codex_small.roles[0].requests
    (writer,) = envelope(replace(_inputs(), roles=(_role(Role.SENIOR),))).roles
    explorer = CLAUDE_LAUNCH_REQUESTS[Role.ANALYST] + CLAUDE_LAUNCH_REQUESTS[Role.SENIOR]
    assert writer.requests == claude_requests(Role.SENIOR, explores=True) == explorer
    assert writer.buckets.exploration == small.roles[0].buckets.exploration
    assert claude_requests(Role.ORCHESTRATOR) == CLAUDE_SINGLE_REQUESTS
    assert claude_requests(Role.DOCS, subagent=True) == CLAUDE_SUBAGENT_REQUESTS[Role.DOCS]


def test_receivers_read_the_measured_number_of_files() -> None:
    roles = (_role(Role.ANALYST), _role(Role.SENIOR), _role(Role.TESTER), _role(Role.DOCS))
    plan = replace(_inputs(), roles=roles, edit_tokens=(700,) * 5, read_tokens=(1_000,) * 6)
    analyst, senior, tester, docs = envelope(plan).roles
    assert analyst.buckets.exploration == 5 * 700 + 6 * 1_000
    assert senior.buckets.exploration == SENIOR_READ_FILES * 700
    assert tester.buckets.exploration == TESTER_READ_FILES * 700
    assert docs.buckets.exploration == DOCS_FILES * TOKENS_PER_READ
    assert docs.requests == CLAUDE_LAUNCH_REQUESTS[Role.DOCS]
    assert docs.stops.max_reads == DOCS_FILES + 2
    assert DOCS_EDITS > 0
    few = envelope(replace(plan, edit_tokens=(700,), read_tokens=())).roles
    assert few[1].buckets.exploration == few[2].buckets.exploration == 700


def test_codex_receivers_keep_the_unmeasured_file_counts_and_output_per_request() -> None:
    roles = (_role(Role.ANALYST), _role(Role.SENIOR), _role(Role.TESTER), _role(Role.DOCS))
    plan = replace(
        _inputs(),
        roles=roles,
        provider=Provider.CODEX,
        edit_tokens=(700,) * 5,
        read_tokens=(1_000,) * 6,
    )
    analyst, senior, tester, docs = envelope(plan).roles
    assert analyst.buckets.exploration == 5 * 700 + 6 * 1_000
    assert senior.buckets.exploration == 5 * 700 + CODEX_REREAD_FILES * 1_000
    assert tester.buckets.exploration == 5 * 700
    assert docs.buckets.exploration == CODEX_DOCS_FILES * TOKENS_PER_READ
    written = (0, 5 * WRITE_TOKENS_PER_EDIT, TEST_WRITE_TOKENS, DOCS_WRITE_TOKENS)
    for item, deliverable in zip((analyst, senior, tester, docs), written, strict=True):
        assert item.buckets.writing == item.requests * CODEX_OUTPUT_PER_REQUEST + deliverable
    scout = envelope(replace(plan, shape=SCOUT_SHAPE, economy=RoleModel("luna", MID))).roles
    assert scout[1].buckets.exploration == 5 * 700
    claude = envelope(replace(plan, provider=Provider.CLAUDE)).roles
    assert claude[1].buckets.exploration == SENIOR_READ_FILES * 700
    assert claude[0].buckets.writing == claude[0].requests * OUTPUT_PER_REQUEST
    assert output_per_request(Provider.CODEX) == CODEX_OUTPUT_PER_REQUEST
    assert output_per_request(Provider.CLAUDE) == OUTPUT_PER_REQUEST


def test_each_role_uses_the_warmth_of_its_own_model() -> None:
    inputs = replace(
        _inputs(),
        warmth=0.0,
        model_warmth={"analyst-model": 0.8, "senior-model": 0.2},
    )
    result = envelope(inputs)
    analyst, senior, tester = result.roles
    assert (analyst.warmth, senior.warmth, tester.warmth) == (0.8, 0.2, 0.0)
    weights = [item.buckets.start + item.buckets.handoff for item in result.roles]
    expected = (0.8 * weights[0] + 0.2 * weights[1]) / sum(weights)
    assert result.warmth == pytest.approx(expected)
    cold = envelope(replace(inputs, model_warmth={}))
    assert cold.p50_usd is not None and result.p50_usd is not None
    assert result.p50_usd < cold.p50_usd
    dated = envelope(replace(inputs, model_warmth={"senior-model": 0.2, "analyst-model": 0.8}))
    assert dated.roles == result.roles


def test_a_launch_turn_rail_caps_the_planned_requests_and_stop_rules() -> None:
    wide = replace(_inputs(), shape="single", edit_tokens=(500,) * 30, roles=(_role(Role.SENIOR),))
    (default,) = envelope(wide).roles
    (railed,) = envelope(replace(wide, max_turns=3)).roles
    assert default.requests == CLAUDE_SINGLE_REQUESTS > 3
    assert (railed.requests, railed.stops.max_turns) == (3, 3)
    codex = replace(wide, provider=Provider.CODEX)
    (planned,) = envelope(codex).roles
    (capped,) = envelope(replace(codex, max_turns=12)).roles
    assert planned.requests > 12
    assert (capped.requests, capped.stops.max_turns) == (12, 12)


def test_unknown_sizes_use_the_read_size_and_the_read_budget_caps_exploration() -> None:
    quick = profile(Depth.QUICK, "feature")
    result = envelope(replace(_inputs(), depth=quick, edit_tokens=(), read_tokens=(0,) * 20))
    analyst = result.roles[0]
    assert analyst.buckets.exploration == quick.read_budget * TOKENS_PER_READ
    assert analyst.stops.max_reads == quick.read_budget


def test_stop_rules_stay_within_the_depth_limits() -> None:
    quick = profile(Depth.QUICK, "feature")
    wide = replace(_inputs(), shape="single", depth=quick, edit_tokens=(500,) * 30)
    (only,) = envelope(wide).roles
    assert only.requests == CLAUDE_SINGLE_REQUESTS
    assert only.stops.max_turns <= quick.max_turns
    assert only.stops.max_reads == quick.read_budget
    assert only.stops.max_output_tokens >= only.buckets.writing
    (codex,) = envelope(replace(wide, provider=Provider.CODEX)).roles
    assert codex.requests == codex.stops.max_turns == quick.max_turns


def test_unpriced_roles_leave_the_forecast_unknown() -> None:
    inputs = replace(
        _inputs(), roles=(_role(Role.ANALYST), _role(Role.SENIOR, None)), cap_usd=0.0001
    )
    result = envelope(inputs)
    assert (result.p50_usd, result.p90_usd, result.margin_usd) == (None, None, None)
    assert result.verdict is Verdict.UNKNOWN
    assert result.suggestions == ()
    assert forecast_record(inputs, result, "R1", "2026-09-28T00:00:00Z") is None


def test_uncapped_runs_are_comfortable_without_a_margin() -> None:
    result = envelope(replace(_inputs(), cap_usd=0.0))
    assert result.verdict is Verdict.COMFORTABLE
    assert result.margin_usd is None


def test_codex_teams_use_codex_fallbacks() -> None:
    result = envelope(replace(_inputs(), provider=Provider.CODEX))
    assert {item.fixed for item in result.roles} == {FixedPrefix(CODEX_FIXED, False)}


def test_role_factor_uses_same_type_history_only() -> None:
    features = tuple(RoleSample("feature", 2.0) for _ in range(3))
    fixes = (RoleSample("fix", 1.5), RoleSample("bug", 1.5), RoleSample("bug", 1.5))
    source = _role(Role.SENIOR, history=features)
    assert role_factor(source, "feature") == 2.0
    assert role_factor(source, "bug") == 1.0
    assert role_factor(replace(source, history=features + fixes), "bug") == 1.5
    assert role_factor(replace(source, history=features[:2]), "feature") == 1.0
    wild = tuple(RoleSample("feature", value) for value in (5.0, 6.0, 7.0))
    assert role_factor(replace(source, history=wild), "feature") == 4.0
    tiny = tuple(RoleSample("feature", value) for value in (0.1, 0.1, float("nan"), 0.1))
    assert role_factor(replace(source, history=tiny), "feature") == 0.25


def test_history_factor_scales_the_role_forecast() -> None:
    plain = envelope(_inputs())
    history = tuple(RoleSample("feature", 2.0) for _ in range(3))
    roles = (_role(Role.ANALYST, history=history), _role(Role.SENIOR, DEAR), _role(Role.TESTER))
    scaled = envelope(replace(_inputs(), roles=roles))
    assert scaled.roles[0].factor == 2.0
    assert scaled.roles[0].p50_usd == pytest.approx((plain.roles[0].p50_usd or 0.0) * 2.0)
    assert scaled.roles[1].p50_usd == pytest.approx(plain.roles[1].p50_usd or 0.0)


def test_fixes_plan_a_warm_repair_turn_as_a_p90_contingency() -> None:
    assert is_fix("bug")
    assert is_fix("fix")
    assert not is_fix("feature")
    feature = envelope(_inputs())
    fix = envelope(replace(_inputs(), task_type="bug", repairable=True))
    assert not any(item.repair for item in feature.roles)
    repair = fix.roles[-1]
    assert (repair.role, repair.repair) == (Role.SENIOR, True)
    assert repair.buckets.handoff == 0
    assert repair.buckets.verification > 0
    assert repair.requests == fix.roles[1].requests
    assert REPAIR_WARMTH == 1.0
    assert feature.p50_usd is not None and repair.p50_usd is not None and repair.p50_usd > 0
    assert fix.p50_usd == pytest.approx(feature.p50_usd)
    assert fix.p90_usd == pytest.approx(feature.p50_usd * fix.spread + repair.p50_usd)
    assert (repair.share, repair.p90_usd) == (0.0, repair.p50_usd)
    assert sum(item.share for item in fix.roles) == pytest.approx(1.0)
    assert fix.p90_usd == pytest.approx(sum(item.p90_usd or 0.0 for item in fix.roles))
    single = envelope(
        replace(
            _inputs(),
            task_type="bug",
            shape="single",
            roles=(_role(Role.SENIOR),),
            repairable=True,
        )
    )
    assert [item.repair for item in single.roles] == [False, True]
    readers = envelope(
        replace(
            _inputs(),
            task_type="bug",
            roles=(_role(Role.ANALYST), _role(Role.TESTER)),
            repairable=True,
        )
    )
    assert not any(item.repair for item in readers.roles)


def test_the_repair_contingency_is_planned_only_where_a_repair_turn_can_run() -> None:
    assert repair_possible("pipeline", False, ("npm test",))
    assert not repair_possible("pipeline", False, ())
    assert not repair_possible("pipeline", True, ("npm test",))
    assert not repair_possible("single", False, ("npm test",))
    fix = replace(_inputs(), task_type="bug")
    plain = envelope(fix)
    assert plain.p50_usd is not None
    assert not any(item.repair for item in plain.roles)
    assert plain.p90_usd == pytest.approx(plain.p50_usd * DEFAULT_SPREAD)
    repairable = replace(fix, repairable=True)
    assert any(item.repair for item in envelope(repairable).roles)
    calibrated = envelope(replace(repairable, calibration=(1.5,) * 5))
    assert calibrated.p50_usd is not None and calibrated.spread_samples == 5
    assert not any(item.repair for item in calibrated.roles)
    assert calibrated.p90_usd == pytest.approx(calibrated.p50_usd * 1.5)


def _tight() -> EnvelopeInputs:
    roles = (
        _role(Role.ANALYST, cheaper=RoleModel("analyst-cheap", CHEAP)),
        _role(Role.SENIOR, DEAR, cheaper=RoleModel("senior-cheap", MID)),
        _role(Role.TESTER),
    )
    return replace(
        _inputs(),
        roles=roles,
        cap_usd=0.01,
        read_tokens=(3_000,) * 18,
        economy=RoleModel("scout-model", CHEAP),
    )


def test_tight_envelopes_suggest_each_remedy_with_its_p90() -> None:
    inputs = _tight()
    result = envelope(inputs)
    assert result.verdict is Verdict.INFEASIBLE
    assert result.p90_usd is not None
    kinds = {item.kind: item for item in result.suggestions}
    assert set(kinds) == set(SuggestionKind)
    depth = kinds[SuggestionKind.DEPTH]
    shallower = envelope(replace(inputs, depth=profile(Depth.QUICK, "feature")))
    assert depth.p90_usd == shallower.p90_usd
    assert depth.message == msg(
        "envelope.suggest.depth", depth=msg("depth.quick"), p90=dollars(depth.p90_usd)
    )
    tier = kinds[SuggestionKind.TIER]
    assert tier.message == msg(
        "envelope.suggest.tier",
        role=msg("role.senior"),
        model="senior-cheap",
        p90=dollars(tier.p90_usd),
    )
    where = kinds[SuggestionKind.WHERE]
    narrowed = envelope(replace(inputs, read_tokens=inputs.read_tokens[:10]))
    assert where.p90_usd == narrowed.p90_usd
    assert where.message == msg("envelope.suggest.where", files="10", p90=dollars(where.p90_usd))
    scout = kinds[SuggestionKind.SCOUT]
    assert scout.p90_usd == envelope(replace(inputs, shape=SCOUT_SHAPE)).p90_usd
    assert scout.message == msg("envelope.suggest.scout", p90=dollars(scout.p90_usd))
    for item in result.suggestions:
        assert item.p90_usd < result.p90_usd
        assert item.saving_usd == pytest.approx(result.p90_usd - item.p90_usd)
    savings = [item.saving_usd for item in result.suggestions]
    assert savings == sorted(savings, reverse=True)


def test_suggestions_are_left_out_when_they_do_not_apply() -> None:
    quick = envelope(
        replace(_tight(), depth=profile(Depth.QUICK, "feature"), read_tokens=(3_000,) * 3)
    )
    kinds = {item.kind for item in quick.suggestions}
    assert SuggestionKind.DEPTH not in kinds
    assert SuggestionKind.WHERE not in kinds
    plain = envelope(
        replace(_tight(), roles=(_role(Role.ANALYST), _role(Role.SENIOR)), economy=None)
    )
    assert SuggestionKind.TIER not in {item.kind for item in plain.suggestions}
    scouted = envelope(replace(_tight(), shape=SCOUT_SHAPE))
    assert SuggestionKind.SCOUT not in {item.kind for item in scouted.suggestions}
    writing = envelope(replace(_tight(), read_tokens=(), edit_tokens=(10,) * 3))
    assert SuggestionKind.SCOUT not in {item.kind for item in writing.suggestions}
    assert envelope(replace(_tight(), cap_usd=100.0)).suggestions == ()


def test_the_tier_suggestion_names_the_costliest_role_with_a_cheaper_model() -> None:
    roles = (
        _role(Role.ANALYST, DEAR, cheaper=RoleModel("analyst-cheap", CHEAP)),
        _role(Role.SENIOR, CHEAP),
    )
    result = envelope(replace(_tight(), roles=roles))
    tier = next(item for item in result.suggestions if item.kind is SuggestionKind.TIER)
    assert dict(tier.message.params)["role"] == msg("role.analyst")


def test_the_scout_shape_hands_the_senior_a_pack_instead_of_rereads() -> None:
    result = envelope(replace(_tight(), shape=SCOUT_SHAPE))
    scout, senior, _ = result.roles
    assert scout.model == "scout-model"
    assert scout.role is Role.SCOUT
    assert senior.buckets.handoff == SCOUT_PACK_TOKENS
    assert senior.buckets.exploration == 2_000 + TOKENS_PER_READ
    single = envelope(replace(_tight(), shape=SCOUT_SHAPE, roles=(_role(Role.ORCHESTRATOR),)))
    assert [item.role for item in single.roles] == [Role.SCOUT, Role.SENIOR]
    fallback = envelope(
        replace(_tight(), shape=SCOUT_SHAPE, roles=(_role(Role.SENIOR),), economy=None)
    )
    assert [item.role for item in fallback.roles] == [Role.SENIOR]


def test_forecast_record_serializes_buckets_roles_and_numeric_features() -> None:
    inputs = replace(_inputs(), task_type="bug", repairable=True)
    result = envelope(inputs)
    record = forecast_record(inputs, result, "R1", "2026-09-28T00:00:00Z", JEV_SOURCE)
    assert record is not None
    assert (record.run_id, record.provider, record.task_type) == ("R1", "claude", "bug")
    assert (record.depth, record.shape, record.source) == ("normal", "pipeline", JEV_SOURCE)
    assert (record.p50_usd, record.p90_usd) == (result.p50_usd, result.p90_usd)
    assert (record.cap_usd, record.verdict) == (10.0, result.verdict.value)
    assert json.loads(record.buckets) == result.buckets.payload()
    roles = json.loads(record.per_role)
    assert [item["role"] for item in roles] == ["analyst", "senior", "tester", "senior"]
    assert roles[-1]["repair"] is True
    features = json.loads(record.features)
    assert all(
        isinstance(value, int | float) and not isinstance(value, bool)
        for value in features.values()
    )
    assert features["edit_files"] == 2
    assert features["unknown_sizes"] == 1
    assert features["repair"] == 1
    assert features["margin_usd"] == pytest.approx(result.margin_usd)
    uncapped = envelope(replace(inputs, cap_usd=0.0))
    stored = forecast_record(inputs, uncapped, "R2", "2026-09-28T00:00:00Z")
    assert stored is not None
    assert stored.cap_usd is None
    assert stored.source == ENVELOPE_SOURCE
    assert "margin_usd" not in json.loads(stored.features)


def test_a_routed_scout_role_prices_the_exploration_and_the_pipeline_ignores_it() -> None:
    roles = (
        _role(Role.ANALYST),
        _role(Role.SCOUT, CHEAP),
        _role(Role.SENIOR, DEAR),
        _role(Role.TESTER),
    )
    inputs = replace(_inputs(), roles=roles, economy=RoleModel("other-economy", MID))
    scout, senior, tester = envelope(replace(inputs, shape=SCOUT_SHAPE)).roles
    assert (scout.role, scout.model) == (Role.SCOUT, "scout-model")
    assert scout.buckets.exploration == 2_000 + TOKENS_PER_READ + 6 * 1_000
    assert scout.requests == CLAUDE_LAUNCH_REQUESTS[Role.SCOUT]
    assert scout.fixed.tokens == fixed_prefix(Provider.CLAUDE, Role.SCOUT).tokens
    assert senior.buckets.handoff == SCOUT_PACK_TOKENS
    assert tester.role is Role.TESTER
    pipeline = envelope(inputs).roles
    assert [item.role for item in pipeline] == [Role.ANALYST, Role.SENIOR, Role.TESTER]


REQUEST_TOKENS = 3_000


def _grown(inputs: EnvelopeInputs) -> list[int]:
    plain = envelope(inputs).roles
    asked = envelope(replace(inputs, request_tokens=REQUEST_TOKENS)).roles
    assert [(item.role, item.repair) for item in asked] == [
        (item.role, item.repair) for item in plain
    ]
    return [
        after.buckets.start - before.buckets.start
        for before, after in zip(plain, asked, strict=True)
    ]


def test_the_request_joins_the_context_of_each_launch_that_receives_it() -> None:
    assert envelope(replace(_inputs(), request_tokens=0)) == envelope(_inputs())
    assert _grown(_inputs()) == [REQUEST_TOKENS] * 3
    roles = (_role(Role.ORCHESTRATOR), _role(Role.ANALYST), _role(Role.SENIOR))
    assert _grown(replace(_inputs(), roles=roles, native=True)) == [REQUEST_TOKENS, 0, 0]
    scouted = replace(
        _inputs(),
        shape=SCOUT_SHAPE,
        roles=(_role(Role.ORCHESTRATOR), _role(Role.SCOUT, CHEAP), _role(Role.SENIOR, DEAR)),
        native=True,
    )
    assert _grown(scouted) == [REQUEST_TOKENS, 0, 0]
    single = replace(_inputs(), shape="single", roles=(_role(Role.ORCHESTRATOR),))
    assert _grown(single) == [REQUEST_TOKENS]
    fix = replace(_inputs(), task_type="bug", repairable=True)
    assert _grown(fix) == [REQUEST_TOKENS] * 4
    plain = envelope(_inputs())
    asked = replace(_inputs(), request_tokens=REQUEST_TOKENS, parts=RequestParts(PartKind.PHASE, 5))
    priced = envelope(asked)
    assert plain.p50_usd is not None and priced.p50_usd is not None
    assert priced.p50_usd > plain.p50_usd
    assert envelope(replace(_inputs(), parts=RequestParts(PartKind.PHASE, 5))) == plain
    features = envelope_features(asked, priced)
    assert (features["request_tokens"], features["request_parts"]) == (REQUEST_TOKENS, 5)
    assert envelope_features(_inputs(), plain)["request_tokens"] == 0
    assert envelope_features(_inputs(), plain)["request_parts"] == 0
