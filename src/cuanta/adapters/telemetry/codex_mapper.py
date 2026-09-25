from __future__ import annotations

from cuanta.adapters.telemetry.mapping import (
    as_bool,
    as_int,
    as_text,
    first,
    parse_json_object,
    raw_json,
)
from cuanta.adapters.telemetry.otlp_json import LogRecord
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.telemetry import MAIN_AGENT

SOURCE = "codex"
PREFIX = "codex."
FILE_KEYS = ("path", "file_path")


def handles(record: LogRecord) -> bool:
    service = as_text(record.resource.get("service.name")).lower()
    return record.name.startswith(PREFIX) or service.startswith("codex")


def map_log(record: LogRecord, keep_prompts: bool = False) -> LedgerEvent:
    attrs = record.attributes
    kind = record.name.removeprefix(PREFIX)
    arguments = parse_json_object(first(attrs, "arguments"))
    command_value = arguments.get("command")
    command = (
        " ".join(str(part) for part in command_value)
        if isinstance(command_value, list)
        else as_text(command_value)
    )
    file_path = next((as_text(arguments[key]) for key in FILE_KEYS if arguments.get(key)), "")
    tokens_counted = kind == "sse_event"
    output = first(attrs, "output")
    return LedgerEvent(
        run_id=as_text(first(attrs, "cuanta.run_id") or first(record.resource, "cuanta.run_id")),
        source=SOURCE,
        session_id=as_text(first(attrs, "conversation.id")),
        prompt_id="",
        trace_id=record.trace_id,
        agent=MAIN_AGENT,
        kind=kind if not tokens_counted else f"sse_event:{as_text(first(attrs, 'event.kind'))}",
        model=as_text(first(attrs, "model")),
        input_tokens=as_int(first(attrs, "input_token_count")) if tokens_counted else 0,
        output_tokens=as_int(first(attrs, "output_token_count")) if tokens_counted else 0,
        cache_read_tokens=as_int(first(attrs, "cached_token_count")) if tokens_counted else 0,
        reasoning_tokens=as_int(first(attrs, "reasoning_token_count")) if tokens_counted else 0,
        tool_name=as_text(first(attrs, "tool_name")),
        tool_use_id=as_text(first(attrs, "call_id")),
        tool_result_bytes=len(as_text(output).encode("utf-8")) if output is not None else 0,
        duration_ms=as_int(first(attrs, "duration_ms")),
        success=as_bool(first(attrs, "success")),
        file_path=file_path,
        command=command[:500],
        ts=record.ts,
        raw=raw_json(record.raw, keep_prompts),
    )
