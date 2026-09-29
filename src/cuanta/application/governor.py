from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from cuanta.domain.agents import role_of
from cuanta.domain.engine import EngineEvent, RunResult, SessionStarted, StepUsage, ToolCall
from cuanta.domain.governor import (
    Projection,
    Reaction,
    ReactionKind,
    RolePlan,
    RoleProgress,
    codex_spend,
    decide,
    path_key,
    project,
    project_path,
    step_usd,
    windows_root,
)
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.pricing import Price, PriceTable, event_cost
from cuanta.domain.routing import Provider, Role
from cuanta.domain.spectrum import EDIT_TOOLS, READ_TOOLS

API_REQUEST = "api_request"
PAGE_TOOL = "mcp__cuanta__page"
CODEX_EDIT = "file_change"
PATH_KEYS = ("file_path", "notebook_path", "path")
STREAM = "stream"
OTLP = "otlp"

GovernorEvent = EngineEvent | LedgerEvent


@dataclass(slots=True)
class _Context:
    first: int
    last: int
    requests: int = 1

    @property
    def growth(self) -> float:
        return (self.last - self.first) / (self.requests - 1) if self.requests > 1 else 0.0


@dataclass(slots=True)
class _Tally:
    source: str = ""
    requests: int = 0
    priced_usd: float = 0.0
    priced_requests: int = 0
    unpriced: bool = False
    reported_usd: float = 0.0
    reported: bool = False
    tokens: int = 0
    cache_read: int = 0
    context_sum: int = 0
    output_sum: int = 0
    items: int = 0
    edit_calls: int = 0
    first_edit_step: int = 0
    edits: set[str] = field(default_factory=set)
    stray: set[str] = field(default_factory=set)
    reads: set[str] = field(default_factory=set)
    leaks: set[str] = field(default_factory=set)
    contexts: dict[str, _Context] = field(default_factory=dict)
    active: str = ""

    def request(self, agent: str, context: int, output: int, tokens: int, cached: int) -> None:
        self.requests += 1
        self.tokens += tokens
        self.cache_read += cached
        self.context_sum += context
        self.output_sum += output
        found = self.contexts.get(agent)
        if found is None:
            self.contexts[agent] = _Context(context, context)
        else:
            found.last = context
            found.requests += 1
        self.active = agent

    def restart(self) -> None:
        self.contexts.clear()
        self.active = ""


@dataclass(slots=True)
class _Lane:
    plan: RolePlan
    root: str
    windows: bool
    edit_keys: frozenset[str]
    read_keys: frozenset[str]
    tally: _Tally = field(default_factory=_Tally)
    agents: dict[Role, _Tally] = field(default_factory=dict)
    spawns: dict[str, Role | None] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    done: set[ReactionKind] = field(default_factory=set)
    started: float | None = None
    ended: float | None = None


def _lane(plan: RolePlan, root: str) -> _Lane:
    windows = windows_root(root)
    edits = frozenset(path_key(path, windows) for path in plan.edit_paths)
    reads = frozenset(path_key(path, windows) for path in plan.read_paths)
    return _Lane(plan, root, windows, edits, reads | edits if reads else frozenset())


def _tool_path(call: ToolCall) -> str:
    for key in PATH_KEYS:
        value = call.inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


class Governor:
    def __init__(
        self,
        plans: Sequence[RolePlan],
        clock: Callable[[], float],
        root: str = "",
        prices: PriceTable | None = None,
    ) -> None:
        self._clock = clock
        self._prices = prices
        self._lanes = {plan.role: _lane(plan, root) for plan in plans}
        self._reactions: list[Reaction] = []

    @property
    def reactions(self) -> tuple[Reaction, ...]:
        return tuple(self._reactions)

    def start(self, role: Role) -> None:
        lane = self._get(role)
        lane.started = self._clock()
        lane.ended = None

    def restart(self, role: Role) -> None:
        lane = self._get(role)
        lane.tally.restart()
        for tally in lane.agents.values():
            tally.restart()
        lane.ended = None
        lane.done.discard(ReactionKind.FINISH_NOW)

    def observe(self, role: Role, event: GovernorEvent) -> tuple[Reaction, ...]:
        lane = self._get(role)
        if lane.started is None:
            lane.started = self._clock()
        if isinstance(event, LedgerEvent):
            self._telemetry(lane, event)
        elif isinstance(event, StepUsage):
            self._step(lane, event)
        elif isinstance(event, ToolCall):
            self._tool(lane, event)
        elif isinstance(event, RunResult):
            lane.ended = self._clock()
            return ()
        elif isinstance(event, SessionStarted):
            return ()
        return self._react(lane)

    def check(self, role: Role) -> tuple[Reaction, ...]:
        return self._react(self._get(role))

    def progress(self, role: Role) -> RoleProgress:
        lane = self._get(role)
        return self._progress(lane, lane.tally)

    def subagents(self, role: Role) -> dict[Role, RoleProgress]:
        lane = self._get(role)
        return {agent: self._progress(lane, tally) for agent, tally in lane.agents.items()}

    def projection(self, role: Role) -> Projection:
        lane = self._get(role)
        return project(lane.plan, self._progress(lane, lane.tally))

    def _get(self, role: Role) -> _Lane:
        lane = self._lanes.get(role)
        if lane is None:
            raise ValueError(f"the governor has no plan for the {role.value} role")
        return lane

    def _elapsed(self, lane: _Lane) -> float:
        if lane.started is None:
            return 0.0
        end = lane.ended if lane.ended is not None else self._clock()
        return max(end - lane.started, 0.0)

    def _react(self, lane: _Lane) -> tuple[Reaction, ...]:
        if lane.ended is not None:
            return ()
        decision = decide(lane.plan, self._progress(lane, lane.tally), lane.done)
        if decision is None:
            return ()
        lane.done.add(decision.kind)
        reaction = Reaction(
            lane.plan.role,
            decision.kind,
            decision.trigger,
            self._elapsed(lane),
            decision.projection,
            decision.saving_usd,
        )
        self._reactions.append(reaction)
        return (reaction,)

    def _price(self, lane: _Lane, model: str) -> Price | None:
        found = self._prices.lookup(model) if self._prices is not None and model else None
        return found if found is not None else lane.plan.price

    def _agent(self, lane: _Lane, parent: str) -> _Tally | None:
        role = lane.spawns.get(parent) if parent else None
        if role is None:
            return None
        return lane.agents.setdefault(role, _Tally())

    def _tallies(self, lane: _Lane, parent: str) -> tuple[_Tally, ...]:
        agent = self._agent(lane, parent)
        return (lane.tally,) if agent is None else (lane.tally, agent)

    def _step(self, lane: _Lane, event: StepUsage) -> None:
        if event.message_id:
            if event.message_id in lane.seen:
                return
            lane.seen.add(event.message_id)
        usage = event.usage
        price = self._price(lane, usage.model)
        cost = (
            step_usd(event, price, lane.plan.provider, lane.plan.ttl_s)
            if price is not None
            else None
        )
        context = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
        output = usage.output_tokens + usage.reasoning_tokens
        for tally in self._tallies(lane, event.parent_tool_use_id):
            if cost is None:
                tally.unpriced = True
            else:
                tally.priced_usd += cost
                tally.priced_requests += 1
            if tally.source in {"", STREAM}:
                tally.source = STREAM
                tally.request(
                    event.parent_tool_use_id, context, output, usage.total, usage.cache_read_tokens
                )

    def _telemetry(self, lane: _Lane, event: LedgerEvent) -> None:
        if event.kind != API_REQUEST:
            return
        price = self._price(lane, event.model)
        cost = event.cost_usd
        if cost is None and price is not None:
            cost = event_cost(event, price)
        role = role_of(event.agent) if event.agent else None
        tallies = [lane.tally]
        if role is not None and role is not lane.plan.role:
            tallies.append(lane.agents.setdefault(role, _Tally()))
        context = event.input_tokens + event.cache_read_tokens + event.cache_write_tokens
        output = event.output_tokens + event.reasoning_tokens
        for tally in tallies:
            if cost is not None:
                tally.reported_usd += cost
                tally.reported = True
            if tally.source in {"", OTLP}:
                tally.source = OTLP
                tally.request(
                    event.agent, context, output, event.total_tokens, event.cache_read_tokens
                )

    def _tool(self, lane: _Lane, call: ToolCall) -> None:
        tallies = self._tallies(lane, call.parent_tool_use_id)
        spawned = call.spawned_agent
        if spawned and call.tool_use_id:
            lane.spawns[call.tool_use_id] = role_of(spawned)
        for tally in tallies:
            tally.items += 1
            if call.name == CODEX_EDIT:
                tally.edit_calls += 1
                if not tally.first_edit_step:
                    tally.first_edit_step = tally.items
        path = project_path(_tool_path(call), lane.root)
        if not path:
            return
        if call.name in EDIT_TOOLS:
            for tally in tallies:
                self._edit(lane, tally, path)
        elif call.name in READ_TOOLS or call.name == PAGE_TOOL:
            for tally in tallies:
                tally.reads.add(path)
                if lane.read_keys and path not in lane.read_keys:
                    tally.leaks.add(path)

    def _edit(self, lane: _Lane, tally: _Tally, path: str) -> None:
        tally.edit_calls += 1
        if path not in lane.edit_keys:
            tally.stray.add(path)
            return
        tally.edits.add(path)
        if not tally.first_edit_step:
            tally.first_edit_step = max(tally.requests, 1)

    def _spent(self, lane: _Lane, tally: _Tally) -> tuple[float | None, bool]:
        reported = tally.reported_usd if tally.reported else None
        if lane.plan.provider is Provider.CODEX:
            estimate = codex_spend(lane.plan, tally.items, self._elapsed(lane))
            known = [value for value in (estimate, reported) if value is not None]
            return (max(known) if known else None), reported is None
        priced = tally.priced_usd if tally.priced_requests and not tally.unpriced else None
        known = [value for value in (priced, reported) if value is not None]
        if known:
            return max(known), False
        return (0.0 if tally.requests == 0 and not tally.unpriced else None), False

    def _progress(self, lane: _Lane, tally: _Tally) -> RoleProgress:
        spent, estimated = self._spent(lane, tally)
        active = tally.contexts.get(tally.active)
        planned = len(lane.edit_keys)
        codex = lane.plan.provider is Provider.CODEX
        return RoleProgress(
            requests=tally.requests,
            spent_usd=spent,
            estimated=estimated,
            tokens=tally.tokens,
            context_tokens=active.last if active is not None else 0,
            growth_tokens=active.growth if active is not None else 0.0,
            growth_requests=active.requests if active is not None else 0,
            output_tokens=tally.output_sum / tally.requests if tally.requests else 0.0,
            cache_read_share=tally.cache_read / tally.context_sum if tally.context_sum else 0.0,
            items=tally.items,
            edits=min(tally.edit_calls, planned) if codex else len(tally.edits),
            edit_calls=tally.edit_calls,
            stray_edits=len(tally.stray),
            first_edit_step=tally.first_edit_step,
            reads=len(tally.reads),
            leaked_reads=len(tally.leaks),
            elapsed_s=self._elapsed(lane),
        )
