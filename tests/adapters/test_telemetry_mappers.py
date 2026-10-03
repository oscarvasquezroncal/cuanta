from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cuanta.adapters.telemetry.claude_code_mapper import map_log
from cuanta.adapters.telemetry.mapping import as_int, raw_json
from cuanta.adapters.telemetry.otlp_json import LogRecord, nano_to_iso
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
    assert api.cost_usd is not None and api.cost_usd > 0
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
    assert not agent_from_signals("", "").certain
    assert agent_from_signals("", "main").certain


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


def test_claude_empty_source_agent_is_marked_as_a_default() -> None:
    event = map_log(LogRecord("claude_code.tool_result", {"tool_name": "Read"}, {}, "", "", None))
    assert event.agent == "main"
    assert json.loads(event.raw)["attributed"] == "default"


def test_claude_explicit_agent_signals_are_not_marked_as_defaults() -> None:
    for attrs in [
        {"agent.name": "tester"},
        {"query_source": "sdk"},
        {"query_source": "agent:docs"},
    ]:
        event = map_log(LogRecord("claude_code.api_request", attrs, {}, "", "", None))
        assert "attributed" not in json.loads(event.raw)


FRAGMENTS = (
    "token=",
    "api_key=",
    "secret: ",
    '"',
    "\\",
    "\n",
    "é",
    "@",
    "ops@example.invalid",
    "abcdef123456",
    " ",
    ",",
    "{",
    "}",
)


def _attributes(raw: str) -> dict[str, Any]:
    stored = json.loads(raw)
    return {item["key"]: item["value"] for item in stored["attributes"]}


def test_values_redaction_touches_keep_raw_json_valid() -> None:
    events = map_payload("/v1/logs", _load("claude_redaction.json"))
    assert [event.kind for event in events] == ["api_request", "tool_result"]
    api, tool = events
    assert api.input_tokens == 8
    stored = json.loads(tool.raw)
    values = _attributes(tool.raw)
    command = "export ANTHROPIC_API_KEY=[redacted]"
    assert json.loads(values["tool_input"]["stringValue"])["command"] == command
    assert stored["cuanta.parameters"]["command"] == command
    assert values["error"]["stringValue"] == "exit 1\n[email]"
    assert "abc123def456" not in tool.raw
    assert "ops@example.invalid" not in tool.raw


def _strict(text: str) -> Any:
    def reject(token: str) -> Any:
        raise ValueError(f"not strict JSON: {token}")

    return json.loads(text, parse_constant=reject)


def _any_value(value: str | float) -> dict[str, Any]:
    return {"stringValue": value} if isinstance(value, str) else {"doubleValue": value}


@given(
    st.dictionaries(
        st.sampled_from(["tool_input", "error", "token", "note", "ratio"]),
        st.one_of(st.lists(st.sampled_from(FRAGMENTS), max_size=12).map("".join), st.floats()),
        max_size=4,
    )
)
def test_raw_json_is_valid_json_for_any_value_redaction_touches(
    values: dict[str, str | float],
) -> None:
    record = {
        "attributes": [{"key": key, "value": _any_value(value)} for key, value in values.items()]
    }
    stored = raw_json(record, False)
    assert isinstance(_strict(stored), dict)
    assert "ops@example.invalid" not in stored


def _metric(value: object) -> dict[str, Any]:
    point = {"asDouble": value, "attributes": []}
    metric = {"name": "claude_code.cost.usage", "sum": {"dataPoints": [point]}}
    return {"resourceMetrics": [{"resource": {}, "scopeMetrics": [{"metrics": [metric]}]}]}


def test_non_finite_numbers_are_stored_as_strict_json_strings() -> None:
    for sent, kept in (("NaN", "NaN"), ("Infinity", "Infinity"), ("-Infinity", "-Infinity")):
        (event,) = map_payload("/v1/metrics", _metric(sent))
        assert _strict(event.raw)["value"] == kept
    body = (
        '{"resourceLogs":[{"scopeLogs":[{"logRecords":[{"body":{"stringValue":'
        '"claude_code.api_request"},"attributes":[{"key":"ratio","value":'
        '{"doubleValue":NaN}},{"key":"cuanta.run_id","value":{"stringValue":"R"}}]}]}]}]}'
    )
    (event,) = map_payload("/v1/logs", json.loads(body))
    assert _attributes(event.raw)["ratio"] == {"doubleValue": "NaN"}
    _strict(event.raw)


def test_numbers_outside_the_ledger_range_read_as_zero() -> None:
    assert as_int(2**63 - 1) == 2**63 - 1
    assert as_int(-(2**63)) == -(2**63)
    assert as_int("12") == 12
    assert as_int(7.9) == 7
    for absurd in (
        2**63,
        -(2**63) - 1,
        "99999999999999999999",
        1e30,
        "1e30",
        float("inf"),
        float("-inf"),
        float("nan"),
        "inf",
        "nan",
    ):
        assert as_int(absurd) == 0


def test_an_absurd_count_keeps_the_record_and_its_raw_value() -> None:
    attrs = [
        {"key": "event.name", "value": {"stringValue": "api_request"}},
        {"key": "input_tokens", "value": {"intValue": "99999999999999999999"}},
        {"key": "duration_ms", "value": {"doubleValue": 1e30}},
    ]
    record = {"body": {"stringValue": "claude_code.api_request"}, "attributes": attrs}
    payload = {"resourceLogs": [{"scopeLogs": [{"logRecords": [record]}]}]}
    (event,) = map_payload("/v1/logs", payload)
    assert (event.kind, event.input_tokens, event.duration_ms) == ("api_request", 0, 0)
    assert _attributes(event.raw)["input_tokens"] == {"intValue": "99999999999999999999"}
    span = {
        "name": "turn",
        "traceId": "t",
        "startTimeUnixNano": "1",
        "endTimeUnixNano": str(10**40),
    }
    traces = {"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]}
    (spanned,) = map_payload("/v1/traces", traces)
    assert spanned.duration_ms == 0


RUN_ATTRIBUTE = '{"key":"cuanta.run_id","value":{"stringValue":"R"}}'
OVERFLOWING_LOGS = (
    '{"resourceLogs":[{"resource":{"attributes":[]},"scopeLogs":[{"logRecords":['
    '{"body":{"stringValue":"claude_code.api_request"},"attributes":['
    '{"key":"event.name","value":{"stringValue":"api_request"}},' + RUN_ATTRIBUTE + ","
    '{"key":"input_tokens","value":{"intValue":"8"}}]},'
    '{"body":{"stringValue":"claude_code.api_request"},"traceId":"T2","attributes":['
    '{"key":"event.name","value":{"stringValue":"api_request"}},' + RUN_ATTRIBUTE + ","
    '{"key":"input_tokens","value":{"intValue":1e999}}]}]}]}]}'
)
OVERFLOWING_SPANS = (
    '{"resourceSpans":[{"resource":{"attributes":[]},"scopeSpans":[{"spans":['
    '{"name":"ok","traceId":"T1","startTimeUnixNano":"1","endTimeUnixNano":"2000001"},'
    '{"name":"bad","traceId":"T2","startTimeUnixNano":"1","endTimeUnixNano":1e999,'
    '"attributes":[' + RUN_ATTRIBUTE + "]}]}]}]}"
)
OVERFLOWING_METRICS = (
    '{"resourceMetrics":[{"resource":{"attributes":[' + RUN_ATTRIBUTE + ']},"scopeMetrics":'
    '[{"metrics":[{"name":"claude_code.cost.usage","sum":{"dataPoints":['
    '{"asDouble":0.5,"attributes":[]},{"asInt":1' + "0" * 400 + ',"attributes":[]}]}}]}]}]}'
)


@pytest.mark.parametrize(
    ("path", "body", "good", "name", "trace"),
    [
        ("/v1/logs", OVERFLOWING_LOGS, "api_request", "claude_code.api_request", "T2"),
        ("/v1/traces", OVERFLOWING_SPANS, "span:ok", "bad", "T2"),
        (
            "/v1/metrics",
            OVERFLOWING_METRICS,
            "metric:claude_code.cost.usage",
            "claude_code.cost.usage",
            "",
        ),
    ],
    ids=["log_int", "span_end", "metric_int"],
)
def test_a_record_whose_numbers_overflow_becomes_a_counted_marker_beside_the_good_ones(
    path: str, body: str, good: str, name: str, trace: str
) -> None:
    failures: list[tuple[str, str]] = []
    events = map_payload(
        path, json.loads(body), failed=lambda item, error: failures.append((item, repr(error)))
    )
    assert [event.kind for event in events] == [good, "telemetry_unreadable"]
    marker = events[1]
    assert (marker.run_id, marker.trace_id) == ("R", trace)
    assert json.loads(marker.raw) == {"event": name, "error": "OverflowError"}
    assert [item for item, _ in failures] == [name]
    assert "OverflowError" in failures[0][1]


def test_absurd_timestamps_do_not_break_the_batch() -> None:
    payload: Any = _load("claude_redaction.json")
    payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][1]["timeUnixNano"] = str(10**40)
    events = map_payload("/v1/logs", payload)
    assert [event.kind for event in events] == ["api_request", "tool_result"]
    assert events[0].ts == "2026-09-23T04:15:45.528Z"
    assert events[1].ts == ""
    for absurd in ("99999999999999999999999", 10**40, 10**400, float("inf"), float("nan")):
        assert nano_to_iso(absurd) == ""
