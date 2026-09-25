from __future__ import annotations

import json
from typing import Any

from cuanta.domain.engine import (
    AssistantText,
    EngineEvent,
    ModelUsage,
    RunResult,
    SessionStarted,
    StepUsage,
    ToolCall,
)


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _float(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _models(data: dict[str, Any]) -> tuple[ModelUsage, ...]:
    usage = data.get("modelUsage")
    if not isinstance(usage, dict):
        return ()
    models: list[ModelUsage] = []
    for name, values in usage.items():
        if not isinstance(values, dict):
            continue
        models.append(
            ModelUsage(
                model=str(name),
                input_tokens=_int(values.get("inputTokens")),
                output_tokens=_int(values.get("outputTokens")),
                cache_read_tokens=_int(values.get("cacheReadInputTokens")),
                cache_write_tokens=_int(values.get("cacheCreationInputTokens")),
                reasoning_tokens=_int(values.get("thinkingTokens")),
                cost_usd=_float(values.get("costUSD")),
            )
        )
    return tuple(models)


def _step_usage(message: object, parent: str) -> StepUsage | None:
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    return StepUsage(
        ModelUsage(
            model=str(message.get("model") or ""),
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
            cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
            cache_write_tokens=_int(usage.get("cache_creation_input_tokens")),
        ),
        parent,
        str(message.get("id") or ""),
    )


def _denials(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("tool_name") or item.get("name") or item.get("tool")
            if isinstance(name, str):
                names.append(name)
    return tuple(dict.fromkeys(names))


def parse_line(line: str) -> list[EngineEvent]:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return []
    try:
        data = json.loads(stripped)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    kind = data.get("type")
    parent = str(data.get("parent_tool_use_id") or "")
    if kind == "system" and data.get("subtype") == "init":
        return [SessionStarted(str(data.get("session_id") or ""), str(data.get("model") or ""))]
    if kind == "assistant":
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        events: list[EngineEvent] = []
        step = _step_usage(message, parent)
        if step is not None:
            events.append(step)
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                inputs = block.get("input")
                events.append(
                    ToolCall(
                        name=str(block.get("name") or ""),
                        tool_use_id=str(block.get("id") or ""),
                        inputs=inputs if isinstance(inputs, dict) else {},
                        parent_tool_use_id=parent,
                    )
                )
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                events.append(AssistantText(block["text"], parent))
        return events
    if kind == "result":
        return [
            RunResult(
                ok=not bool(data.get("is_error")) and data.get("subtype") == "success",
                subtype=str(data.get("subtype") or ""),
                cost_usd=_float(data.get("total_cost_usd")),
                num_turns=_int(data.get("num_turns")),
                session_id=str(data.get("session_id") or ""),
                models=_models(data),
                text=str(data.get("result") or ""),
                duration_ms=_int(data.get("duration_ms")),
                denials=_denials(data.get("permission_denials")),
            )
        ]
    return []
