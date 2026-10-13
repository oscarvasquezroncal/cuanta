from __future__ import annotations

from dataclasses import dataclass, field

from cuanta.domain.engine import EngineEvent, StepUsage, ToolCall
from cuanta.domain.governor import step_usd
from cuanta.domain.messages import Message, msg
from cuanta.domain.pricing import PriceTable, dollars
from cuanta.domain.progress import LiveStatus, ProgressEvent, StepFinished, StepStarted
from cuanta.domain.received import Received
from cuanta.domain.routing import Provider


@dataclass(frozen=True, slots=True)
class SpendWatch:
    p50_usd: float | None
    p90_usd: float | None
    prices: PriceTable


@dataclass
class LiveRun:
    started: float
    usage: Received = field(default_factory=Received)
    role: str = "main"
    tool: str = ""
    file: str = ""
    roles: dict[str, str] = field(default_factory=dict)
    last: float | None = None
    verifying: float | None = None
    runner: str = ""
    spend: SpendWatch | None = None
    spent: float = 0.0
    warned: set[str] = field(default_factory=set)

    def progress(self, event: ProgressEvent, now: float) -> None:
        if isinstance(event, StepStarted) and event.key == "verdict":
            self.verifying = now
            self.runner = str(dict(event.message.params).get("runner", "")) if event.message else ""
            self.last = None
        elif isinstance(event, StepFinished) and event.key == "verdict":
            self.verifying = None
            self.tool = ""
            self.file = ""

    def observe(self, event: EngineEvent) -> None:
        before = self.usage.requests
        self.usage.add(event)
        if isinstance(event, StepUsage) and self.usage.requests > before:
            self._price(event)
        if isinstance(event, ToolCall):
            if event.spawned_agent:
                self.roles[event.tool_use_id] = event.spawned_agent
                self.role = event.spawned_agent
            else:
                self.role = self.roles.get(event.parent_tool_use_id, "main")
            self.tool = event.name
            path = event.inputs.get("file_path") or event.inputs.get("path")
            self.file = str(path) if path else ""

    def _price(self, event: StepUsage) -> None:
        if self.spend is None:
            return
        price = self.spend.prices.lookup(event.usage.model)
        if price is not None:
            self.spent += step_usd(event, price, Provider.CLAUDE)

    def crossed(self) -> tuple[Message, ...]:
        if self.spend is None:
            return ()
        found: list[Message] = []
        marks = (
            ("p50", self.spend.p50_usd, "live.over_p50"),
            ("p90", self.spend.p90_usd, "live.over_p90"),
        )
        for name, mark, key in marks:
            if mark is None or mark <= 0 or name in self.warned or self.spent <= mark:
                continue
            self.warned.add(name)
            found.append(msg(key, spent=dollars(self.spent), mark=dollars(mark)))
        return tuple(found)

    def take(self, now: float) -> LiveStatus | None:
        if self.last is not None and now - self.last < 0.5:
            return None
        self.last = now
        watched = self.spend
        return LiveStatus(
            max(0.0, now - (self.verifying if self.verifying is not None else self.started)),
            sum(item.total for item in self.usage.usage),
            self.role,
            self.tool,
            self.file,
            "verification" if self.verifying is not None else "",
            self.runner,
            round(self.spent, 4) if watched is not None else None,
            watched.p50_usd if watched is not None else None,
            watched.p90_usd if watched is not None else None,
        )
