from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from cuanta.domain.engine import (
    AssistantText,
    EngineEvent,
    RunResult,
    SessionStarted,
    StepUsage,
    ToolCall,
)

STAGES = ("analyst", "senior", "tester", "docs")
STAGE_HINTS = (("analyst",), ("senior", "developer", "engineer"), ("tester", "test"), ("docs",))
FEED_LIMIT = 200
MAIN = "main"


class CardState(StrEnum):
    WAITING = "waiting"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


def stage_of(agent: str) -> int | None:
    lowered = agent.lower()
    for index, hints in enumerate(STAGE_HINTS):
        if any(hint in lowered for hint in hints):
            return index
    return None


@dataclass
class AgentCard:
    stage: str
    agent: str = ""
    state: CardState = CardState.WAITING
    tokens: int = 0
    tools: int = 0


@dataclass
class Pipeline:
    stages: tuple[str, ...] = STAGES
    main_context: bool = False
    cards: list[AgentCard] = field(default_factory=lambda: [AgentCard(name) for name in STAGES])
    feed: list[str] = field(default_factory=list)
    spawns: dict[str, int] = field(default_factory=dict)
    seen_messages: set[str] = field(default_factory=set)
    main_tokens: int = 0
    main_tools: int = 0
    finished: bool = False
    ok: bool = False
    cost_usd: float | None = 0.0
    order: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cards = [AgentCard(name) for name in self.stages]

    def _main_card(self) -> AgentCard:
        card = self.cards[0]
        if card.state is CardState.WAITING:
            card.agent = MAIN
            card.state = CardState.ACTIVE
            self.order.append(0)
        return card

    def card_for(self, parent_tool_use_id: str) -> AgentCard | None:
        index = self.spawns.get(parent_tool_use_id)
        return self.cards[index] if index is not None else None

    def _log(self, line: str) -> None:
        self.feed.append(line)
        if len(self.feed) > FEED_LIMIT:
            del self.feed[: len(self.feed) - FEED_LIMIT]

    def _activate(self, index: int, agent: str, tool_use_id: str) -> None:
        for card in self.cards:
            if card.state is CardState.ACTIVE:
                card.state = CardState.DONE
        card = self.cards[index]
        card.agent = agent
        card.state = CardState.ACTIVE
        if tool_use_id:
            self.spawns[tool_use_id] = index
        self.order.append(index)
        self._log(f"handoff to {agent}")

    def apply(self, event: EngineEvent) -> None:
        if isinstance(event, SessionStarted):
            if self.main_context:
                self._main_card()
            self._log(f"session started with {event.model or 'the default model'}")
        elif isinstance(event, ToolCall):
            self._tool(event)
        elif isinstance(event, StepUsage):
            self._usage(event)
        elif isinstance(event, AssistantText):
            text = " ".join(event.text.split())
            if text:
                self._log(text[:160])
        elif isinstance(event, RunResult):
            self._finish(event)

    def _tool(self, event: ToolCall) -> None:
        agent = event.spawned_agent
        if agent:
            index = stage_of(agent)
            if not self.main_context and index is not None and STAGES[index] in self.stages:
                self._activate(self.stages.index(STAGES[index]), agent, event.tool_use_id)
                return
            self._log(f"handoff to {agent}")
            return
        card = self.card_for(event.parent_tool_use_id)
        if card is None:
            self.main_tools += 1
            if self.main_context:
                self._main_card().tools += 1
            owner = MAIN
        else:
            card.tools += 1
            owner = card.agent
        self._log(f"{owner}: {event.name}")

    def _usage(self, event: StepUsage) -> None:
        if event.message_id:
            if event.message_id in self.seen_messages:
                return
            self.seen_messages.add(event.message_id)
        usage = event.usage
        tokens = (
            usage.input_tokens
            + usage.output_tokens
            + usage.cache_read_tokens
            + usage.cache_write_tokens
            + usage.reasoning_tokens
        )
        card = self.card_for(event.parent_tool_use_id)
        if card is None:
            self.main_tokens += tokens
            if self.main_context:
                self._main_card().tokens += tokens
        else:
            card.tokens += tokens

    def _finish(self, event: RunResult) -> None:
        self.finished = True
        self.ok = event.ok
        self.cost_usd = event.cost_usd
        for card in self.cards:
            if card.state is CardState.ACTIVE:
                card.state = CardState.DONE if event.ok else CardState.FAILED
        self._log(f"run finished: {event.subtype or ('success' if event.ok else 'error')}")
