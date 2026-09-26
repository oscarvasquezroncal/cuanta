from __future__ import annotations

import re
from dataclasses import dataclass, field

SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
PHASE_MARKER = re.compile(r"\bPhase\s+(0\.5|2\.5|2A|2B|[0-6])\b", re.IGNORECASE)
PIPELINE_AGENTS = ("architecture-analyst", "senior", "tester", "docs-updater")
TURN_LIMIT_SUBTYPE = "error_max_turns"


COMMAND_LINE_LIMIT = 30_000


def command_line_length(args: list[str] | tuple[str, ...]) -> int:
    return sum(len(arg) + 2 * arg.count('"') + 3 for arg in args)


@dataclass(frozen=True, slots=True)
class ModelUsage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.reasoning_tokens
        )


@dataclass(frozen=True, slots=True)
class SessionStarted:
    session_id: str
    model: str
    api_key_source: str = ""
    engine_version: str = ""


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    tool_use_id: str
    inputs: dict[str, object] = field(default_factory=dict)
    parent_tool_use_id: str = ""

    @property
    def spawned_agent(self) -> str:
        if self.name not in SUBAGENT_TOOLS:
            return ""
        value = self.inputs.get("subagent_type")
        return value if isinstance(value, str) else ""


@dataclass(frozen=True, slots=True)
class AssistantText:
    text: str
    parent_tool_use_id: str = ""


@dataclass(frozen=True, slots=True)
class StepUsage:
    usage: ModelUsage
    parent_tool_use_id: str = ""
    message_id: str = ""
    write_5m_tokens: int = 0
    write_1h_tokens: int = 0


@dataclass(frozen=True, slots=True)
class RunResult:
    ok: bool
    subtype: str
    cost_usd: float | None
    num_turns: int
    session_id: str
    models: tuple[ModelUsage, ...] = ()
    text: str = ""
    duration_ms: int = 0
    denials: tuple[str, ...] = ()
    terminal_reason: str = ""


def cut_by_turns(subtype: str, terminal_reason: str = "") -> bool:
    return subtype == TURN_LIMIT_SUBTYPE or terminal_reason == "max_turns"


EngineEvent = SessionStarted | ToolCall | AssistantText | StepUsage | RunResult


def phase_markers(text: str) -> tuple[str, ...]:
    seen: list[str] = []
    for match in PHASE_MARKER.finditer(text):
        phase = match.group(1).upper()
        if phase not in seen:
            seen.append(phase)
    return tuple(seen)


@dataclass(frozen=True, slots=True)
class EngineRequest:
    prompt: str
    cwd: str
    env: dict[str, str]
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    model: str = ""
    max_budget_usd: float = 0.0
    agents_file: str = ""
    effort: str = ""
    append_system_prompt: str = ""
    unset_env: tuple[str, ...] = ()
    mcp_config: str = ""
    settings_file: str = ""
    tools: tuple[str, ...] | None = None
    max_turns: int = 0
    stable_prefix: bool = False
    persist_session: bool = True


@dataclass(frozen=True, slots=True)
class EngineOutcome:
    exit_code: int
    result: RunResult | None
    tool_calls: int
    stderr_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.result is not None and self.result.ok

    @property
    def cost_usd(self) -> float | None:
        return self.result.cost_usd if self.result is not None else None

    @property
    def tokens(self) -> int:
        if self.result is None:
            return 0
        return sum(model.total for model in self.result.models)
