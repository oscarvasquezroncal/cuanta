from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cuanta.adapters.engines.codex import CodexParser
from cuanta.adapters.system.prices import load_prices
from cuanta.domain.engine import EngineEvent, ModelUsage, RunResult, SessionStarted, ToolCall
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.pricing import dollars, estimate_cost

CAPTURES = Path(__file__).parents[1] / "fixtures" / "engines"


def _capture(
    filename: str, model: str = "gpt-6-luna"
) -> tuple[list[dict[str, Any]], list[EngineEvent], RunResult]:
    lines = (CAPTURES / filename).read_text(encoding="utf-8").splitlines()
    parser = CodexParser(model)
    events = [event for line in lines for event in parser.feed(line)]
    result = parser.finish(0)
    assert result is not None
    return [json.loads(line) for line in lines], events, result


def _usage_event(result: RunResult) -> LedgerEvent:
    (usage,) = result.models
    return LedgerEvent(
        kind="api_request",
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cost_usd=usage.cost_usd,
    )


@pytest.mark.parametrize(
    ("filename", "input_tokens", "output_tokens", "exit_code", "status", "expected_cost"),
    [
        ("codex_readonly_0_156_1.jsonl", 12_622, 118, 1, "failed", 0.00159256),
        ("codex_workspace_0_156_1.jsonl", 13_821, 320, 0, "completed", 0.00181346),
    ],
)
def test_codex_live_capture_preserves_usage_and_command_outcome(
    filename: str,
    input_tokens: int,
    output_tokens: int,
    exit_code: int,
    status: str,
    expected_cost: float,
) -> None:
    raw, events, result = _capture(filename)
    assert [event for event in events if isinstance(event, SessionStarted)] == [
        SessionStarted("<session>", "gpt-6-luna")
    ]
    assert result.ok and result.num_turns == 1
    assert result.session_id == "<session>"
    assert result.cost_usd is None
    assert result.models == (
        ModelUsage(
            "gpt-6-luna",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=27_136,
        ),
    )
    (tool,) = [event for event in events if isinstance(event, ToolCall)]
    assert tool.name == "shell"
    assert "Set-Content" in str(tool.inputs["command"])
    assert "<powershell>" in str(tool.inputs["command"])
    assert "<repo>" in str(tool.inputs["command"])
    (command,) = [
        event["item"]
        for event in raw
        if event["type"] == "item.completed" and event["item"]["type"] == "command_execution"
    ]
    assert command["exit_code"] == exit_code
    assert command["status"] == status
    assert "is denied" in command["aggregated_output"]
    if exit_code == 0:
        assert "<sibling>" in command["command"]
        assert "INSIDE_OK" in command["aggregated_output"]
        assert "OUTSIDE_ERROR" in command["aggregated_output"]
        assert "OUTSIDE_OK" not in command["aggregated_output"]
    estimate = estimate_cost([_usage_event(result)], load_prices())
    assert estimate.known and estimate.kind == "estimated"
    assert estimate.value == pytest.approx(expected_cost)


def test_codex_live_capture_combined_estimate_matches_recorded_probe_spend() -> None:
    usage = [
        _usage_event(_capture(filename)[2])
        for filename in ("codex_readonly_0_156_1.jsonl", "codex_workspace_0_156_1.jsonl")
    ]
    assert sum(event.total_tokens for event in usage) == 81_153
    assert all(event.cost_usd is None for event in usage)
    estimate = estimate_cost(usage, load_prices())
    assert estimate.kind == "estimated"
    assert estimate.value == pytest.approx(0.00340602)


def test_codex_live_capture_unknown_requested_model_stays_unpriced() -> None:
    _, _, result = _capture("codex_readonly_0_156_1.jsonl", "gpt-unpriced")
    assert result.models[0].model == "gpt-unpriced"
    assert result.cost_usd is None
    estimate = estimate_cost([_usage_event(result)], load_prices())
    assert estimate.value is None
    assert estimate.unpriced == ("gpt-unpriced",)
    assert dollars(estimate.value) == "cost n/a"
