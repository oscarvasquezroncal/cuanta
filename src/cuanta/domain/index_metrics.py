from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cuanta.domain.ledger import LedgerEvent

RAW_TOOLS = frozenset({"read", "grep", "glob"})
INDEX_TOOLS = frozenset({"find", "card", "impact", "facts", "page", "tests_for"})
EXECUTED_STATES = frozenset({"completed", "succeeded", "success", "failed", "error"})
PENDING_STATES = frozenset({"started", "start", "running", "pending"})


@dataclass(frozen=True, slots=True)
class IndexMetrics:
    index_calls: int = 0
    raw_reads: int = 0
    exploration_calls: int = 0
    index_hit_rate: float | None = None
    exploration_tokens_estimate: int = 0
    stale_facts: int = 0
    findings_saved: int = 0
    guard_violations: tuple[str, ...] = ()
    out_of_plan_edits: tuple[str, ...] = ()


def _object(value: object) -> Mapping[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if isinstance(value, Mapping) else {}


def _attributes(event: LedgerEvent) -> dict[str, object]:
    raw = _object(event.raw)
    attributes = raw.get("attributes")
    values = dict(raw)
    if isinstance(attributes, Mapping):
        values.update(attributes)
    elif isinstance(attributes, list):
        for item in attributes:
            if not isinstance(item, Mapping) or not isinstance(item.get("key"), str):
                continue
            value = item.get("value")
            if isinstance(value, Mapping):
                value = next(iter(value.values()), None)
            values[item["key"]] = value
    for key in ("tool_parameters", "cuanta.parameters"):
        values.update(_object(values.get(key)))
    return values


def _tool(event: LedgerEvent) -> tuple[str, str] | None:
    name = event.tool_name
    if name.lower() in RAW_TOOLS:
        return "raw", name.lower()
    if name.startswith("mcp__cuanta__"):
        name = name.removeprefix("mcp__cuanta__")
    elif event.source == "cuanta_mcp":
        name = event.tool_name
    elif event.tool_name == "mcp_tool":
        values = _attributes(event)
        if values.get("mcp_server_name") != "cuanta":
            return None
        value = values.get("mcp_tool_name")
        name = value if isinstance(value, str) else ""
    else:
        return None
    return ("index", name) if name in INDEX_TOOLS else None


def _unique(events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    found: list[LedgerEvent] = []
    rows: set[tuple[str, int]] = set()
    calls: set[tuple[str, str, str, str, str, str, bool]] = set()
    identical: set[LedgerEvent] = set()
    for event in events:
        row = event.run_id, event.id
        call = (
            event.run_id,
            event.source,
            event.session_id,
            event.kind,
            event.tool_name,
            event.tool_use_id,
            _executed(event),
        )
        if event in identical or (event.id > 0 and row in rows):
            continue
        if event.tool_use_id and call in calls:
            continue
        identical.add(event)
        if event.id > 0:
            rows.add(row)
        if event.tool_use_id:
            calls.add(call)
        found.append(event)
    return found


def _executed(event: LedgerEvent) -> bool:
    if event.kind == "tool_result":
        return True
    if event.kind not in {"mcp_tool", "mcp_tool_call"}:
        return False
    value = _attributes(event).get("status")
    status = value if isinstance(value, str) else ""
    if status in PENDING_STATES:
        return False
    return event.success is not None or event.tool_result_bytes > 0 or status in EXECUTED_STATES


def _priority(event: LedgerEvent) -> int:
    return 3 if event.kind == "tool_result" else 2 if _executed(event) else 1


def _order(event: LedgerEvent) -> tuple[str, int]:
    return event.ts, event.id


def _native_calls(events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    identified: dict[tuple[str, str, str], LedgerEvent] = {}
    anonymous: dict[tuple[str, str], dict[int, list[LedgerEvent]]] = {}
    for event in events:
        priority = _priority(event)
        if event.tool_use_id:
            key = event.source, event.session_id, event.tool_use_id
            before = identified.get(key)
            if before is None or priority > _priority(before):
                identified[key] = event
        else:
            phases = anonymous.setdefault((event.source, event.session_id), {})
            phases.setdefault(priority, []).append(event)
    selected = list(identified.values())
    for phases in anonymous.values():
        occurrences: list[LedgerEvent] = []
        for priority in sorted(phases, reverse=True):
            occurrences.extend(sorted(phases[priority], key=_order)[len(occurrences) :])
        selected.extend(occurrences)
    return sorted(selected, key=_order)


def _exploration(events: Sequence[LedgerEvent]) -> list[tuple[str, LedgerEvent]]:
    unique = _unique(events)
    owned: dict[tuple[str, str], list[LedgerEvent]] = defaultdict(list)
    native: dict[tuple[str, str, str], list[LedgerEvent]] = defaultdict(list)
    for event in unique:
        identity = _tool(event)
        if identity is None:
            continue
        kind, name = identity
        if event.source == "cuanta_mcp" and event.kind == "index_call":
            owned[event.run_id, name].append(event)
        elif _executed(event) or event.kind == "tool_use":
            native[event.run_id, kind, name].append(event)
    selected = [("index", event) for items in owned.values() for event in items]
    for (run_id, kind, name), items in native.items():
        calls = _native_calls(items)
        paired = len(owned.get((run_id, name), ())) if kind == "index" else 0
        selected.extend((kind, event) for event in calls[paired:])
    return selected


def _tokens(event: LedgerEvent) -> int:
    value = _attributes(event).get("returned_tokens_estimate")
    if event.source == "cuanta_mcp" and type(value) is int and value >= 0:
        return value
    return max(event.tool_result_bytes, 0) // 4


def index_metrics(
    events: Sequence[LedgerEvent],
    *,
    stale_facts: int = 0,
    findings_saved: int = 0,
    guard_violations: Sequence[str] = (),
    out_of_plan_edits: Sequence[str] = (),
) -> IndexMetrics:
    exploration = _exploration(events)
    calls = sum(kind == "index" for kind, _ in exploration)
    total = len(exploration)
    return IndexMetrics(
        index_calls=calls,
        raw_reads=total - calls,
        exploration_calls=total,
        index_hit_rate=calls / total if total else None,
        exploration_tokens_estimate=sum(_tokens(event) for _, event in exploration),
        stale_facts=max(stale_facts, 0),
        findings_saved=max(findings_saved, 0),
        guard_violations=tuple(dict.fromkeys(guard_violations)),
        out_of_plan_edits=tuple(dict.fromkeys(out_of_plan_edits)),
    )
