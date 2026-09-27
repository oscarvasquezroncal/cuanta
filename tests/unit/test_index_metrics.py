from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cuanta.domain.index_metrics import IndexMetrics, index_metrics
from cuanta.domain.ledger import LedgerEvent


def call(
    tool: str,
    *,
    run: str = "run",
    source: str = "claude_code",
    kind: str = "tool_result",
    identity: str = "",
    size: int = 400,
    raw: str = "",
    success: bool | None = True,
) -> LedgerEvent:
    return LedgerEvent(
        run_id=run,
        source=source,
        kind=kind,
        tool_name=tool,
        tool_use_id=identity,
        tool_result_bytes=size,
        raw=raw,
        success=success,
    )


def native(tool: str, *, identity: str = "", kind: str = "tool_result") -> LedgerEvent:
    values = {
        "mcp_server_name": "cuanta",
        "mcp_tool_name": tool,
    }
    raw = json.dumps(
        {"attributes": [{"key": "tool_parameters", "value": {"stringValue": json.dumps(values)}}]}
    )
    return call("mcp_tool", identity=identity, kind=kind, raw=raw)


def test_no_exploration_has_unknown_hit_rate_and_zero_estimate() -> None:
    assert index_metrics([]) == IndexMetrics()
    assert index_metrics([LedgerEvent(kind="api_request", input_tokens=40_000)]) == IndexMetrics()


def test_index_hit_rate_and_tokens_are_exploration_counts_not_api_tokens() -> None:
    events = [
        call("Read", size=801),
        call("Grep", size=400),
        call("Glob", size=80),
        call("mcp__cuanta__find", size=40),
        call("mcp__cuanta__page", size=204),
        LedgerEvent(kind="api_request", input_tokens=99_999, cost_usd=4.0),
    ]
    metrics = index_metrics(events)
    assert metrics.index_calls == 2
    assert metrics.raw_reads == 3
    assert metrics.exploration_calls == 5
    assert metrics.index_hit_rate == 2 / 5
    assert metrics.exploration_tokens_estimate == 381


@pytest.mark.parametrize("tool", ["find", "card", "impact", "facts", "page", "tests_for"])
def test_all_index_exploration_tools_count(tool: str) -> None:
    assert index_metrics([call(f"mcp__cuanta__{tool}")]).index_calls == 1
    assert index_metrics([native(tool)]).index_calls == 1


@pytest.mark.parametrize("tool", ["note", "initialize", "ping", "unknown"])
def test_mutation_and_protocol_calls_do_not_count_as_exploration(tool: str) -> None:
    assert index_metrics([call(f"mcp__cuanta__{tool}"), native(tool)]) == IndexMetrics()


def test_owned_metadata_replaces_mirrored_native_calls_per_run_and_tool() -> None:
    events = [
        call(
            "mcp__cuanta__find",
            source="cuanta_mcp",
            kind="index_call",
            identity="nonce:1",
            raw='{"returned_tokens_estimate":31}',
        ),
        native("find", identity="native:1"),
        native("find", identity="native:1", kind="tool_decision"),
        call("mcp__cuanta__page", kind="tool_use", identity="page:1"),
        native("page", identity="page:1"),
        native("impact", identity="impact:1"),
        replace(native("find", identity="native:1"), run_id="retry"),
    ]
    metrics = index_metrics(events)
    assert metrics.index_calls == 4
    assert metrics.index_hit_rate == 1.0
    assert metrics.exploration_tokens_estimate == 331


def test_result_preferred_to_tool_use_and_decision_is_not_execution() -> None:
    events = [
        call("Read", kind="tool_use", identity="read:1", size=0),
        call("Read", identity="read:1", size=800),
        call("Grep", kind="tool_decision", identity="grep:1"),
        call("Glob", kind="tool_use", identity="glob:1", size=0),
    ]
    metrics = index_metrics(events)
    assert metrics.raw_reads == 2
    assert metrics.exploration_tokens_estimate == 200


def test_partial_results_do_not_drop_other_native_tool_use_ids() -> None:
    events = [
        call("Read", kind="tool_use", identity="read:1", size=0),
        call("Read", identity="read:1", size=800),
        call("Read", kind="tool_use", identity="read:2", size=0),
    ]
    metrics = index_metrics(events)
    assert metrics.raw_reads == 2
    assert metrics.exploration_calls == 2
    assert metrics.exploration_tokens_estimate == 200
    assert metrics == index_metrics(events * 2)


@pytest.mark.parametrize(("owned_count", "native_count"), [(2, 3), (3, 2), (0, 3)])
def test_partial_owned_logs_keep_unmatched_native_calls_without_double_counting(
    owned_count: int, native_count: int
) -> None:
    events = [
        call(
            "mcp__cuanta__find",
            source="cuanta_mcp",
            kind="index_call",
            identity=f"nonce:{number}",
            raw='{"returned_tokens_estimate":31}',
        )
        for number in range(owned_count)
    ]
    for number in range(native_count):
        events.extend(
            (
                native("find", identity=f"native:{number}", kind="tool_use"),
                native("find", identity=f"native:{number}"),
            )
        )
    metrics = index_metrics(events)
    assert metrics.index_calls == max(owned_count, native_count)
    assert metrics.exploration_tokens_estimate == (
        owned_count * 31 + max(native_count - owned_count, 0) * 100
    )
    assert metrics == index_metrics(events * 2)


def test_native_call_identity_keeps_sessions_sources_and_retry_runs_separate() -> None:
    first = call("Read", identity="read:1", size=400)
    events = [
        first,
        replace(first, session_id="other"),
        replace(first, source="other"),
        replace(first, run_id="retry"),
    ]
    assert index_metrics(events).raw_reads == 4


def test_duplicate_rows_and_tool_ids_are_idempotent_and_retries_are_separate() -> None:
    first = replace(call("Read", identity="read:1"), id=1)
    same_call = replace(first, id=2)
    second = replace(first, tool_use_id="read:2", id=3)
    retry = replace(first, run_id="retry", id=4)
    events = [first, same_call, second, retry]
    metrics = index_metrics(events)
    assert metrics.raw_reads == 3
    assert metrics == index_metrics(events * 2)


def test_failed_executed_calls_count_without_changing_api_costs() -> None:
    events = [call("Read", success=False, size=80), native("find", identity="find:1")]
    metrics = index_metrics(events)
    assert metrics.exploration_calls == 2
    assert metrics.index_hit_rate == 0.5
    assert metrics.exploration_tokens_estimate == 120


@pytest.mark.parametrize(
    "raw",
    [
        "bad json",
        "[]",
        '{"returned_tokens_estimate":-1}',
        '{"returned_tokens_estimate":true}',
        '{"returned_tokens_estimate":2.5}',
    ],
)
def test_missing_or_invalid_owned_token_metadata_falls_back_to_four_bytes(raw: str) -> None:
    event = call("mcp__cuanta__facts", source="cuanta_mcp", kind="index_call", raw=raw, size=805)
    assert index_metrics([event]).exploration_tokens_estimate == 201


def test_valid_zero_token_estimate_is_not_replaced_by_byte_fallback() -> None:
    event = call(
        "mcp__cuanta__facts",
        source="cuanta_mcp",
        kind="index_call",
        raw='{"returned_tokens_estimate":0}',
    )
    assert index_metrics([event]).exploration_tokens_estimate == 0


def test_generic_mcp_execution_fallback_excludes_start_events() -> None:
    before = replace(
        native("page", identity="page:1"),
        kind="mcp_tool",
        raw='{"tool_parameters":{"mcp_server_name":"cuanta","mcp_tool_name":"page"},"status":"started"}',
        success=None,
        tool_result_bytes=0,
    )
    after = replace(
        before, raw=before.raw.replace("started", "completed"), success=True, tool_result_bytes=400
    )
    assert index_metrics([before, after]).index_calls == 1
    assert index_metrics([before]).index_calls == 0


def test_native_result_preferred_to_generic_mcp_completion() -> None:
    completed = replace(native("find", identity="find:1"), kind="mcp_tool", success=True)
    result = native("find", identity="find:1")
    assert index_metrics([completed, result]).index_calls == 1


def test_live_handshake_and_mirrored_tools_shape_counts_only_four_explorations() -> None:
    events = [call("mcp__cuanta__initialize", source="cuanta_mcp", kind="index_handshake")]
    sizes = (
        ("find", 417, 104),
        ("page", 622, 155),
        ("note", 1067, 266),
        ("find", 803, 200),
        ("page", 622, 155),
        ("note", 1065, 266),
    )
    for number, (tool, size, tokens) in enumerate(sizes):
        events.extend(
            (
                call(
                    f"mcp__cuanta__{tool}",
                    source="cuanta_mcp",
                    kind="index_call",
                    identity=f"nonce:{number}",
                    size=size,
                    raw=json.dumps({"returned_tokens_estimate": tokens}),
                ),
                native(tool, identity=f"native:{number}", kind="tool_decision"),
                native(tool, identity=f"native:{number}"),
            )
        )
    metrics = index_metrics(events)
    assert metrics.index_calls == 4
    assert metrics.exploration_calls == 4
    assert metrics.exploration_tokens_estimate == 614


def test_other_mcp_servers_and_unexecuted_notes_do_not_count() -> None:
    other = replace(
        native("find"),
        raw='{"tool_parameters":{"mcp_server_name":"external","mcp_tool_name":"find"}}',
    )
    assert index_metrics([other, call("Bash"), call("Write")]) == IndexMetrics()


def test_saved_findings_stale_facts_and_guard_paths_remain_separate() -> None:
    metrics = index_metrics(
        [],
        stale_facts=2,
        findings_saved=3,
        guard_violations=("protected.py", "protected.py"),
        out_of_plan_edits=("other.py",),
    )
    assert metrics.stale_facts == 2
    assert metrics.findings_saved == 3
    assert metrics.guard_violations == ("protected.py",)
    assert metrics.out_of_plan_edits == ("other.py",)
    assert index_metrics([], stale_facts=-1, findings_saved=-2).stale_facts == 0
