from __future__ import annotations

import json

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.spectrum import (
    LeakKind,
    View,
    amplification,
    analyze,
    calibrate,
    changed_paths,
    estimated_tokens,
    find_leaks,
    grouped,
    plan_weeks,
    plan_windows,
    resolve_agents,
    suggest,
    totals_of,
    usage_events,
    utilization,
)


def api(
    ts: str,
    fresh: int = 100,
    output: int = 10,
    cache_read: int = 0,
    cache_write: int = 0,
    model: str = "m1",
    agent: str = "main",
    session: str = "s",
    cost: float = 0.01,
) -> LedgerEvent:
    return LedgerEvent(
        kind="api_request",
        session_id=session,
        ts=ts,
        agent=agent,
        model=model,
        input_tokens=fresh,
        output_tokens=output,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cost_usd=cost,
    )


def tool(
    ts: str,
    name: str = "Read",
    result: int = 400,
    path: str = "",
    command: str = "",
    agent: str = "main",
    session: str = "s",
    inputs: int = 0,
    raw: str = "",
) -> LedgerEvent:
    return LedgerEvent(
        kind="tool_result",
        session_id=session,
        ts=ts,
        agent=agent,
        tool_name=name,
        tool_result_bytes=result,
        tool_input_bytes=inputs,
        file_path=path,
        command=command,
        raw=raw,
    )


def test_estimated_tokens() -> None:
    assert estimated_tokens(400) == 100
    assert estimated_tokens(0) == 0
    assert estimated_tokens(400, 2.0) == 200


def test_totals_and_cache_share() -> None:
    totals = totals_of(
        [api("1", fresh=100, cache_read=300, output=50), api("2", fresh=100, cache_write=100)]
    )
    assert totals.total == 660
    assert totals.cache_share == 300 / 600
    assert totals.requests == 2


def test_usage_prefers_telemetry_over_stream_result() -> None:
    stream = LedgerEvent(kind="result_usage", input_tokens=5)
    assert usage_events([stream, api("1")])[0].kind == "api_request"
    assert usage_events([stream])[0].kind == "result_usage"


def test_amplification_counts_later_requests_until_compaction() -> None:
    result = tool("1", result=4000)
    later = [api("2"), api("3"), LedgerEvent(kind="compaction", session_id="s", ts="4"), api("5")]
    assert amplification(result, later) == 1000 * 2
    other_agent = [api("2", agent="tester")]
    assert amplification(result, other_agent) == 0


def test_leaks_repeated_reads_test_output_switch_and_compaction() -> None:
    events = [
        api("01"),
        tool("02", path="src/app.py", result=800),
        api("03"),
        tool("04", path="src/app.py", result=800),
        tool("05", name="Bash", command="pytest -vv", result=40_000),
        api("06", model="m2", cache_write=900),
        LedgerEvent(kind="compaction", session_id="s", ts="07"),
        api("08", model="m2", cache_write=1200),
        tool("09", name="Bash", command="cuanta test --json", result=40_000),
    ]
    leaks = find_leaks(events)
    kinds = {leak.kind for leak in leaks}
    assert kinds == {
        LeakKind.AMPLIFICATION,
        LeakKind.REPEATED_READ,
        LeakKind.TEST_OUTPUT,
        LeakKind.COMPACTION,
        LeakKind.MODEL_SWITCH,
    }
    assert [leak.tokens for leak in leaks] == sorted((leak.tokens for leak in leaks), reverse=True)
    repeated = next(leak for leak in leaks if leak.kind is LeakKind.REPEATED_READ)
    assert repeated.tokens == 200
    test_leaks = [leak for leak in leaks if leak.kind is LeakKind.TEST_OUTPUT]
    assert len(test_leaks) == 1
    switch = next(leak for leak in leaks if leak.kind is LeakKind.MODEL_SWITCH)
    assert switch.subject == "m1 → m2"
    compaction = next(leak for leak in leaks if leak.kind is LeakKind.COMPACTION)
    assert compaction.tokens == 1200
    actions = [item.action for item in suggest(leaks)]
    assert any("cuanta test" in action for action in actions)
    assert any("graphify update" in action for action in actions)
    assert any("split the mandate" in action for action in actions)


def test_resolve_agents_uses_next_spawn() -> None:
    spawn = tool(
        "05",
        name="Agent",
        raw=json.dumps({"cuanta.parameters": {"spawned_agent": "tester"}}),
    )
    events = [api("01", agent="custom"), api("02", agent=""), spawn, api("09", agent="custom")]
    resolved = resolve_agents(events)
    agents = [event.agent for event in resolved if event.kind == "api_request"]
    assert agents == ["tester", "tester", "custom"]


def test_utilization_counts_changed_reads_edits_and_resolved_tests() -> None:
    events = [
        tool("1", path="/repo/src/app.py", result=4000),
        tool("2", path="/repo/src/other.py", result=4000),
        tool("3", name="Edit", path="/repo/src/app.py", inputs=800),
        tool("4", name="Bash", command="cuanta test --json", result=1200),
    ]
    value = utilization(events, frozenset({"src/app.py"}), True, 10_000)
    assert value.useful_tokens == 1000 + 200 + 300
    assert value.value == 0.15
    assert utilization(events, frozenset(), False, 0).value is None
    assert changed_paths({"a": "1", "b": "2"}, {"a": "1", "b": "3", "c": "4"}) == frozenset(
        {"b", "c"}
    )


def test_plan_windows_and_weeks() -> None:
    usage = [
        api("2026-01-05T00:00:00Z"),
        api("2026-01-05T04:59:00Z"),
        api("2026-01-05T05:00:00Z", cache_read=500),
        api("2026-01-13T00:00:00Z"),
    ]
    windows = plan_windows(usage)
    assert [window.totals.requests for window in windows] == [2, 1, 1]
    assert windows[1].totals.cache_read == 500
    weeks = plan_weeks(usage)
    assert [week.label for week in weeks] == ["2026-W02", "2026-W03"]


def test_grouped_views() -> None:
    usage = [api("1", model="a", agent="main"), api("2", model="b", agent="tester")]
    tools = [tool("3", path="x.py", result=4000), tool("4", name="Grep", result=400)]
    assert [row.key for row in grouped(View.MODEL, usage, tools)] == ["a", "b"]
    assert {row.key for row in grouped(View.AGENT, usage, tools)} == {"main", "tester"}
    assert grouped(View.TOOL, usage, tools)[0].key == "Read"
    assert [row.key for row in grouped(View.FILE, usage, tools)] == ["x.py"]


def test_calibration_needs_three_samples() -> None:
    events = []
    context = 1000
    for index in range(5):
        events.append(api(f"{index}a", fresh=context, output=0))
        events.append(tool(f"{index}b", result=800))
        context += 200
    events.append(api("9", fresh=context, output=0))
    assert calibrate(events) == {"m1": 4.0}
    assert calibrate(events[:3]) == {}


def test_analyze_report() -> None:
    report = analyze("run X", [api("1", cost=0.5), tool("2", path="a.py")])
    assert report.totals.cost_usd == 0.5
    assert report.source == "telemetry"
    assert report.tree.children[0].label == "main"
    assert report.utilization.label == "heuristic v1"
    empty = analyze("nothing", [])
    assert empty.source == "none"
    assert empty.utilization.value is None
