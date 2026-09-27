from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.spectrum import resolve_agents


def row(
    second: int,
    kind: str = "tool_result",
    agent: str = "main",
    session: str = "session",
    run: str = "run",
    tool: str = "Read",
    source: str = "",
    raw: dict[str, object] | None = None,
) -> LedgerEvent:
    return LedgerEvent(
        run_id=run,
        session_id=session,
        kind=kind,
        agent=agent,
        tool_name=tool,
        ts=f"2026-01-01T00:00:{second:02d}Z",
        id=second,
        query_source=source,
        raw=json.dumps({"attributed": "default", **(raw or {})}),
    )


def start(second: int, name: str = "analyst", session: str = "session") -> LedgerEvent:
    return row(
        second,
        kind="tool_decision",
        tool="Agent",
        session=session,
        raw={"cuanta.parameters": {"subagent_type": name}},
    )


def end(second: int, name: str = "analyst", session: str = "session") -> LedgerEvent:
    return row(
        second,
        kind="subagent_completed",
        tool="",
        session=session,
        raw={"attributes": {"agent_type": name}},
    )


def test_default_agent_rows_use_the_closed_decision_completion_window() -> None:
    events = [
        row(1),
        start(2),
        row(3, kind="tool_decision"),
        row(4, kind="tool_use"),
        row(5),
        row(6, kind="api_request", tool=""),
        row(7, kind="mcp_tool_call", tool="mcp__cuanta__page"),
        row(8, kind="index_call", tool="mcp__cuanta__page"),
        end(9),
        row(10),
    ]
    resolved = resolve_agents(events)
    assert [event.agent for event in resolved] == [
        "main",
        "main",
        "analyst",
        "analyst",
        "analyst",
        "analyst",
        "analyst",
        "analyst",
        "main",
        "main",
    ]
    assert len(resolved) == len(events)
    assert resolve_agents(resolved) == resolved
    assert events[4].agent == "main"


def test_explicit_agent_and_sdk_source_override_a_default_marker() -> None:
    events = [
        start(1),
        row(2, kind="api_request", source="sdk"),
        row(3, raw={"attributes": {"agent.name": "tester"}}),
        row(4, source="agent:docs"),
        row(5, raw={"attributes": {"agent.type": "main"}}),
        end(6),
    ]
    assert [event.agent for event in resolve_agents(events)] == [
        "main",
        "main",
        "tester",
        "docs",
        "main",
        "main",
    ]


def test_open_overlapping_child_blocks_closed_window_and_api_fallback() -> None:
    events = [
        start(1),
        start(2, "tester"),
        row(3),
        end(4),
        row(5, kind="api_request", source="agent:analyst"),
        row(6),
        row(7, kind="api_request", source="agent:analyst"),
    ]
    resolved = {event.id: event for event in resolve_agents(events)}
    assert resolved[3].agent == resolved[6].agent == "main"
    assert resolved[5].agent == resolved[7].agent == "analyst"


def test_ambiguous_same_type_starts_block_an_other_closed_window() -> None:
    events = [start(1), start(2, "tester"), start(3, "tester"), row(4), end(5, "tester"), end(6)]
    resolved = {event.id: event for event in resolve_agents(events)}
    assert resolved[4].agent == "main"


def test_unmatched_same_type_start_blocks_after_the_first_completion() -> None:
    events = [start(1), start(2, "tester"), start(3, "tester"), end(4, "tester"), row(5), end(6)]
    resolved = {event.id: event for event in resolve_agents(events)}
    assert resolved[5].agent == "main"


def test_decision_and_completion_otlp_attributes_match_the_agent_type() -> None:
    decision = row(
        1,
        kind="tool_decision",
        tool="Task",
        raw={
            "attributes": [
                {"key": "tool_parameters", "value": {"stringValue": '{"subagent_type":"tester"}'}}
            ]
        },
    )
    completion = row(
        3,
        kind="subagent_completed",
        raw={"attributes": [{"key": "agent_type", "value": {"stringValue": "tester"}}]},
    )
    assert resolve_agents([decision, row(2), completion])[1].agent == "tester"


def test_open_or_mismatched_windows_do_not_infer_a_child() -> None:
    events = [start(1), row(2), end(3, name="tester"), row(4)]
    assert [event.agent for event in resolve_agents(events)] == ["main"] * 4
    assert resolve_agents([start(1), row(2)])[1].agent == "main"


def test_overlapping_different_agents_are_ambiguous() -> None:
    events = [start(1), start(2, name="tester"), row(3), end(4), end(5, name="tester")]
    assert resolve_agents(events)[2].agent == "main"


def test_same_type_concurrent_decisions_are_ambiguous() -> None:
    events = [start(1), start(2), row(3), end(4), end(5)]
    assert resolve_agents(events)[2].agent == "main"


def test_duplicate_decision_rows_do_not_create_an_extra_window() -> None:
    decision = start(1)
    events = [decision, decision, row(2), end(3)]
    resolved = resolve_agents(events)
    assert len(resolved) == 4
    assert next(event for event in resolved if event.id == 2).agent == "analyst"


def test_windows_never_cross_a_session_or_run() -> None:
    events = [
        start(1),
        row(2, session="other"),
        row(3, run="other"),
        end(4),
        row(5, session="other"),
    ]
    assert [event.agent for event in resolve_agents(events)] == ["main"] * 5


def test_owned_mcp_metadata_without_a_session_remains_uncertain() -> None:
    owned = row(2, kind="index_call", agent="uncertain", session="", raw={"attributed": "owned"})
    assert resolve_agents([start(1), owned, end(3)])[1].agent == "uncertain"


def test_matching_explicit_api_brackets_are_the_fallback() -> None:
    events = [
        row(1, kind="api_request", source="agent:analyst"),
        row(2),
        row(3, kind="api_request", source="agent:analyst"),
        row(4),
    ]
    assert [event.agent for event in resolve_agents(events)] == [
        "analyst",
        "analyst",
        "analyst",
        "main",
    ]


def test_api_brackets_with_different_or_uncertain_agents_do_not_infer() -> None:
    events = [
        row(1, kind="api_request", source="agent:analyst"),
        row(2),
        row(3, kind="api_request"),
        row(4),
        row(5, kind="api_request", source="agent:tester"),
    ]
    assert resolve_agents(events)[1].agent == "main"
    assert resolve_agents(events)[3].agent == "main"


def test_api_brackets_with_same_timestamp_and_different_agents_are_ambiguous() -> None:
    events = [
        row(1, kind="api_request", source="agent:analyst"),
        replace(row(1, kind="api_request", source="agent:tester"), id=10),
        row(2),
        row(3, kind="api_request", source="agent:analyst"),
    ]
    assert next(event for event in resolve_agents(events) if event.id == 2).agent == "main"


@pytest.mark.parametrize("raw", ["", "{", "[]", "null", '{"attributes":[null,1]}'])
def test_malformed_raw_is_safe(raw: str) -> None:
    event = replace(row(2, agent="custom"), raw=raw)
    assert resolve_agents([event])[0].agent == "custom"


def test_invalid_timestamps_cannot_open_a_window() -> None:
    decision = replace(start(1), ts="invalid")
    assert (
        next(event for event in resolve_agents([decision, row(2), end(3)]) if event.id == 2).agent
        == "main"
    )


def test_real_investigation_window_preserves_the_analyst_reads_and_parent_request() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "telemetry" / "real_investigation.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    events = [LedgerEvent(**value) for value in payload["events"]]
    resolved = resolve_agents(events)
    analyst = [
        event
        for event in resolved
        if event.kind == "tool_result" and event.agent == "architecture-analyst"
    ]
    assert len(analyst) == 31
    assert sum(event.tool_result_bytes for event in analyst) == 56_725
    assert sum(event.tool_name == "Read" for event in analyst) == 25
    assert sum(event.tool_name == "Glob" for event in analyst) == 2
    assert sum(event.tool_name == "Grep" for event in analyst) == 3
    assert sum(event.tool_name == "Bash" for event in analyst) == 1
    decision = next(
        event for event in resolved if event.kind == "tool_decision" and event.tool_name == "Agent"
    )
    parent = next(
        event
        for event in resolved
        if event.kind == "api_request" and event.query_source == "sdk" and event.ts > decision.ts
    )
    assert parent.agent == "main"
    assert parent.ts == "2026-01-01T00:00:16.373000Z"
    assert all(
        event.agent == "main"
        for event in resolved
        if event.kind == "tool_result" and event.tool_name == "Agent"
    )
    assert len(events) == len(resolved) == 127
    assert sum(event.total_tokens for event in resolved) == sum(
        event.total_tokens for event in events
    )
    assert resolve_agents(resolved) == resolved
