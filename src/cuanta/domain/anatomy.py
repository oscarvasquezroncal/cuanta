from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from cuanta.domain.costs import sum_costs
from cuanta.domain.ledger import LedgerEvent

USAGE_KINDS = frozenset({"api_request", "sse_event:response.completed"})
TOOL_KINDS = frozenset(
    {"tool_decision", "tool_use", "tool_result", "mcp_tool", "mcp_tool_call", "index_call"}
)
SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
HANDOFF_WRAPPER_TOKENS = 256
ANATOMY_HEURISTIC = (
    "Exclusive request phases; writing compares output with fresh input plus cache write, "
    "excluding cache read. Handoff matches a closed child window to the next parent request "
    "when cache write is within 50% of final child output plus 256 wrapper tokens. "
    "These are observed heuristics, not causal token attribution."
)


class Phase(StrEnum):
    START = "start"
    EXPLORATION = "exploration"
    WRITING = "writing"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class PhaseTotals:
    fresh_input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0
    cost_usd: float | None = 0.0
    requests: int = 0

    @property
    def total(self) -> int:
        return self.fresh_input + self.cache_read + self.cache_write + self.output + self.reasoning

    @property
    def cache_share(self) -> float:
        processed = self.fresh_input + self.cache_read + self.cache_write
        return self.cache_read / processed if processed else 0.0

    def add(self, event: LedgerEvent) -> PhaseTotals:
        return PhaseTotals(
            fresh_input=self.fresh_input + event.input_tokens,
            cache_read=self.cache_read + event.cache_read_tokens,
            cache_write=self.cache_write + event.cache_write_tokens,
            output=self.output + event.output_tokens,
            reasoning=self.reasoning + event.reasoning_tokens,
            cost_usd=sum_costs((self.cost_usd, event.cost_usd)),
            requests=self.requests + 1,
        )


@dataclass(frozen=True, slots=True)
class PhaseSummary:
    phase: Phase
    totals: PhaseTotals


@dataclass(frozen=True, slots=True)
class AgentAnatomy:
    agent: str
    phases: tuple[PhaseSummary, ...]
    totals: PhaseTotals


@dataclass(frozen=True, slots=True)
class UsagePhase:
    event_id: int
    run_id: str
    session_id: str
    agent: str
    ts: str
    phase: Phase
    tokens: int
    cost_usd: float | None
    totals: PhaseTotals = PhaseTotals()


@dataclass(frozen=True, slots=True)
class AnatomyReport:
    totals: PhaseTotals = PhaseTotals()
    phases: tuple[PhaseSummary, ...] = ()
    agents: tuple[AgentAnatomy, ...] = ()
    events: tuple[UsagePhase, ...] = ()
    heuristic: str = ANATOMY_HEURISTIC


@dataclass(frozen=True, slots=True)
class _ChildWindow:
    run_id: str
    session_id: str
    parent: str
    child: str
    start: tuple[datetime, int]
    end: tuple[datetime, int]


def _moment(ts: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _stamp(event: LedgerEvent) -> tuple[datetime, int] | None:
    moment = _moment(event.ts)
    return (moment, event.id) if moment is not None else None


def _order(event: LedgerEvent) -> tuple[datetime, str, int]:
    return _moment(event.ts) or datetime.min.replace(tzinfo=UTC), event.ts, event.id


def _agent_key(event: LedgerEvent) -> tuple[str, str, str]:
    return event.run_id, event.session_id, event.agent or "main"


def _object(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _fields(event: LedgerEvent) -> dict[str, object]:
    fields = _object(event.raw)
    attributes = fields.get("attributes")
    if isinstance(attributes, dict):
        fields.update(_object(attributes))
    elif isinstance(attributes, list):
        for attribute in attributes:
            if not isinstance(attribute, dict) or not isinstance(attribute.get("key"), str):
                continue
            value = attribute.get("value")
            if isinstance(value, dict):
                value = value.get("stringValue")
            fields[attribute["key"]] = value
    return fields


def _text(fields: Mapping[str, object], *keys: str) -> str:
    return next((value for key in keys if isinstance(value := fields.get(key), str) and value), "")


def _spawned(event: LedgerEvent) -> str:
    if event.tool_name not in SUBAGENT_TOOLS:
        return ""
    fields = _fields(event)
    for key in ("cuanta.parameters", "tool_parameters", "tool_input", "tool.parameters"):
        name = _text(_object(fields.get(key)), "spawned_agent", "subagent_type")
        if name:
            return name
    return _text(fields, "spawned_agent", "subagent_type")


def _child_windows(events: Sequence[LedgerEvent]) -> list[_ChildWindow]:
    pending: dict[tuple[str, str, str], list[LedgerEvent]] = defaultdict(list)
    windows: list[_ChildWindow] = []
    for event in events:
        stamp = _stamp(event)
        if stamp is None or not event.session_id:
            continue
        if event.kind == "tool_decision" and (name := _spawned(event)):
            starts = pending[event.run_id, event.session_id, name]
            if event not in starts:
                starts.append(event)
        elif event.kind == "subagent_completed":
            name = _text(_fields(event), "agent_type", "subagent_type")
            starts = pending.pop((event.run_id, event.session_id, name), [])
            if len(starts) > 1:
                pending[event.run_id, event.session_id, name] = starts[1:]
            if len(starts) != 1:
                continue
            start = _stamp(starts[0])
            if start is not None and start < stamp:
                windows.append(
                    _ChildWindow(
                        event.run_id,
                        event.session_id,
                        starts[0].agent or "main",
                        name,
                        start,
                        stamp,
                    )
                )
    return windows


def _handoffs(events: Sequence[LedgerEvent], usage: Sequence[LedgerEvent]) -> frozenset[int]:
    candidates: dict[int, list[int]] = defaultdict(list)
    for window in _child_windows(events):
        parent = window.run_id, window.session_id, window.parent
        child = window.run_id, window.session_id, window.child
        child_requests = [
            event
            for event in usage
            if _agent_key(event) == child
            and (stamp := _stamp(event)) is not None
            and window.start < stamp < window.end
        ]
        if not child_requests:
            continue
        following = next(
            (
                (index, event)
                for index, event in enumerate(usage)
                if _agent_key(event) == parent
                and (stamp := _stamp(event)) is not None
                and stamp > window.end
            ),
            None,
        )
        if following is None:
            continue
        index, request = following
        request_stamp = _stamp(request)
        if request_stamp is None:
            continue
        intervening = any(
            _agent_key(event) == parent
            and event.kind in TOOL_KINDS
            and not (event.kind == "tool_result" and event.tool_name in SUBAGENT_TOOLS)
            and (stamp := _stamp(event)) is not None
            and window.end < stamp < request_stamp
            for event in events
        )
        if not intervening:
            candidates[index].append(child_requests[-1].output_tokens)
    found: set[int] = set()
    for index, outputs in candidates.items():
        if len(outputs) != 1:
            continue
        output = outputs[0]
        cache_write = usage[index].cache_write_tokens
        if (
            output > 0
            and cache_write > 0
            and abs(cache_write - output) <= output * 0.5 + HANDOFF_WRAPPER_TOKENS
        ):
            found.add(index)
    return frozenset(found)


def analyze_anatomy(
    resolved_events: Sequence[LedgerEvent], selected_usage: Sequence[LedgerEvent] | None = None
) -> AnatomyReport:
    ordered = sorted(resolved_events, key=_order)
    if selected_usage is None:
        primary_runs = {event.run_id for event in ordered if event.kind in USAGE_KINDS}
        usage = [
            event
            for event in ordered
            if event.kind in USAGE_KINDS
            or (event.kind == "result_usage" and event.run_id not in primary_runs)
        ]
    else:
        usage = sorted(selected_usage, key=_order)
    if not usage:
        return AnatomyReport()
    last_tools: dict[tuple[str, str, str], tuple[datetime, int]] = {}
    for event in ordered:
        if event.kind in TOOL_KINDS and (stamp := _stamp(event)) is not None:
            last_tools[_agent_key(event)] = stamp
    handoffs = _handoffs(ordered, usage)
    started: set[tuple[str, str, str]] = set()
    phases = {phase: PhaseTotals() for phase in Phase}
    agent_phases: dict[str, dict[Phase, PhaseTotals]] = {}
    agents: dict[str, PhaseTotals] = {}
    totals = PhaseTotals()
    classified: list[UsagePhase] = []
    for index, event in enumerate(usage):
        key = _agent_key(event)
        stamp = _stamp(event)
        last_tool = last_tools.get(key)
        if key not in started:
            phase = Phase.START
            started.add(key)
        elif index in handoffs:
            phase = Phase.HANDOFF
        elif (
            stamp is not None
            and last_tool is not None
            and stamp > last_tool
            and event.output_tokens > event.input_tokens + event.cache_write_tokens
        ):
            phase = Phase.WRITING
        else:
            phase = Phase.EXPLORATION
        agent = key[2]
        totals = totals.add(event)
        phases[phase] = phases[phase].add(event)
        agents[agent] = agents.get(agent, PhaseTotals()).add(event)
        buckets = agent_phases.setdefault(agent, {phase: PhaseTotals() for phase in Phase})
        buckets[phase] = buckets[phase].add(event)
        classified.append(
            UsagePhase(
                event.id,
                event.run_id,
                event.session_id,
                agent,
                event.ts,
                phase,
                event.total_tokens,
                event.cost_usd,
                PhaseTotals().add(event),
            )
        )
    return AnatomyReport(
        totals=totals,
        phases=tuple(PhaseSummary(phase, phases[phase]) for phase in Phase),
        agents=tuple(
            AgentAnatomy(
                agent,
                tuple(PhaseSummary(phase, agent_phases[agent][phase]) for phase in Phase),
                agents[agent],
            )
            for agent in sorted(agents)
        ),
        events=tuple(classified),
    )
