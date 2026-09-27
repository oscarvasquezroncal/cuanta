from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cuanta.domain.anatomy import AnatomyReport, Phase, PhaseTotals, analyze_anatomy
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.spectrum import resolve_agents


def api(
    second: int,
    agent: str = "main",
    fresh: int = 10,
    output: int = 5,
    cache_read: int = 0,
    cache_write: int = 0,
    cost: float | None = 0.01,
    session: str = "session",
    run: str = "run",
) -> LedgerEvent:
    return LedgerEvent(
        id=second,
        kind="api_request",
        run_id=run,
        session_id=session,
        agent=agent,
        input_tokens=fresh,
        output_tokens=output,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cost_usd=cost,
        ts=(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=second)).isoformat(),
    )


def tool(second: int, agent: str = "main", name: str = "Read") -> LedgerEvent:
    return replace(
        api(second, agent),
        kind="tool_result",
        tool_name=name,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
    )


def start(second: int, child: str = "analyst", parent: str = "main") -> LedgerEvent:
    return replace(
        tool(second, parent, "Agent"),
        kind="tool_decision",
        raw=json.dumps({"cuanta.parameters": {"subagent_type": child}}),
    )


def end(second: int, child: str = "analyst") -> LedgerEvent:
    return replace(
        tool(second, name=""),
        kind="subagent_completed",
        raw=json.dumps({"attributes": {"agent_type": child}}),
    )


def pipeline() -> list[LedgerEvent]:
    return [
        api(0),
        start(1),
        api(2, "analyst"),
        tool(3, "analyst"),
        api(4, "analyst", output=1000, cache_write=100, cache_read=5000),
        end(5),
        tool(6, name="Agent"),
        api(40, cache_write=1250),
        api(50, cache_write=1250),
    ]


def phase_for(report: AnatomyReport, second: int) -> Phase:
    return next(event.phase for event in report.events if event.event_id == second)


def test_empty_report_has_no_usage_coverage() -> None:
    assert analyze_anatomy([]) == AnatomyReport()
    assert AnatomyReport().totals.requests == 0
    assert AnatomyReport().phases == ()


def test_unmatched_same_type_start_blocks_a_later_handoff() -> None:
    events = [
        api(0),
        start(1),
        api(2, "analyst"),
        start(3),
        api(4, "analyst"),
        end(5),
        start(6),
        api(7, "analyst", output=1000),
        end(8),
        api(9, cache_write=1000),
    ]
    assert phase_for(analyze_anatomy(events), 9) is Phase.EXPLORATION
    assert AnatomyReport().events == ()
    assert PhaseTotals().total == 0
    assert PhaseTotals().cache_share == 0


def test_phases_partition_usage_and_handoff_is_consumed_once() -> None:
    events = pipeline()
    report = analyze_anatomy(events)
    assert [event.phase for event in report.events] == [
        Phase.START,
        Phase.START,
        Phase.WRITING,
        Phase.HANDOFF,
        Phase.EXPLORATION,
    ]
    requests = [event for event in events if event.kind == "api_request"]
    assert report.totals.requests == len(requests) == len(report.events)
    assert report.totals.total == sum(event.total_tokens for event in requests)
    assert report.totals.cost_usd == pytest.approx(0.05)
    assert sum(phase.totals.total for phase in report.phases) == report.totals.total
    assert sum(agent.totals.total for agent in report.agents) == report.totals.total
    assert sum(phase.totals.requests for phase in report.phases) == report.totals.requests
    assert report == analyze_anatomy(list(reversed(events)))


def test_start_is_first_per_run_session_and_agent_and_wins_over_writing() -> None:
    events = [
        tool(0),
        api(1, output=1000),
        api(2, "analyst", output=1000),
        api(3, session="other", output=1000),
        api(4, run="other", output=1000),
        api(5, output=1000),
    ]
    report = analyze_anatomy(events)
    assert [event.phase for event in report.events] == [Phase.START] * 4 + [Phase.WRITING]


def test_writing_excludes_cache_reads_from_output_dominance() -> None:
    report = analyze_anatomy(
        [api(0), tool(1), api(2, output=1000, cache_write=100, cache_read=99999)]
    )
    assert phase_for(report, 2) is Phase.WRITING
    assert report.totals.cache_share > 0.99


def test_writing_requires_output_dominance_and_a_final_same_agent_tool() -> None:
    events = [
        api(0),
        tool(1),
        api(2, fresh=10, output=110, cache_write=100),
        api(3, output=1000),
        tool(4),
        api(5, output=1000),
    ]
    report = analyze_anatomy(events)
    assert phase_for(report, 2) is Phase.EXPLORATION
    assert phase_for(report, 3) is Phase.EXPLORATION
    assert phase_for(report, 5) is Phase.WRITING
    no_tools = analyze_anatomy([api(0), api(1, output=1000)])
    assert phase_for(no_tools, 1) is Phase.EXPLORATION


@pytest.mark.parametrize(
    ("cache_write", "expected"),
    [(1756, Phase.HANDOFF), (1757, Phase.EXPLORATION), (0, Phase.EXPLORATION)],
)
def test_handoff_approximation_has_a_bounded_wrapper_allowance(
    cache_write: int, expected: Phase
) -> None:
    events = [
        replace(event, cache_write_tokens=cache_write) if event.id == 40 else event
        for event in pipeline()
    ]
    assert phase_for(analyze_anatomy(events), 40) is expected


def test_handoff_uses_the_next_parent_request_and_has_no_time_cutoff() -> None:
    events = [
        replace(event, ts=api(7200).ts) if event.id == 40 else event
        for event in pipeline()
        if event.id != 50
    ]
    assert phase_for(analyze_anatomy(events), 40) is Phase.HANDOFF


def test_a_mismatching_next_parent_request_does_not_match_a_later_request() -> None:
    events = [
        replace(event, cache_write_tokens=9000) if event.id == 40 else event for event in pipeline()
    ]
    report = analyze_anatomy(events)
    assert phase_for(report, 40) is Phase.EXPLORATION
    assert phase_for(report, 50) is Phase.EXPLORATION


def test_parent_exploration_between_completion_and_usage_blocks_handoff() -> None:
    events = [*pipeline(), tool(7)]
    assert phase_for(analyze_anatomy(events), 40) is Phase.EXPLORATION


def test_first_parent_request_is_start_even_when_it_matches_a_handoff() -> None:
    events = [event for event in pipeline() if event.id != 0]
    assert phase_for(analyze_anatomy(events), 40) is Phase.START


def test_incomplete_or_mismatched_child_windows_do_not_match_handoff() -> None:
    incomplete = [event for event in pipeline() if event.kind != "subagent_completed"]
    mismatched = [
        replace(event, raw=json.dumps({"agent_type": "tester"}))
        if event.kind == "subagent_completed"
        else event
        for event in pipeline()
    ]
    assert phase_for(analyze_anatomy(incomplete), 40) is Phase.EXPLORATION
    assert phase_for(analyze_anatomy(mismatched), 40) is Phase.EXPLORATION


def test_two_child_completions_for_one_parent_request_are_ambiguous() -> None:
    events = [
        api(0),
        start(1),
        api(2, "analyst"),
        start(3, "tester"),
        api(4, "tester"),
        tool(5, "analyst"),
        api(6, "analyst", output=1000),
        tool(7, "tester"),
        api(8, "tester", output=900),
        end(9),
        end(10, "tester"),
        api(11, cache_write=1000),
    ]
    assert phase_for(analyze_anatomy(events), 11) is Phase.EXPLORATION


def test_handoff_never_crosses_a_session_or_run() -> None:
    for session in (True, False):
        events = [
            (replace(event, session_id="other") if session else replace(event, run_id="other"))
            if event.id in {40, 50}
            else event
            for event in pipeline()
        ]
        report = analyze_anatomy(events)
        assert phase_for(report, 40) is Phase.START
        assert phase_for(report, 50) is Phase.EXPLORATION


def test_primary_usage_excludes_result_usage_mirrors() -> None:
    request = api(1)
    mirror = replace(request, kind="result_usage", id=2)
    report = analyze_anatomy([request, mirror])
    assert report.totals.requests == 1
    assert report.totals.total == request.total_tokens
    fallback = analyze_anatomy([mirror])
    assert fallback.totals.total == mirror.total_tokens
    assert fallback.events[0].phase is Phase.START
    selected = analyze_anatomy([request, mirror], selected_usage=[mirror])
    assert selected.events[0].event_id == 2


def test_sse_usage_and_reasoning_tokens_are_preserved() -> None:
    event = replace(api(1), kind="sse_event:response.completed", reasoning_tokens=200)
    report = analyze_anatomy([event])
    assert report.totals.reasoning == 200
    assert report.totals.total == 215


def test_default_usage_keeps_child_fallback_without_a_primary_mirror() -> None:
    root = api(1)
    root_mirror = replace(root, kind="result_usage", id=2)
    child = replace(api(3, "analyst", run="child"), kind="result_usage")
    report = analyze_anatomy([root, root_mirror, child])
    assert report.totals.requests == 2
    assert [event.event_id for event in report.events] == [1, 3]
    assert report.events[1].totals.fresh_input == child.input_tokens
    assert report.events[1].totals.cache_write == child.cache_write_tokens
    assert report.events[1].totals.output == child.output_tokens
    selected = analyze_anatomy([root, root_mirror, child], selected_usage=[root])
    assert selected.totals.requests == 1


def test_missing_cost_remains_unknown_and_covered_absent_phases_are_zero() -> None:
    report = analyze_anatomy([api(1, cost=None), api(2)])
    assert report.totals.cost_usd is None
    assert (
        next(phase.totals.cost_usd for phase in report.phases if phase.phase is Phase.START) is None
    )
    assert (
        next(phase.totals.cost_usd for phase in report.phases if phase.phase is Phase.EXPLORATION)
        == 0.01
    )
    assert (
        next(phase.totals.cost_usd for phase in report.phases if phase.phase is Phase.HANDOFF)
        == 0.0
    )
    assert report.agents[0].totals.cost_usd is None


@pytest.mark.parametrize("raw", ["", "{", "[]", "null", '{"attributes":[null,1]}'])
def test_malformed_completion_metadata_is_safe(raw: str) -> None:
    events = [
        replace(event, raw=raw) if event.kind == "subagent_completed" else event
        for event in pipeline()
    ]
    assert phase_for(analyze_anatomy(events), 40) is Phase.EXPLORATION


def test_anatomy_json_contains_no_raw_prompt_or_source() -> None:
    event = replace(
        api(1), raw='{"prompt":"private source"}', file_path="private.py", command="private command"
    )
    encoded = json.dumps(asdict(analyze_anatomy([event])))
    assert "private" not in encoded
    assert "raw" not in encoded
    assert "file_path" not in encoded


def test_real_investigation_cost_and_token_anatomy() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "telemetry" / "real_investigation.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    resolved = resolve_agents([LedgerEvent(**value) for value in payload["events"]])
    report = analyze_anatomy(resolved)
    assert {phase.phase: phase.totals.requests for phase in report.phases} == {
        Phase.START: 2,
        Phase.EXPLORATION: 8,
        Phase.WRITING: 1,
        Phase.HANDOFF: 1,
    }
    assert {phase.phase: phase.totals.total for phase in report.phases} == {
        Phase.START: 65_648,
        Phase.EXPLORATION: 367_203,
        Phase.WRITING: 59_855,
        Phase.HANDOFF: 49_255,
    }
    expected_cost = {
        Phase.START: 0.2483865,
        Phase.EXPLORATION: 0.1839955,
        Phase.WRITING: 0.0615582,
        Phase.HANDOFF: 0.0515198,
    }
    for phase in report.phases:
        assert phase.totals.cost_usd == pytest.approx(expected_cost[phase.phase])
    assert report.totals.total == 541_961
    assert report.totals.cost_usd == pytest.approx(0.54546)
    assert report.totals.requests == len(report.events) == 12
    assert phase_for(report, 110) is Phase.WRITING
    assert phase_for(report, 119) is Phase.HANDOFF
    assert "not causal" in report.heuristic
