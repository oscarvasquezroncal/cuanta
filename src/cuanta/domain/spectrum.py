from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, english, msg
from cuanta.domain.pricing import CostEstimate, PriceTable, estimate_cost

USAGE_KINDS = frozenset({"api_request", "sse_event:response.completed"})
FALLBACK_KINDS = frozenset({"result_usage"})
TOOL_KINDS = frozenset({"tool_result", "tool_use"})
READ_TOOLS = frozenset({"Read", "read", "NotebookRead", "View"})
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "edit", "write", "patch"})
SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
UNCERTAIN_AGENTS = frozenset({"", "custom", "subagent"})
TEST_COMMAND = re.compile(
    r"\b(pytest|jest|vitest|go test|cargo test|npm test|pnpm test|yarn test"
    r"|mvn test|gradle test|rspec|phpunit)\b"
)
GATEWAY_COMMAND = re.compile(r"\bcuanta\s+test\b")
BYTES_PER_TOKEN = 4.0
OVERSIZED_TEST_BYTES = 8_192
BOUNDED_TEST_BYTES = 2_048
WINDOW = timedelta(hours=5)
UTILIZATION_LABEL = "heuristic v1"
QUOTA_NOTE = "providers do not publish exact quotas; windows and weeks are for comparison only"


def estimated_tokens(size_bytes: int, bytes_per_token: float = BYTES_PER_TOKEN) -> int:
    if size_bytes <= 0:
        return 0
    return int(size_bytes / (bytes_per_token if bytes_per_token > 0 else BYTES_PER_TOKEN))


@dataclass(frozen=True, slots=True)
class Totals:
    fresh_input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0
    cost_usd: float = 0.0
    requests: int = 0

    @property
    def total(self) -> int:
        return self.fresh_input + self.cache_read + self.cache_write + self.output + self.reasoning

    @property
    def cache_share(self) -> float:
        processed = self.fresh_input + self.cache_read + self.cache_write
        return self.cache_read / processed if processed else 0.0

    def add(self, event: LedgerEvent) -> Totals:
        return Totals(
            fresh_input=self.fresh_input + event.input_tokens,
            cache_read=self.cache_read + event.cache_read_tokens,
            cache_write=self.cache_write + event.cache_write_tokens,
            output=self.output + event.output_tokens,
            reasoning=self.reasoning + event.reasoning_tokens,
            cost_usd=self.cost_usd + event.cost_usd,
            requests=self.requests + 1,
        )


def totals_of(events: Iterable[LedgerEvent]) -> Totals:
    totals = Totals()
    for event in events:
        totals = totals.add(event)
    return totals


def _ts(event: LedgerEvent) -> str:
    return event.ts


def _spawned(event: LedgerEvent) -> str:
    if event.tool_name not in SUBAGENT_TOOLS or not event.raw:
        return ""
    try:
        data = json.loads(event.raw)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    parameters = data.get("cuanta.parameters")
    if isinstance(parameters, dict):
        value = parameters.get("spawned_agent") or parameters.get("subagent_type")
        if isinstance(value, str):
            return value
    value = data.get("spawned_agent")
    return value if isinstance(value, str) else ""


def resolve_agents(events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    ordered = sorted(events, key=lambda event: (event.ts, event.id))
    spawns: list[tuple[str, str, str]] = [
        (event.session_id, event.ts, name) for event in ordered if (name := _spawned(event))
    ]
    resolved: list[LedgerEvent] = []
    for event in ordered:
        if event.agent not in UNCERTAIN_AGENTS or event.kind not in USAGE_KINDS | TOOL_KINDS:
            resolved.append(event if event.agent else replace(event, agent="main"))
            continue
        match = next(
            (
                name
                for session, ts, name in spawns
                if session == event.session_id and ts >= event.ts
            ),
            "",
        )
        fallback = "main" if not event.agent else event.agent
        resolved.append(replace(event, agent=match or fallback))
    return resolved


def usage_events(events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    primary = [event for event in events if event.kind in USAGE_KINDS]
    if primary:
        return primary
    return [event for event in events if event.kind in FALLBACK_KINDS]


def tool_events(events: Sequence[LedgerEvent]) -> list[LedgerEvent]:
    results = [event for event in events if event.kind == "tool_result"]
    return results if results else [event for event in events if event.kind == "tool_use"]


def calibrate(events: Sequence[LedgerEvent]) -> dict[str, float]:
    ordered = sorted(events, key=lambda event: (event.session_id, event.ts, event.id))
    samples: dict[str, list[tuple[int, int]]] = defaultdict(list)
    previous: dict[str, LedgerEvent] = {}
    pending_bytes: dict[str, int] = defaultdict(int)
    for event in ordered:
        key = event.session_id
        if event.kind == "tool_result":
            pending_bytes[key] += event.tool_result_bytes
            continue
        if event.kind not in USAGE_KINDS:
            continue
        before = previous.get(key)
        context = event.input_tokens + event.cache_read_tokens + event.cache_write_tokens
        if before is not None and pending_bytes[key] > 0:
            prior = before.input_tokens + before.cache_read_tokens + before.cache_write_tokens
            delta = context - prior - before.output_tokens
            if delta > 0:
                samples[event.model].append((pending_bytes[key], delta))
        pending_bytes[key] = 0
        previous[key] = event
    ratios: dict[str, float] = {}
    for model, pairs in samples.items():
        if len(pairs) >= 3:
            total_bytes = sum(size for size, _ in pairs)
            total_tokens = sum(tokens for _, tokens in pairs)
            ratio = total_bytes / total_tokens
            if 1.0 <= ratio <= 12.0:
                ratios[model] = ratio
    return ratios


def amplification(
    tool_result: LedgerEvent,
    later: Sequence[LedgerEvent],
    bytes_per_token: float = BYTES_PER_TOKEN,
) -> int:
    requests = 0
    for event in later:
        if event.session_id != tool_result.session_id or event.ts < tool_result.ts:
            continue
        if event.kind == "compaction":
            break
        if event.kind in USAGE_KINDS and event.agent == tool_result.agent:
            requests += 1
    return estimated_tokens(tool_result.tool_result_bytes, bytes_per_token) * requests


class LeakKind(StrEnum):
    AMPLIFICATION = "amplification"
    REPEATED_READ = "repeated_read"
    TEST_OUTPUT = "test_output"
    COMPACTION = "compaction"
    MODEL_SWITCH = "model_switch"


@dataclass(frozen=True, slots=True)
class Leak:
    kind: LeakKind
    subject: str
    agent: str
    tokens: int
    message: Message

    @property
    def detail(self) -> str:
        return english(self.message)


@dataclass(frozen=True, slots=True)
class Suggestion:
    leak: LeakKind
    message: Message

    @property
    def action(self) -> str:
        return english(self.message)


def find_leaks(
    events: Sequence[LedgerEvent], ratios: Mapping[str, float] | None = None
) -> list[Leak]:
    ordered = sorted(events, key=lambda event: (event.ts, event.id))
    bpt = BYTES_PER_TOKEN
    if ratios:
        bpt = sum(ratios.values()) / len(ratios)
    leaks: list[Leak] = []
    results = [event for event in ordered if event.kind == "tool_result"]
    for event in results:
        amount = amplification(event, ordered, bpt)
        if amount > 0:
            subject = event.file_path or event.command[:60] or event.tool_name
            leaks.append(
                Leak(
                    LeakKind.AMPLIFICATION,
                    subject,
                    event.agent,
                    amount,
                    msg(
                        "leak.amplification",
                        tool=event.tool_name,
                        bytes=f"{event.tool_result_bytes:,}",
                    ),
                )
            )
    reads: dict[tuple[str, str], list[LedgerEvent]] = defaultdict(list)
    for event in results or [event for event in ordered if event.kind == "tool_use"]:
        if event.tool_name in READ_TOOLS and event.file_path:
            reads[(event.session_id, event.file_path)].append(event)
    for (_, path), items in reads.items():
        if len(items) >= 2:
            waste = sum(estimated_tokens(item.tool_result_bytes, bpt) for item in items[1:])
            leaks.append(
                Leak(
                    LeakKind.REPEATED_READ,
                    path,
                    items[0].agent,
                    waste,
                    msg("leak.repeated_read", count=len(items)),
                )
            )
    for event in results:
        if (
            TEST_COMMAND.search(event.command)
            and not GATEWAY_COMMAND.search(event.command)
            and event.tool_result_bytes > OVERSIZED_TEST_BYTES
        ):
            waste = estimated_tokens(event.tool_result_bytes - BOUNDED_TEST_BYTES, bpt)
            leaks.append(
                Leak(
                    LeakKind.TEST_OUTPUT,
                    event.command[:60],
                    event.agent,
                    waste,
                    msg("leak.test_output", bytes=f"{event.tool_result_bytes:,}"),
                )
            )
    usage = [event for event in ordered if event.kind in USAGE_KINDS]
    for index, event in enumerate(ordered):
        if event.kind != "compaction":
            continue
        following = next(
            (
                later
                for later in ordered[index + 1 :]
                if later.kind in USAGE_KINDS and later.session_id == event.session_id
            ),
            None,
        )
        rebuild = following.cache_write_tokens if following is not None else 0
        leaks.append(
            Leak(
                LeakKind.COMPACTION,
                event.session_id or "session",
                event.agent,
                rebuild,
                msg("leak.compaction"),
            )
        )
    last: dict[tuple[str, str], LedgerEvent] = {}
    for event in usage:
        key = (event.session_id, event.agent)
        before = last.get(key)
        if before is not None and before.model and event.model and before.model != event.model:
            leaks.append(
                Leak(
                    LeakKind.MODEL_SWITCH,
                    f"{before.model} → {event.model}",
                    event.agent,
                    event.cache_write_tokens,
                    msg("leak.model_switch"),
                )
            )
        last[key] = event
    leaks.sort(key=lambda leak: leak.tokens, reverse=True)
    return leaks


def suggest(leaks: Sequence[Leak]) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    seen: set[tuple[LeakKind, str]] = set()
    for leak in leaks:
        action = msg(f"suggest.{leak.kind.value}", agent=leak.agent, subject=leak.subject)
        key = (leak.kind, english(action))
        if key not in seen:
            seen.add(key)
            suggestions.append(Suggestion(leak.kind, action))
    return suggestions


@dataclass(frozen=True, slots=True)
class Branch:
    label: str
    tokens: int
    share: float
    children: tuple[Branch, ...] = ()
    said: Message | None = None


def named(said: Message, tokens: int, share: float) -> Branch:
    return Branch(english(said), tokens, share, (), said)


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def build_tree(
    label: str, usage: Sequence[LedgerEvent], tools: Sequence[LedgerEvent], top: int = 3
) -> Branch:
    whole = sum(event.total_tokens for event in usage)
    agents: dict[str, list[LedgerEvent]] = defaultdict(list)
    for event in usage:
        agents[event.agent or "main"].append(event)
    agent_branches: list[Branch] = []
    for agent, items in sorted(
        agents.items(), key=lambda pair: -sum(e.total_tokens for e in pair[1])
    ):
        agent_tokens = sum(event.total_tokens for event in items)
        models: dict[str, int] = defaultdict(int)
        for event in items:
            models[event.model or "unknown"] += event.total_tokens
        agent_tools = [event for event in tools if (event.agent or "main") == agent]
        model_branches: list[Branch] = []
        for model, model_tokens in sorted(models.items(), key=lambda pair: -pair[1]):
            tool_totals: dict[str, int] = defaultdict(int)
            file_totals: dict[str, int] = defaultdict(int)
            for event in agent_tools:
                if event.model and event.model != model:
                    continue
                size = estimated_tokens(event.tool_result_bytes or event.tool_input_bytes)
                tool_totals[event.tool_name or "tool"] += size
                if event.file_path:
                    file_totals[event.file_path] += size
            tool_branches = tuple(
                named(msg("spectrum.tool", name=name), tokens, _share(tokens, whole))
                for name, tokens in sorted(tool_totals.items(), key=lambda pair: -pair[1])[:top]
            )
            file_branches = tuple(
                named(msg("spectrum.file", name=name), tokens, _share(tokens, whole))
                for name, tokens in sorted(file_totals.items(), key=lambda pair: -pair[1])[:top]
            )
            model_branches.append(
                Branch(
                    model, model_tokens, _share(model_tokens, whole), tool_branches + file_branches
                )
            )
        agent_branches.append(
            Branch(agent, agent_tokens, _share(agent_tokens, whole), tuple(model_branches))
        )
    return Branch(label, whole, 1.0 if whole else 0.0, tuple(agent_branches))


@dataclass(frozen=True, slots=True)
class Utilization:
    value: float | None
    useful_tokens: int
    total_tokens: int
    label: str = UTILIZATION_LABEL
    why: Message | None = None

    @property
    def reason(self) -> str:
        return english(self.why) if self.why is not None else ""


NO_SNAPSHOTS = msg("spectrum.no_snapshots")
NO_TOKENS = msg("spectrum.no_tokens")


def utilization(
    events: Sequence[LedgerEvent],
    changed_files: frozenset[str],
    resolved_tests: bool,
    total_tokens: int,
    snapshots: bool = True,
) -> Utilization:
    if total_tokens <= 0:
        return Utilization(None, 0, 0, why=NO_TOKENS)
    if not snapshots:
        return Utilization(None, 0, total_tokens, why=NO_SNAPSHOTS)
    useful = 0
    normalized = {path.replace("\\", "/").lstrip("./") for path in changed_files}
    for event in events:
        if event.kind not in TOOL_KINDS:
            continue
        path = event.file_path.replace("\\", "/")
        touched = any(path.endswith(changed) for changed in normalized) if path else False
        if event.tool_name in READ_TOOLS and touched:
            useful += estimated_tokens(event.tool_result_bytes)
        elif event.tool_name in EDIT_TOOLS:
            useful += estimated_tokens(event.tool_input_bytes)
        elif resolved_tests and (
            TEST_COMMAND.search(event.command) or GATEWAY_COMMAND.search(event.command)
        ):
            useful += estimated_tokens(event.tool_result_bytes)
    useful = min(useful, total_tokens)
    return Utilization(useful / total_tokens, useful, total_tokens)


def changed_paths(start: Mapping[str, str], end: Mapping[str, str]) -> frozenset[str]:
    keys = set(start) | set(end)
    return frozenset(path for path in keys if start.get(path) != end.get(path))


@dataclass(frozen=True, slots=True)
class Window:
    label: str
    start: str
    totals: Totals


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def plan_windows(usage: Sequence[LedgerEvent]) -> list[Window]:
    windows: list[Window] = []
    current_start: datetime | None = None
    current = Totals()
    for event in sorted(usage, key=_ts):
        moment = _parse(event.ts)
        if moment is None:
            continue
        if current_start is None or moment >= current_start + WINDOW:
            if current_start is not None:
                windows.append(
                    Window(f"5h #{len(windows) + 1}", current_start.isoformat(), current)
                )
            current_start = moment
            current = Totals()
        current = current.add(event)
    if current_start is not None:
        windows.append(Window(f"5h #{len(windows) + 1}", current_start.isoformat(), current))
    return windows


def plan_weeks(usage: Sequence[LedgerEvent]) -> list[Window]:
    weeks: dict[str, Totals] = {}
    starts: dict[str, str] = {}
    for event in usage:
        moment = _parse(event.ts)
        if moment is None:
            continue
        year, week, _ = moment.isocalendar()
        key = f"{year}-W{week:02d}"
        weeks[key] = weeks.get(key, Totals()).add(event)
        starts.setdefault(key, (moment - timedelta(days=moment.weekday())).date().isoformat())
    return [Window(key, starts[key], weeks[key]) for key in sorted(weeks)]


class View(StrEnum):
    AGENT = "agent"
    MODEL = "model"
    TOOL = "tool"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class Row:
    key: str
    tokens: int
    share: float
    count: int
    cost_usd: float = 0.0


def grouped(view: View, usage: Sequence[LedgerEvent], tools: Sequence[LedgerEvent]) -> list[Row]:
    whole = sum(event.total_tokens for event in usage)
    buckets: dict[str, list[int | float]] = defaultdict(lambda: [0, 0, 0.0])
    if view in {View.AGENT, View.MODEL}:
        for event in usage:
            key = (event.agent or "main") if view is View.AGENT else (event.model or "unknown")
            bucket = buckets[key]
            bucket[0] += event.total_tokens
            bucket[1] += 1
            bucket[2] += event.cost_usd
    else:
        for event in tools:
            key = (event.tool_name or "tool") if view is View.TOOL else event.file_path
            if not key:
                continue
            bucket = buckets[key]
            bucket[0] += estimated_tokens(event.tool_result_bytes or event.tool_input_bytes)
            bucket[1] += 1
    rows = [
        Row(key, int(values[0]), _share(int(values[0]), whole), int(values[1]), float(values[2]))
        for key, values in buckets.items()
    ]
    rows.sort(key=lambda row: row.tokens, reverse=True)
    return rows


@dataclass(frozen=True, slots=True)
class SpectrumReport:
    label: str
    totals: Totals
    tree: Branch
    leaks: tuple[Leak, ...]
    suggestions: tuple[Suggestion, ...]
    utilization: Utilization
    events: int
    source: str
    calibration: Mapping[str, float] = field(default_factory=dict)
    cost: CostEstimate = field(default_factory=lambda: CostEstimate(None, "not computed"))
    title: Message | None = None


def analyze(
    label: str,
    events: Sequence[LedgerEvent],
    changed_files: frozenset[str] = frozenset(),
    resolved_tests: bool = False,
    snapshots: bool = True,
    prices: PriceTable | None = None,
    title: Message | None = None,
) -> SpectrumReport:
    resolved = resolve_agents(events)
    usage = usage_events(resolved)
    tools = tool_events(resolved)
    totals = totals_of(usage)
    ratios = calibrate(resolved)
    leaks = find_leaks(resolved, ratios)
    source = "telemetry" if any(event.kind in USAGE_KINDS for event in usage) else "stream result"
    if not usage:
        source = "none"
    return SpectrumReport(
        label=label,
        title=title,
        totals=totals,
        tree=build_tree(label, usage, tools),
        leaks=tuple(leaks[:5]),
        suggestions=tuple(suggest(leaks[:5])),
        utilization=utilization(tools, changed_files, resolved_tests, totals.total, snapshots),
        events=len(events),
        source=source,
        calibration=ratios,
        cost=estimate_cost(usage, prices),
    )
