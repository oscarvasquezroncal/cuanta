from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

REAL_RUN_ID = "01M3YMH7MKDDJTDYZ122B4BD08"
REAL_MODEL = "claude-opus-5-5"
SESSION = "SESSION"
MANDATES = Path(__file__).parent / "fixtures" / "mandates"
PHASES_LINE = "This mandate has 5 phases; the forecast covers one change"
FASES_LINE = "Este mandato tiene 5 fases; el pronóstico cubre un cambio"


def phased_mandate() -> str:
    return (MANDATES / "real_run_es.md").read_text(encoding="utf-8")


def usage(
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: int = 0,
    cache_write: int = 0,
) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }


def init_line(model: str = REAL_MODEL) -> str:
    return json.dumps({"type": "system", "subtype": "init", "session_id": SESSION, "model": model})


def assistant_line(
    message_id: str,
    parent: str = "",
    tool: tuple[str, str, Mapping[str, object]] | None = None,
    model: str = REAL_MODEL,
    tokens: Mapping[str, int] | None = None,
) -> str:
    content: list[dict[str, object]] = []
    if tool is not None:
        name, tool_id, inputs = tool
        content.append({"type": "tool_use", "id": tool_id, "name": name, "input": dict(inputs)})
    else:
        content.append({"type": "text", "text": "working"})
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": message_id,
                "model": model,
                "content": content,
                "usage": dict(tokens) if tokens is not None else usage(),
            },
            "parent_tool_use_id": parent or None,
            "session_id": SESSION,
        }
    )


def result_line(
    text: str = "done",
    cost: float | None = 0.2,
    turns: int = 1,
    subtype: str = "success",
) -> str:
    data: dict[str, object] = {
        "type": "result",
        "subtype": subtype,
        "is_error": subtype != "success",
        "result": text,
        "num_turns": turns,
        "session_id": SESSION,
    }
    if cost is not None:
        data["total_cost_usd"] = cost
    return json.dumps(data)


def scout_spawn(message_id: str = "msg_main_spawn", tool_id: str = "toolu_scout") -> str:
    return assistant_line(
        message_id, tool=("Agent", tool_id, {"subagent_type": "scout", "prompt": "map"})
    )


def scout_read(message_id: str, path: str = "src/app.py", parent: str = "toolu_scout") -> str:
    return assistant_line(
        message_id, parent=parent, tool=("Read", f"toolu_{message_id}", {"file_path": path})
    )
