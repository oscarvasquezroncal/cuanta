from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, msg
from cuanta.domain.read_discipline import ReadDisciplineDecision

INDEX_FLAG = "index"
SEARCH_TOOLS = frozenset({"Grep", "Glob", "Read"})
RECENT_CALLS = 20
REDUNDANT_PRIOR = 0.8
SAME_QUERY_PRIOR = 0.7
ASK_ABOVE = 0.5
DENY_ABOVE = 0.7


@dataclass(frozen=True, slots=True)
class IndexCall:
    tool: str
    path: str = ""
    line: int = 0
    end_line: int = 0
    query: str = ""


def _text(inputs: Mapping[str, object], name: str) -> str:
    value = inputs.get(name, "")
    return value if isinstance(value, str) else ""


def _number(inputs: Mapping[str, object], name: str) -> int:
    value = inputs.get(name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def query_key(text: str) -> str:
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()[:16]


def suggestion(tool: str, inputs: Mapping[str, object], relative: str) -> str:
    if tool == "Read" and relative:
        return f'mcp__cuanta__card with path "{relative}", then mcp__cuanta__page for the lines'
    pattern = _text(inputs, "pattern") or relative or "what you are looking for"
    return f'mcp__cuanta__find with query "{pattern[:80]}"'


def index_first(
    tool: str, inputs: Mapping[str, object], relative: str, index_calls: int
) -> ReadDisciplineDecision:
    if tool not in SEARCH_TOOLS or index_calls > 0:
        return ReadDisciplineDecision()
    call = suggestion(tool, inputs, relative)
    return ReadDisciplineDecision(
        "deny",
        f"Index first: no index query in this session yet. Call {call}"
        f" and use {tool} only for what the index cannot answer.",
    )


def redundancy_prior(
    tool: str, inputs: Mapping[str, object], relative: str, recent: Sequence[IndexCall]
) -> float:
    window = recent[-RECENT_CALLS:]
    if tool == "Read" and relative:
        start = _number(inputs, "offset") or 1
        end = start + (_number(inputs, "limit") or 10**9) - 1
        covered = any(
            call.path == relative
            and call.tool == "page"
            and call.line <= start
            and end <= max(call.end_line, call.line)
            for call in window
        )
        if covered:
            return REDUNDANT_PRIOR
    if tool == "Grep":
        pattern = _text(inputs, "pattern")
        if pattern.strip() and any(call.query == query_key(pattern) for call in window):
            return SAME_QUERY_PRIOR
    return 0.0


def redundant_decision(tool: str, relative: str, p_yes: float) -> ReadDisciplineDecision:
    if p_yes < DENY_ABOVE:
        return ReadDisciplineDecision()
    target = relative or "this query"
    return ReadDisciplineDecision(
        "deny",
        f"Redundant {tool}: the index already returned {target} in this session; use that answer "
        "or ask mcp__cuanta__page for lines it did not show.",
    )


def parsed_lines(text: str) -> tuple[int, int]:
    first, _, last = text.partition("-")
    try:
        start = int(first)
        return start, int(last) if last else start
    except ValueError:
        return 0, 0


def index_calls(events: Sequence[LedgerEvent]) -> tuple[IndexCall, ...]:
    calls: list[IndexCall] = []
    for event in events:
        if event.kind != "index_call":
            continue
        try:
            raw = json.loads(event.raw or "{}")
        except ValueError:
            raw = {}
        details = raw if isinstance(raw, dict) else {}
        line, end = parsed_lines(str(details.get("lines", "")))
        calls.append(
            IndexCall(
                event.tool_name.removeprefix("mcp__cuanta__"),
                event.file_path,
                line,
                end,
                str(details.get("query_key", "")),
            )
        )
    return tuple(calls)


def index_ratio(index_calls: int, greps: int, reads: int) -> Message:
    return msg("index_first.ratio", index=index_calls, grep=greps, read=reads)
