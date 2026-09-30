from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from cuanta.domain.index_metrics import executed_tool_events, execution_tool_name
from cuanta.domain.ledger import LedgerEvent, Run
from cuanta.domain.overhead import HOOK_KINDS, attributes
from cuanta.domain.report import AUXILIARY_SOURCES
from cuanta.domain.spectrum import resolve_agents

PHASES = (
    "index_refresh",
    "forecast_plan",
    "sandbox_copy",
    "engine_startup",
    "session_title",
    "api_requests",
    "tools",
    "hooks",
    "verification",
    "repair",
    "handoff",
    "snapshots_guards",
    "after_image",
    "copy_removal",
)


@dataclass(frozen=True, slots=True)
class PhaseTime:
    phase: str
    seconds: float | None = None
    samples: int = 0


@dataclass(frozen=True, slots=True)
class RoleTime:
    role: str
    phases: tuple[PhaseTime, ...] = ()


@dataclass(frozen=True, slots=True)
class RequestTime:
    request_id: str
    role: str
    model: str
    duration_seconds: float | None = None
    ttft_seconds: float | None = None
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class TimeReport:
    wall_seconds: float | None = None
    phases: tuple[PhaseTime, ...] = ()
    roles: tuple[RoleTime, ...] = ()
    requests: tuple[RequestTime, ...] = ()


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _fields(event: LedgerEvent) -> dict[str, object]:
    try:
        raw: object = json.loads(event.raw) if event.raw else {}
    except (ValueError, RecursionError):
        raw = {}
    values = {str(key): value for key, value in raw.items()} if isinstance(raw, dict) else {}
    return {**values, **attributes(event.raw)}


def _duration(event: LedgerEvent, *keys: str) -> float | None:
    fields = _fields(event)
    for key in keys or ("duration_ms",):
        if key in fields and (value := _number(fields[key])) is not None:
            return value / 1000
    return event.duration_ms / 1000 if event.duration_ms > 0 else None


def _summaries(values: dict[str, list[float | None]]) -> tuple[PhaseTime, ...]:
    names = (*PHASES, *sorted(set(values) - set(PHASES)))
    return tuple(
        PhaseTime(
            phase,
            sum(value for value in values[phase] if value is not None)
            if values[phase] and all(value is not None for value in values[phase])
            else None,
            len(values[phase]),
        )
        for phase in names
    )


def analyze_time(run: Run, events: Sequence[LedgerEvent]) -> TimeReport:
    totals: dict[str, list[float | None]] = defaultdict(list)
    roles: dict[str, dict[str, list[float | None]]] = {}
    requests: list[RequestTime] = []
    walls: list[float] = []
    role_names = {event.run_id: event.agent for event in events if event.kind == "run_role"}
    resolved = resolve_agents(events)
    tool_events = set(executed_tool_events(resolved))
    for event in resolved:
        role = (
            role_names.get(event.run_id, event.agent or "main")
            if event.kind != "phase_timing" and event.agent in {"", "main", *AUXILIARY_SOURCES}
            else event.agent or "main"
        )
        buckets = roles.setdefault(role, defaultdict(list))
        phases: tuple[str, ...] = ()
        duration = _duration(event)
        if event.kind == "phase_timing":
            phase = _fields(event).get("phase")
            if phase == "run_wall":
                if duration is not None and run.id and event.run_id == run.id:
                    walls.append(duration)
                continue
            if not isinstance(phase, str) or not phase:
                continue
            phases = (phase,)
        elif event.kind == "api_request":
            title = event.query_source in AUXILIARY_SOURCES or event.agent in AUXILIARY_SOURCES
            phases = ("session_title" if title else "api_requests",)
            fields = _fields(event)
            first = _number(fields.get("ttft_ms"))
            if first is None and event.ttft_ms > 0:
                first = float(event.ttft_ms)
            requests.append(
                RequestTime(
                    str(fields.get("request_id") or fields.get("request.id") or event.id),
                    role,
                    event.model,
                    duration,
                    first / 1000 if first is not None else None,
                    event.query_source,
                )
            )
        elif event in tool_events:
            tool_events.remove(event)
            name = execution_tool_name(event)
            phases = (f"tool:{name}",) if name in {"Agent", "Task"} else ("tools", f"tool:{name}")
        elif event.kind in HOOK_KINDS:
            phases = ("hooks",)
            duration = _duration(event, "total_duration_ms", "duration_ms", "elapsed_ms")
        for phase in phases:
            totals[phase].append(duration)
            buckets[phase].append(duration)
    return TimeReport(
        max(walls) if walls else None,
        _summaries(totals),
        tuple(
            RoleTime(role, _summaries(values)) for role, values in sorted(roles.items()) if values
        ),
        tuple(requests),
    )
