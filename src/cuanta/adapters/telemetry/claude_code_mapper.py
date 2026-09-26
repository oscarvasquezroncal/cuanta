from __future__ import annotations

import json

from cuanta.adapters.telemetry.mapping import (
    as_bool,
    as_cost,
    as_int,
    as_text,
    first,
    parse_json_object,
    raw_json,
)
from cuanta.adapters.telemetry.otlp_json import LogRecord, MetricPoint
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.telemetry import agent_from_signals

SOURCE = "claude_code"
PREFIX = "claude_code."
SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
FILE_KEYS = ("file_path", "path", "notebook_path")


def handles(record: LogRecord) -> bool:
    service = as_text(record.resource.get("service.name"))
    return record.name.startswith(PREFIX) or service == "claude-code"


def _kind(name: str) -> str:
    return name.removeprefix(PREFIX)


def map_log(record: LogRecord, keep_prompts: bool = False) -> LedgerEvent:
    attrs = record.attributes
    resource = record.resource
    kind = _kind(record.name)
    run_id = as_text(first(attrs, "cuanta.run_id") or first(resource, "cuanta.run_id"))
    query_source = as_text(first(attrs, "query_source", "query.source"))
    agent_name = as_text(first(attrs, "agent.name", "agent_name", "agent.type"))
    parameters = parse_json_object(first(attrs, "tool_parameters", "tool_input", "tool.parameters"))
    tool_name = as_text(first(attrs, "tool_name", "tool.name"))
    hint = agent_from_signals(agent_name, query_source)
    agent = hint.name
    if kind == "tool_result" and tool_name in SUBAGENT_TOOLS:
        spawned = as_text(parameters.get("subagent_type"))
        if spawned:
            agent = hint.name if hint.certain else ""
            parameters = {**parameters, "spawned_agent": spawned}
    file_path = next((as_text(parameters[key]) for key in FILE_KEYS if parameters.get(key)), "")
    command = as_text(parameters.get("command") or parameters.get("bash_command"))
    return LedgerEvent(
        run_id=run_id,
        source=SOURCE,
        session_id=as_text(first(attrs, "session.id") or first(resource, "session.id")),
        prompt_id=as_text(first(attrs, "prompt.id")),
        trace_id=record.trace_id or as_text(first(attrs, "trace_id")),
        agent=agent,
        kind=kind,
        model=as_text(first(attrs, "model")),
        input_tokens=as_int(first(attrs, "input_tokens")),
        output_tokens=as_int(first(attrs, "output_tokens")),
        cache_read_tokens=as_int(first(attrs, "cache_read_tokens", "cache_read_input_tokens")),
        cache_write_tokens=as_int(
            first(attrs, "cache_creation_tokens", "cache_creation_input_tokens")
        ),
        reasoning_tokens=as_int(first(attrs, "reasoning_tokens")),
        cost_usd=as_cost(first(attrs, "cost_usd")),
        tool_name=tool_name,
        tool_use_id=as_text(first(attrs, "tool_use_id")),
        tool_result_bytes=as_int(first(attrs, "tool_result_size_bytes")),
        tool_input_bytes=as_int(first(attrs, "tool_input_size_bytes")),
        duration_ms=as_int(first(attrs, "duration_ms")),
        success=as_bool(first(attrs, "success")),
        query_source=query_source,
        file_path=file_path,
        command=command[:500],
        ts=record.ts,
        raw=_raw(record, parameters, keep_prompts),
        effort=as_text(first(attrs, "effort")),
        ttft_ms=as_int(first(attrs, "ttft_ms")),
    )


def _raw(record: LogRecord, parameters: dict[str, object], keep_prompts: bool) -> str:
    base = json.loads(raw_json(record.raw, keep_prompts))
    if parameters:
        base["cuanta.parameters"] = parameters
    base["cuanta.resource"] = record.resource
    return raw_json(base, keep_prompts)


def map_metric(point: MetricPoint) -> LedgerEvent:
    attrs = point.attributes
    payload = {"name": point.name, "value": point.value, "attributes": attrs}
    return LedgerEvent(
        run_id=as_text(first(attrs, "cuanta.run_id") or first(point.resource, "cuanta.run_id")),
        source=SOURCE,
        session_id=as_text(first(attrs, "session.id")),
        kind=f"metric:{point.name}",
        model=as_text(first(attrs, "model")),
        agent=as_text(first(attrs, "agent.name")),
        query_source=as_text(first(attrs, "query_source")),
        ts=point.ts,
        raw=raw_json(payload, False),
    )
