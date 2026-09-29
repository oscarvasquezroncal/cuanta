from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest

from cuanta.adapters.engines.codex import CodexParser
from cuanta.application.governor import Governor
from cuanta.application.steering import Steering
from cuanta.domain.change_plan import ChangePlan, EditTarget
from cuanta.domain.engine import RunResult
from cuanta.domain.envelope import Buckets, FixedPrefix, RoleForecast, StopRules
from cuanta.domain.governor import ReactionKind, RolePlan, Trigger, role_plan
from cuanta.domain.progress import ProgressEvent
from cuanta.domain.routing import Provider, Role

MODEL = "gpt-6-sol"
THREAD = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"
SHARE = 0.4785
FORECAST_P50 = 0.2574
FORECAST_REQUESTS = 18
USD_PER_SECOND = 0.001314
RECORDED_COST = 1.8189
SHARE_REACHED_AT_S = 366.0
MCP, SHELL, EDIT = "mcp_tool_call", "command_execution", "file_change"
SHAPE: tuple[tuple[int, float, str], ...] = (
    (1, 32.0, MCP),
    (5, 0.0, MCP),
    (1, 6.0, MCP),
    (13, 4.0, MCP),
    (1, 36.0, MCP),
    (2, 6.0, MCP),
    (1, 6.0, SHELL),
    (6, 5.5, MCP),
    (1, 64.0, MCP),
    (7, 4.0, MCP),
    (1, 56.0, SHELL),
    (1, 40.0, EDIT),
    (20, 16.0, EDIT),
    (20, 16.0, MCP),
    (19, 16.0, SHELL),
)
PLAN = ChangePlan(edit=tuple(EditTarget(f"src/ui/part_{index}.tsx", 0.75) for index in range(6)))


@dataclass
class Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Sink:
    events: list[ProgressEvent] = field(default_factory=list)

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)


@dataclass
class Halt:
    clock: Clock
    calls: list[tuple[float, float | None]] = field(default_factory=list)

    def __call__(self, estimate: Callable[[], float | None]) -> bool:
        self.calls.append((self.clock.now, estimate()))
        return True


def line(payload: dict[str, object]) -> str:
    return json.dumps(payload)


def recorded_shape() -> Iterator[tuple[float, str]]:
    at = 0.0
    number = 0
    yield at, line({"type": "thread.started", "thread_id": THREAD})
    for count, gap, kind in SHAPE:
        for _ in range(count):
            number += 1
            at += gap
            yield (
                at,
                line({"type": "item.completed", "item": {"id": f"r{number}", "type": "reasoning"}}),
            )
            yield at, line({"type": "item.completed", "item": {"id": f"i{number}", "type": kind}})
    yield (
        at + 60.0,
        line(
            {
                "type": "item.completed",
                "item": {"id": "last", "type": "agent_message", "text": "{}"},
            }
        ),
    )
    yield (
        at + 60.0,
        line(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 5_774_358,
                    "cached_input_tokens": 5_644_416,
                    "output_tokens": 43_018,
                },
            }
        ),
    )


def senior_plan(share: float, per_item: bool, per_second: bool) -> RolePlan:
    forecast = RoleForecast(
        role=Role.SENIOR,
        model=MODEL,
        buckets=Buckets(),
        requests=FORECAST_REQUESTS if per_item else 0,
        p50_usd=FORECAST_P50 if per_item else None,
        p90_usd=None,
        share=0.0,
        stops=StopRules(0, 0, 0),
        fixed=FixedPrefix(20_000, False),
        factor=1.0,
    )
    rate = USD_PER_SECOND if per_second else 0.0
    return role_plan(forecast, Provider.CODEX, share, PLAN, usd_per_second=rate)


@dataclass
class Replay:
    governor: Governor
    steering: Steering
    halt: Halt
    items: int
    ended: RunResult | None


def replay(plan: RolePlan) -> Replay:
    clock = Clock()
    governor = Governor([plan], clock, "/srv/app")
    halt = Halt(clock)
    steering = Steering(governor, Role.SENIOR, lambda _text: False, Sink(), halt=halt)
    steering.started("senior-run")
    parser = CodexParser(MODEL)
    for at, text in recorded_shape():
        clock.now = at
        for event in parser.feed(text):
            steering(event)
            if steering.stopped is not None:
                break
        if steering.stopped is not None:
            return Replay(governor, steering, halt, governor.progress(Role.SENIOR).items, None)
    ended = parser.finish(0)
    if ended is not None:
        steering(ended)
    return Replay(governor, steering, halt, governor.progress(Role.SENIOR).items, ended)


@pytest.mark.parametrize(
    ("per_item", "per_second", "stop_item"),
    [(True, True, 29), (True, False, 29), (False, True, 39)],
)
def test_a_stream_shaped_like_the_x3_codex_senior_stops_at_or_before_its_share(
    per_item: bool, per_second: bool, stop_item: int
) -> None:
    run = replay(senior_plan(SHARE, per_item, per_second))
    stopped = run.steering.stopped
    assert stopped is not None
    assert (stopped.kind, stopped.trigger) == (ReactionKind.CODEX_STOP, Trigger.SHARE)
    assert len(run.halt.calls) == 1
    assert run.items == stop_item
    estimate = stopped.projection.spent_usd
    assert estimate is not None
    assert stopped.projection.estimated
    assert run.halt.calls[0][1] == pytest.approx(estimate)
    assert 0.85 * SHARE <= estimate <= SHARE
    assert stopped.at_s <= SHARE_REACHED_AT_S
    assert run.governor.progress(Role.SENIOR).edit_calls == 0
    assert [taken.sent for taken in run.steering.taken] == [True]
    assert run.ended is None


def test_the_same_stream_under_a_share_that_covers_it_runs_to_the_end() -> None:
    run = replay(senior_plan(RECORDED_COST * 1.4, per_item=True, per_second=True))
    assert run.steering.stopped is None
    assert run.halt.calls == []
    assert run.steering.taken == []
    assert run.items == sum(count for count, _, _ in SHAPE)
    assert run.ended is not None
    assert run.ended.ok
    assert run.ended.models[0].cache_read_tokens == 5_644_416
    spent = run.governor.progress(Role.SENIOR).spent_usd
    assert spent is not None
    assert spent < 0.85 * RECORDED_COST * 1.4
