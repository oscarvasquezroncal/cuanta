from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cuanta.domain.anatomy import AnatomyReport, Phase, UsagePhase
from cuanta.domain.bench import BenchTask, Condition, ProofRecord, RunMetrics
from cuanta.domain.bench_proof import (
    ARMS,
    DEFAULT_OVERSHOOT_USD,
    NOT_LAUNCHED,
    Comparison,
    FinishVerdict,
    Verdict,
    arm_caps,
    arm_config,
    arm_named,
    arm_settings,
    committed_runs,
    cut_by_cap_when_finish_possible,
    ended_by_cap,
    exploration_leak,
    finish_check,
    launched_cost,
    overshoot_allowance,
    parse_comparison,
    plan_proof,
    planned_arm,
    proof_markdown,
    proof_record,
    queue_warm_share,
    read_discipline_saving,
    scout_saving,
    targets,
    targets_payload,
    worst_case,
)
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.config import Config
from cuanta.domain.errors import DomainFailure
from cuanta.domain.governor import (
    Projection,
    Reaction,
    ReactionKind,
    ReactionTaken,
    Trigger,
)
from cuanta.domain.governor_report import BlockedCalls
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.mandate import MandateRequest
from cuanta.domain.read_efficiency import ReadCall, read_calls
from cuanta.domain.routing import Role

TASK = BenchTask("t1", ("proof",), "bugfix", MandateRequest(type="feature", what="w"), "p", {})
OTHER = replace(TASK, name="t2")


def record(comparison: Comparison, arm: str) -> ProofRecord:
    return ProofRecord(comparison.value, arm, 1.0)


def run(
    proof: ProofRecord,
    cost: float | None = 0.5,
    accepted: bool = True,
    capped: bool = False,
    rep: int = 1,
    costs: tuple[float | None, ...] = (),
) -> RunMetrics:
    events = tuple(
        UsagePhase(
            index, "r", "s", "main", f"2026-09-29T10:00:{index:02d}", Phase.WRITING, 10, value
        )
        for index, value in enumerate(costs, 1)
    )
    return RunMetrics(
        "t1",
        Condition.CUANTA,
        rep,
        f"run-{proof.arm}-{rep}",
        accepted,
        capped,
        0,
        0,
        0,
        0,
        cost,
        1.0,
        0,
        0,
        anatomy=AnatomyReport(events=events),
        proof=proof,
    )


def discipline(
    arm: str, leak: int | None, blocked: int | None = None, rule: int | None = None
) -> RunMetrics:
    return run(
        replace(
            record(Comparison.READ_DISCIPLINE, arm),
            leak_tokens=leak,
            blocked_reads=None if blocked is None else 2,
            blocked_tokens=blocked,
            rule_tokens=rule,
        )
    )


def test_read_discipline_saving_compares_the_mean_leak_of_both_arms() -> None:
    met = read_discipline_saving(
        [
            discipline("discipline-on", 1_000, 3_000, 0),
            discipline("discipline-on", 1_000, 3_000, 0),
            discipline("discipline-off", 4_000, rule=2_500),
        ]
    )
    assert met.verdict is Verdict.MET and met.measured == pytest.approx(0.75)
    assert "1,000 tokens per run with the discipline (2 runs)" in met.summary
    assert "reads that break the discipline's rule 0 against 2,500" in met.summary
    assert "4 reads blocked, ~3,000 tokens avoided per run (estimate)" in met.summary
    assert "counting blocked" not in met.summary
    missed = read_discipline_saving(
        [discipline("discipline-on", 3_000), discipline("discipline-off", 4_000)]
    )
    assert missed.verdict is Verdict.MISSED and missed.measured == pytest.approx(0.25)
    assert "blocked reads n/a" in missed.summary and "rule-breaking reads n/a" in missed.summary


@pytest.mark.parametrize(
    ("runs", "why"),
    [
        ([discipline("discipline-on", 10)], "needs runs of discipline-on and discipline-off"),
        (
            [discipline("discipline-on", None), discipline("discipline-off", 10)],
            "read telemetry is missing",
        ),
        (
            [discipline("discipline-on", 0), discipline("discipline-off", 0)],
            "no exploration leak without the discipline",
        ),
    ],
)
def test_read_discipline_saving_is_na_when_the_records_cannot_decide(
    runs: list[RunMetrics], why: str
) -> None:
    result = read_discipline_saving(runs)
    assert result.verdict is Verdict.UNKNOWN and result.measured is None
    assert why in result.summary


def scouted(
    arm: str, cost: float | None, accepted: bool = True, mode: str = "native"
) -> RunMetrics:
    return run(
        replace(
            record(Comparison.SCOUT, arm),
            scout_mode=mode if arm == "scout" else "",
            scout_dispatched=True if arm == "scout" else None,
        ),
        cost,
        accepted,
    )


def test_scout_saving_needs_forty_percent_and_acceptance_parity() -> None:
    met = scout_saving([scouted("scout", 0.30), scouted("pipeline", 0.60)])
    assert met.verdict is Verdict.MET and met.measured == pytest.approx(0.5)
    assert "accepted 1/1 against 1/1 (parity)" in met.summary
    assert "scout mode native, scout ran in 1/1" in met.summary
    cheaper_but_worse = scout_saving(
        [scouted("scout", 0.10, accepted=False), scouted("pipeline", 0.60)]
    )
    assert cheaper_but_worse.verdict is Verdict.MISSED
    assert "below parity" in cheaper_but_worse.summary
    too_close = scout_saving([scouted("scout", 0.50), scouted("pipeline", 0.60)])
    assert too_close.verdict is Verdict.MISSED
    assert too_close.measured == pytest.approx(1 - 0.5 / 0.6)
    better = scout_saving([scouted("scout", 0.20), scouted("pipeline", 0.60, accepted=False)])
    assert better.verdict is Verdict.MET
    unknown = scout_saving([scouted("scout", None), scouted("pipeline", 0.60)])
    assert unknown.verdict is Verdict.UNKNOWN and "cost is unknown" in unknown.summary
    alone = scout_saving([scouted("scout", 0.2)])
    assert alone.verdict is Verdict.UNKNOWN


@pytest.mark.parametrize(
    ("scout", "why"),
    [
        (scouted("scout", 0.10, mode=""), "the scout did not run in 1 of 1 scout-arm runs"),
        (
            run(
                replace(
                    record(Comparison.SCOUT, "scout"), scout_mode="native", scout_dispatched=False
                ),
                0.10,
            ),
            "the scout did not run in 1 of 1",
        ),
        (scouted("scout", 0.10, accepted=False), "neither arm has an accepted run"),
    ],
)
def test_scout_saving_is_na_without_a_scout_or_any_accepted_run(
    scout: RunMetrics, why: str
) -> None:
    pipeline = scouted("pipeline", 0.60, accepted=scout.accepted)
    result = scout_saving([scout, pipeline])
    assert result.verdict is Verdict.UNKNOWN and result.measured is None
    assert why in result.summary


def queued(arm: str, read: int | None, fixed: int | None) -> RunMetrics:
    return run(
        replace(record(Comparison.WARM_QUEUE, arm), first_cache_read=read, fixed_prefix=fixed)
    )


def test_queue_warm_share_reads_the_second_mandates_first_request() -> None:
    met = queue_warm_share(
        [queued("queue-first", 0, 20_000), queued("queue-second", 18_000, 20_000)]
    )
    assert met.verdict is Verdict.MET and met.measured == pytest.approx(0.9)
    assert "read 90% of its fixed prefix" in met.summary and "first mandates: 0%" in met.summary
    capped = queue_warm_share([queued("queue-second", 25_000, 20_000)])
    assert capped.measured == 1.0
    lowest = queue_warm_share(
        [queued("queue-second", 19_000, 20_000), queued("queue-second", 10_000, 20_000)]
    )
    assert lowest.verdict is Verdict.MISSED and lowest.measured == pytest.approx(0.5)
    missing = queue_warm_share([queued("queue-second", None, 20_000)])
    assert missing.verdict is Verdict.UNKNOWN
    zero = queue_warm_share([queued("queue-second", 100, 0)])
    assert zero.verdict is Verdict.UNKNOWN
    assert queue_warm_share([queued("queue-first", 1, 1)]).verdict is Verdict.UNKNOWN


STEADY = (0.2, 0.2, 0.2, 0.2, 0.25)


def tight(
    costs: tuple[float | None, ...],
    capped: bool = True,
    sent: bool = False,
    spent: float | None = None,
    end: str = "",
    answered: bool | None = None,
) -> RunMetrics:
    proof = replace(
        record(Comparison.FINISH, "tight-cap"),
        finish_sent=sent,
        finish_spent_usd=spent,
        end_reason=end,
        answered=answered,
    )
    return run(
        proof, sum(value or 0 for value in costs), accepted=False, capped=capped, costs=costs
    )


@pytest.mark.parametrize(
    ("metrics", "verdict", "trigger", "after"),
    [
        (tight(STEADY, capped=False), FinishVerdict.FINISHED, 3, None),
        (tight(STEADY), FinishVerdict.NOT_SENT, 3, None),
        (tight(STEADY, sent=True, spent=0.6), FinishVerdict.IGNORED, 3, 2),
        (tight(STEADY, sent=True, spent=0.8), FinishVerdict.TOO_LATE, 3, 1),
        (tight(STEADY, sent=True), FinishVerdict.SEND_UNKNOWN, 3, None),
        (tight((0.3, 0.8)), FinishVerdict.NO_ROOM, 2, None),
        (tight(()), FinishVerdict.NO_TELEMETRY, None, None),
        (tight((0.2, None, 0.9)), FinishVerdict.NO_TELEMETRY, None, None),
        (
            tight(STEADY, capped=False, end="error_max_budget_usd"),
            FinishVerdict.NOT_SENT,
            3,
            None,
        ),
        (
            tight((0.2, 0.2, 0.2, 0.2, 0.21), sent=True, spent=0.6, end="success"),
            FinishVerdict.FINISHED,
            3,
            2,
        ),
        (
            tight(STEADY, sent=True, spent=0.6, end="error_max_budget_usd", answered=True),
            FinishVerdict.FINISHED,
            3,
            2,
        ),
        (
            tight(STEADY, capped=False, sent=True, spent=0.6, end="error_max_budget_usd"),
            FinishVerdict.IGNORED,
            3,
            2,
        ),
    ],
)
def test_finish_checks_judge_each_cut_run_from_its_request_costs(
    metrics: RunMetrics, verdict: FinishVerdict, trigger: int | None, after: int | None
) -> None:
    check = finish_check(metrics)
    assert check.verdict is verdict
    assert (check.trigger, check.after_send) == (trigger, after)
    assert check.missed is (
        verdict in {FinishVerdict.NOT_SENT, FinishVerdict.TOO_LATE, FinishVerdict.IGNORED}
    )


def test_a_graceful_finish_past_the_cap_is_not_a_cut() -> None:
    finished = tight((0.2, 0.2, 0.2, 0.2, 0.21), sent=True, spent=0.6, end="success")
    assert finished.cost_usd == pytest.approx(1.01) and finished.capped
    check = finish_check(finished)
    assert not check.cut and not check.missed
    assert cut_by_cap_when_finish_possible([finished]).verdict is Verdict.MET
    assert not ended_by_cap(finished.proof, True)
    assert ended_by_cap(replace(record(Comparison.FINISH, "tight-cap"), end_reason=""), True)
    cut = replace(record(Comparison.FINISH, "tight-cap"), end_reason="error_max_budget_usd")
    assert ended_by_cap(cut, False) and not ended_by_cap(replace(cut, answered=True), False)
    assert not ended_by_cap(replace(cut, end_reason="error_max_turns"), True)


def test_cut_by_cap_when_finish_possible_is_met_only_without_a_missed_finish() -> None:
    met = cut_by_cap_when_finish_possible([tight(STEADY, capped=False), tight((0.3, 0.8))])
    assert met.verdict is Verdict.MET and met.measured == 0.0
    assert met.summary.startswith("1 of 2 runs cut by their cap; 0 cut although")
    assert "the finish rule fired in 2 of 2 runs" in met.summary
    assert [check.verdict for check in met.finishes] == [
        FinishVerdict.FINISHED,
        FinishVerdict.NO_ROOM,
    ]
    missed = cut_by_cap_when_finish_possible([tight(STEADY), tight(())])
    assert missed.verdict is Verdict.MISSED and missed.measured == 1.0
    assert "1 cut runs cannot be judged" in missed.summary
    unknown = cut_by_cap_when_finish_possible([tight(())])
    assert unknown.verdict is Verdict.UNKNOWN
    assert cut_by_cap_when_finish_possible([]).verdict is Verdict.UNKNOWN
    loose = cut_by_cap_when_finish_possible([tight((0.1, 0.1), capped=False)])
    assert loose.verdict is Verdict.UNKNOWN and loose.measured == 0.0
    assert "fired in 0 of 1 runs" in loose.summary and "never tight" in loose.summary


def test_plans_pair_the_queue_back_to_back_and_cap_each_arm() -> None:
    caps = arm_caps(["queue-second=0.2"], Comparison.WARM_QUEUE)
    planned = plan_proof([TASK, OTHER], Comparison.WARM_QUEUE, 2, 5, caps, 0.4)
    assert planned == plan_proof([TASK, OTHER], Comparison.WARM_QUEUE, 2, 5, caps, 0.4)
    assert [item.order for item in planned] == list(range(1, 9))
    pairs = [planned[index : index + 2] for index in range(0, 8, 2)]
    for first, second in pairs:
        assert (first.arm, second.arm) == ("queue-first", "queue-second")
        assert first.group == second.group and (first.task, first.rep) == (second.task, second.rep)
        assert (first.cap_usd, second.cap_usd) == (0.4, 0.2)
        assert first.condition is Condition.CUANTA
    assert len({item.group for item in planned}) == 4
    assert worst_case(planned, 0.0) == pytest.approx(2.4)
    assert worst_case(planned, 1.5) == 1.5
    assert committed_runs(planned) == 2
    assert worst_case(planned, 0.0, 0.1) == pytest.approx(3.2)
    assert worst_case(planned, 1.5, 0.1) == pytest.approx(1.7)
    assert worst_case(planned, 0.5, 0.1) == 0.0
    single = plan_proof([TASK], Comparison.SCOUT, 2, 1, {}, 0.8)
    assert sorted((item.arm, item.rep) for item in single) == [
        ("pipeline", 1),
        ("pipeline", 2),
        ("scout", 1),
        ("scout", 2),
    ]
    assert len({item.group for item in single}) == 4
    assert all(item.condition is Condition.ROUTED for item in single)
    interleaved = [replace(item, rep=rep) for item, rep in zip(single, (1, 2, 1, 2), strict=True)]
    assert committed_runs(interleaved) == 3
    contiguous = [replace(item, rep=rep) for item, rep in zip(single, (1, 1, 2, 2), strict=True)]
    assert committed_runs(contiguous) == 2
    assert worst_case(contiguous, 2.0, 0.1) == pytest.approx(2.2)
    assert worst_case(interleaved, 2.0, 0.1) == pytest.approx(2.3)
    assert worst_case(interleaved, 1.0, 0.1) == 0.0


def test_the_overshoot_allowance_is_the_largest_measured_with_a_floor() -> None:
    assert overshoot_allowance({}) == DEFAULT_OVERSHOOT_USD
    assert overshoot_allowance({"sonnet": (0.02, 0.17), "haiku": (0.01,)}) == 0.17
    assert overshoot_allowance({"sonnet": (0.02,)}) == DEFAULT_OVERSHOOT_USD


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (["scout=0.5"], "names no arm of read-discipline"),
        (["discipline-on"], "names no arm"),
        (["discipline-on=abc"], "has no amount"),
        (["discipline-on=0"], "must be positive"),
        (["discipline-on=nan"], "must be positive"),
    ],
)
def test_arm_caps_refuse_unknown_arms_and_bad_amounts(values: list[str], message: str) -> None:
    with pytest.raises(DomainFailure, match=message):
        arm_caps(values, Comparison.READ_DISCIPLINE)


def test_comparisons_arms_and_config_overrides() -> None:
    assert parse_comparison(" warm-queue ") is Comparison.WARM_QUEUE
    with pytest.raises(DomainFailure, match="unknown comparison"):
        parse_comparison("everything")
    assert arm_caps(["discipline-off=0.3"], Comparison.READ_DISCIPLINE) == {"discipline-off": 0.3}
    assert {arm.name for arms in ARMS.values() for arm in arms} == {
        "discipline-on",
        "discipline-off",
        "scout",
        "pipeline",
        "queue-first",
        "queue-second",
        "tight-cap",
    }
    on = arm_named("discipline-on")
    off = arm_named("discipline-off")
    assert on is not None and off is not None and arm_named("nope") is None
    assert arm_config(Config(), on).read_discipline is True
    disabled = arm_config(Config(), off)
    assert not disabled.read_discipline and not disabled.pipeline_read_discipline
    scout = arm_named("scout")
    assert scout is not None and arm_config(Config(), scout) == Config()
    assert arm_config(Config(scout_mode="launch"), scout).scout_mode == "native"
    tight_cap = arm_named("tight-cap")
    assert tight_cap is not None and arm_config(Config(governor=False), tight_cap).governor
    assert arm_config(Config(), None) == Config()
    assert arm_settings(tight_cap) == "runs.governor=true"
    assert arm_settings(scout) == "runs.scout_mode=native, shape scout"
    assert arm_settings(off) == "runs.read_discipline=false, runs.pipeline_read_discipline=false"
    queue = arm_named("queue-first")
    assert queue is not None and arm_settings(queue) == "the copy's config"
    planned = plan_proof([TASK], Comparison.FINISH, 1, 1, {}, 0.1)
    assert planned_arm(planned[0]) == arm_named("tight-cap") and planned_arm(None) is None
    with pytest.raises(ValueError, match="not defined"):
        planned_arm(replace(planned[0], arm="nope"))


def test_leak_counts_reads_outside_the_plan_and_reads_that_break_the_rule() -> None:
    plan = ChangePlan(edit=(EditTarget("boltons/strutils.py", 0.9),), read=("tests/*.py",))
    whole = {"file_path": "boltons/strutils.py"}
    ranged = {"file_path": "boltons/strutils.py", "offset": 1, "limit": 200}
    calls = (
        ReadCall("Read", "boltons/strutils.py", 5_000, ranged),
        ReadCall("Read", "tests/test_strutils.py", 800, {}),
        ReadCall("Read", "boltons/iterutils.py", 2_000, {}),
        ReadCall("Read", "README.md", 300, {}),
    )
    lines = {"boltons/strutils.py": 1_500, "boltons/iterutils.py": 90}
    assert exploration_leak(calls, plan, lines) == (2_300, 0)
    unranged = (ReadCall("Read", "boltons/strutils.py", 9_000, whole), *calls[1:])
    assert exploration_leak(unranged, plan, lines) == (11_300, 9_000)
    assert exploration_leak(unranged, plan, lines, 2_000) == (2_300, 0)
    tree = ReadCall("Grep", ".", 4_000, {"path": ".", "output_mode": "content"}, search=True)
    scoped = ReadCall(
        "Grep", "boltons", 700, {"path": "boltons", "output_mode": "content"}, search=True
    )
    paged = ReadCall("mcp__cuanta__page", "boltons/iterutils.py", 100, {"lines": "1-40"})
    assert exploration_leak((tree, scoped, paged), plan, lines) == (4_100, 4_000)
    assert exploration_leak((), plan, lines) == (0, 0)


def test_read_calls_count_each_successful_read_once_inside_the_project() -> None:
    root = "C:/work/boltons"
    events = [
        LedgerEvent(
            run_id="r",
            kind="tool_result",
            tool_name="Read",
            tool_use_id="a",
            file_path=f"{root}/boltons/strutils.py",
            tool_result_bytes=4_000,
            success=True,
        ),
        LedgerEvent(
            run_id="r",
            kind="tool_result",
            tool_name="Read",
            tool_use_id="a",
            file_path=f"{root}/boltons/strutils.py",
            tool_result_bytes=4_000,
            success=True,
        ),
        LedgerEvent(
            run_id="r",
            kind="tool_result",
            tool_name="Read",
            tool_use_id="b",
            file_path="C:/elsewhere/secret.py",
            tool_result_bytes=900,
            success=True,
        ),
        LedgerEvent(
            run_id="r",
            kind="tool_result",
            tool_name="Read",
            tool_use_id="c",
            file_path=f"{root}/README.md",
            tool_result_bytes=0,
            success=False,
        ),
        LedgerEvent(
            run_id="r",
            kind="tool_result",
            tool_name="Edit",
            tool_use_id="d",
            file_path=f"{root}/boltons/strutils.py",
            tool_result_bytes=100,
            success=True,
        ),
        LedgerEvent(run_id="r", kind="tool_use", tool_name="Read", tool_use_id="e"),
        LedgerEvent(
            run_id="r",
            kind="tool_result",
            tool_name="Grep",
            tool_use_id="f",
            file_path=root,
            tool_result_bytes=8_000,
            success=True,
            raw=json.dumps({"tool_parameters": {"path": root, "output_mode": "content"}}),
        ),
        LedgerEvent(
            run_id="r",
            kind="tool_result",
            tool_name="Grep",
            tool_use_id="g",
            tool_result_bytes=400,
            success=True,
            raw=json.dumps({"tool_parameters": {"pattern": "slug"}}),
        ),
    ]
    found = read_calls(events, root)
    assert [(call.tool, call.path, call.tokens, call.search) for call in found] == [
        ("Read", "boltons/strutils.py", 1_000, False),
        ("Grep", ".", 2_000, True),
        ("Grep", ".", 100, True),
    ]
    assert found[1].inputs["path"] == "." and "path" not in found[2].inputs


def finish_reaction(sent: bool, spent: float | None) -> ReactionTaken:
    projection = Projection(spent, False, 1.0, 0.2, 0.5, 2, None, None, None)
    reaction = Reaction(Role.ORCHESTRATOR, ReactionKind.FINISH_NOW, Trigger.SHARE, 30.0, projection)
    return ReactionTaken(reaction, "run", sent)


def test_proof_records_take_the_sent_finish_the_scout_and_the_first_request() -> None:
    arm = arm_named("tight-cap")
    assert arm is not None
    planned = plan_proof([TASK], Comparison.FINISH, 1, 1, {"tight-cap": 0.15}, 0.5)[0]
    made = proof_record(
        arm,
        planned,
        "error_max_budget_usd",
        1_200,
        BlockedCalls(reads=2, tokens=9_000),
        {"mode": "native", "dispatched": False},
        18_000,
        20_000,
        (finish_reaction(False, 0.5), finish_reaction(True, 0.6)),
        rule_tokens=700,
        answered=False,
    )
    assert made == ProofRecord(
        "finish",
        "tight-cap",
        0.15,
        "error_max_budget_usd",
        1_200,
        2,
        9_000,
        "native",
        False,
        18_000,
        20_000,
        True,
        0.6,
        planned.group,
        0,
        700,
        False,
    )
    quiet = proof_record(arm, planned, "", None, None, None, None, None, ())
    assert (quiet.finish_sent, quiet.finish_spent_usd, quiet.blocked_reads) == (False, None, None)
    assert quiet.scout_mode == "" and quiet.scout_dispatched is None
    assert (quiet.rule_tokens, quiet.answered) == (None, None)


def test_a_run_that_never_launched_spent_nothing() -> None:
    skipped = run(replace(record(Comparison.SCOUT, "scout"), end_reason=NOT_LAUNCHED), None)
    assert launched_cost(skipped) == 0.0
    assert launched_cost(run(record(Comparison.SCOUT, "scout"), None)) is None
    assert launched_cost(run(record(Comparison.SCOUT, "scout"), 0.4)) == 0.4
    assert not ended_by_cap(skipped.proof, False)


def test_the_report_and_payload_state_each_target_from_the_records() -> None:
    metrics = [
        discipline("discipline-on", 1_000, 3_000),
        discipline("discipline-off", 4_000),
        tight(STEADY),
    ]
    found = targets(metrics)
    assert [item.comparison for item in found] == [Comparison.READ_DISCIPLINE, Comparison.FINISH]
    text = proof_markdown(metrics)
    assert "## Targets" in text
    assert "| read-discipline | met | exploration leak 1,000 tokens" in text
    assert "Rule-breaking tokens" in text and "whole-tree content Grep" in text
    assert "| finish | missed | 1 of 1 runs cut by their cap" in text
    assert "### Graceful finish per run" in text
    assert "cut, finish not sent" in text
    assert "| t1 | discipline-on | 1 | $1.0000 |" in text
    payload = targets_payload(found)
    assert payload[0]["target"] == "read-discipline" and payload[0]["verdict"] == "met"
    assert payload[0]["threshold"] == 0.5 and payload[0]["runs"] == []
    finish = payload[1]["runs"]
    assert isinstance(finish, list) and finish[0]["verdict"] == "cut, finish not sent"
    assert finish[0]["missed"] is True and finish[0]["rule_fired_at"] == 3
    plain = replace(discipline("discipline-on", 1), proof=None)
    assert proof_markdown([plain]) == "" and targets([plain]) == ()
