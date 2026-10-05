from __future__ import annotations

from dataclasses import dataclass, field

from cuanta.domain.engine import EngineEvent, ToolCall
from cuanta.domain.progress import LiveStatus, ProgressEvent, StepFinished, StepStarted
from cuanta.domain.received import Received


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
        self.usage.add(event)
        if isinstance(event, ToolCall):
            if event.spawned_agent:
                self.roles[event.tool_use_id] = event.spawned_agent
                self.role = event.spawned_agent
            else:
                self.role = self.roles.get(event.parent_tool_use_id, "main")
            self.tool = event.name
            path = event.inputs.get("file_path") or event.inputs.get("path")
            self.file = str(path) if path else ""

    def take(self, now: float) -> LiveStatus | None:
        if self.last is not None and now - self.last < 0.5:
            return None
        self.last = now
        return LiveStatus(
            max(0.0, now - (self.verifying if self.verifying is not None else self.started)),
            sum(item.total for item in self.usage.usage),
            self.role,
            self.tool,
            self.file,
            "verification" if self.verifying is not None else "",
            self.runner,
        )
