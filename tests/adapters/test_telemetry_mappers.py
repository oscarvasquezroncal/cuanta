from __future__ import annotations

import json
from pathlib import Path

from cuanta.adapters.telemetry.claude_code_mapper import map_log
from cuanta.adapters.telemetry.otlp_json import LogRecord
from cuanta.adapters.telemetry.otlp_receiver import map_payload
from cuanta.domain.redaction import redact_for_remote, redact_for_storage
from cuanta.domain.shells import Shell, env_hint, shell_from_name, snippet
from cuanta.domain.telemetry import (
    agent_from_signals,
    claude_env,
    codex_otel_table,
    project_slug,
)

OTLP = Path(__file__).parents[1] / "fixtures" / "otlp"


def _load(name: str) -> object:
    return json.loads((OTLP / name).read_text(encoding="utf-8"))


def test_claude_live_capture_maps_api_request_and_tools() -> None:
    events = map_payload("/v1/logs", _load("claude_logs.json"))
    kinds = [event.kind for event in events]
    assert {
        "user_prompt",
        "api_request",
        "tool_decision",
        "tool_result",
        "assistant_response",
    } <= set(kinds)
    api = next(event for event in events if event.kind == "api_request")
    assert api.model == "claude-haiku-4-5-20251001"
    assert api.output_tokens > 0
    assert any(
        event.kind == "api_request"
        and event.cache_read_tokens == 25559
        and event.cache_write_tokens == 1155
        and event.input_tokens == 8
        for event in events
    )
    assert api.cost_usd > 0
    assert api.run_id == "01TESTRUN"
    assert api.trace_id == "0af7651916cd43dd8448eb211c80319c"
    assert api.agent == "main"
    tool = next(event for event in events if event.kind == "tool_result")
    assert tool.tool_name == "Read"
    assert tool.success is True
    assert tool.tool_result_bytes == 22
    assert tool.file_path == "/work/live/hello.txt"
    assert tool.duration_ms == 4


def test_raw_events_drop_identity_and_prompt_text() -> None:
    events = map_payload("/v1/logs", _load("claude_logs.json"))
    for event in events:
        assert "user.email" not in event.raw
        assert "account_uuid" not in event.raw
        assert '"prompt"' not in event.raw
    kept = map_payload("/v1/logs", _load("claude_logs.json"), keep_prompts=True)
    prompt = next(event for event in kept if event.kind == "user_prompt")
    assert '"prompt"' in prompt.raw


def test_claude_metrics_are_stored_without_tokens() -> None:
    events = map_payload("/v1/metrics", _load("claude_metrics.json"))
    assert events
    assert all(event.kind.startswith("metric:") for event in events)
    assert all(event.total_tokens == 0 for event in events)


def test_codex_mapper() -> None:
    events = map_payload("/v1/logs", _load("codex_logs.json"))
    sse = next(event for event in events if event.kind.startswith("sse_event"))
    assert sse.kind == "sse_event:response.completed"
    assert (sse.input_tokens, sse.output_tokens, sse.cache_read_tokens, sse.reasoning_tokens) == (
        1200,
        300,
        800,
        64,
    )
    assert sse.session_id == "conv-1"
    assert sse.run_id == "01CODEXRUN"
    tool = next(event for event in events if event.kind == "tool_result")
    assert tool.command == "bash -lc pytest -q"
    assert tool.success is False
    assert tool.tool_result_bytes == len("1 failed, 3 passed")


def test_unknown_payloads_do_not_crash() -> None:
    assert (
        map_payload("/v1/logs", {"resourceLogs": [None, {"scopeLogs": [{"logRecords": [5]}]}]})
        == []
    )
    assert map_payload("/v1/logs", []) == []
    spans = map_payload(
        "/v1/traces",
        {
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "name": "s",
                                    "traceId": "t",
                                    "startTimeUnixNano": "1000000000",
                                    "endTimeUnixNano": "3000000000",
                                }
                            ]
                        }
                    ]
                }
            ]
        },
    )
    assert spans[0].kind == "span:s"
    assert spans[0].duration_ms == 2000


def test_agent_attribution_rules() -> None:
    assert agent_from_signals("tester", "subagent").name == "tester"
    assert agent_from_signals("", "sdk").name == "main"
    assert agent_from_signals("", "agent:docs-updater").name == "docs-updater"
    custom = agent_from_signals("custom", "subagent")
    assert custom.name == "custom"
    assert not custom.certain


def test_env_builders() -> None:
    env = claude_env(4318, "My Shop", run_id="01R", traceparent="00-a-b-01")
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json"
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "cuanta.run_id=01R,cuanta.project=my-shop"
    assert env["TRACEPARENT"] == "00-a-b-01"
    interactive = claude_env(4318, "x")
    assert "TRACEPARENT" not in interactive
    assert "cuanta.run_id" not in interactive["OTEL_RESOURCE_ATTRIBUTES"]
    assert project_slug("  ") == "project"
    assert snippet({"A": "1"}, Shell.POWERSHELL) == "$env:A = '1'"
    assert snippet({"A": "1"}, Shell.FISH) == "set -gx A '1'"
    assert snippet({"A": "1", "B": "x y"}, Shell.CMD) == "set A=1\nset B=x y"
    assert env_hint("K", "v", Shell.UNKNOWN).startswith("cmd: set K=v")
    assert shell_from_name("CMD.EXE") is Shell.CMD
    assert shell_from_name("pwsh.exe") is Shell.POWERSHELL
    assert shell_from_name("tcsh") is Shell.UNKNOWN
    table = codex_otel_table(5000)
    assert table["log_user_prompt"] is False


def test_redaction() -> None:
    text = "key sk-ant-abcdefghijklmnopqrstuv mail me@example.invalid token=supersecret123 at C:\\Users\\me\\src\\app.py"
    stored = redact_for_storage(text)
    assert "sk-ant" not in stored
    assert "me@example.invalid" not in stored
    assert "supersecret123" not in stored
    remote = redact_for_remote(text)
    assert "Users" not in remote
    assert ".../app.py" in remote
    assert redact_for_remote("see /home/dev/repo/src/main.go") == "see .../main.go"


def test_claude_requests_keep_effort_and_time_to_first_token() -> None:
    record = LogRecord(
        name="claude_code.api_request",
        attributes={"event.name": "api_request", "effort": "low", "ttft_ms": 812, "model": "m"},
        resource={},
        ts="2026-09-25T03:14:47.109Z",
        trace_id="",
        body=None,
    )
    event = map_log(record)
    assert (event.kind, event.effort, event.ttft_ms) == ("api_request", "low", 812)
