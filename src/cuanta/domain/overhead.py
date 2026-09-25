from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, msg
from cuanta.domain.report import ContextSplit, context_split

HOOK_KINDS = frozenset({"hook_execution_complete", "hook_execution"})
PLUGIN_KINDS = frozenset({"plugin_loaded"})
MCP_KINDS = frozenset({"mcp_server_connection"})
FIRST_REQUEST = "api_request"
PROMPT_KIND = "user_prompt"
SPAWN_KIND = "spawn"
SPAWN_SOURCE = "cuanta"
NOT_SESSION = frozenset({SPAWN_KIND, "result_usage"})
METRIC_PREFIX = "metric:"
LIST_LIMIT = 6
FAILED_STATES = frozenset({"failed", "error", "failure", "disconnected", "timeout"})

PLUGIN_NAME = ("plugin.name", "plugin_name", "plugin", "name")
PLUGIN_VERSION = ("plugin.version", "plugin_version", "version")
SERVER_NAME = ("server_name", "mcp_server_name", "server.name", "mcp.server", "name")
SERVER_STATUS = ("status", "connection_status", "state", "result")
SERVER_ERROR = ("error", "error_message", "error.message", "reason")
HOOK_NAME = ("hook_name", "hook.name", "hook_event_name", "hook_event", "event_type", "hook")
HOOK_OUTPUT = (
    "additional_context_length",
    "additional_context_chars",
    "output_length",
    "output_size",
    "stdout_length",
)
DURATION = ("duration_ms", "duration", "elapsed_ms")


@dataclass(frozen=True, slots=True)
class HookRun:
    name: str
    duration_ms: int
    output_chars: int


@dataclass(frozen=True, slots=True)
class ServerConnection:
    name: str
    ok: bool
    error: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class Startup:
    spawn_ms: int | None
    setup_ms: int
    queue_ms: int
    request_ms: int
    ttft_ms: int
    output_tokens: int

    @property
    def before_request_ms(self) -> int:
        return (self.spawn_ms or 0) + self.setup_ms + self.queue_ms

    @property
    def total_ms(self) -> int:
        return self.before_request_ms + self.request_ms

    @property
    def measured(self) -> bool:
        return self.spawn_ms is not None or self.total_ms > 0


@dataclass(frozen=True, slots=True)
class SessionOverhead:
    split: ContextSplit | None
    plugins: tuple[str, ...]
    servers: tuple[ServerConnection, ...]
    hooks: tuple[HookRun, ...]
    startup: Startup | None

    @property
    def startup_ms(self) -> int | None:
        return self.startup.before_request_ms if self.startup is not None else None

    @property
    def failed_servers(self) -> tuple[ServerConnection, ...]:
        return tuple(server for server in self.servers if not server.ok)

    @property
    def hook_ms(self) -> int:
        return sum(hook.duration_ms for hook in self.hooks)

    @property
    def hook_chars(self) -> int:
        return sum(hook.output_chars for hook in self.hooks)

    @property
    def empty(self) -> bool:
        return not (self.plugins or self.servers or self.hooks)


def _value(item: object) -> object:
    if not isinstance(item, Mapping):
        return item
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in item:
            return item[key]
    return None


def attributes(raw: str) -> dict[str, object]:
    try:
        record = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    found = record.get("attributes") if isinstance(record, dict) else None
    if isinstance(found, dict):
        return {str(key): value for key, value in found.items()}
    if isinstance(found, list):
        return {
            str(item["key"]): _value(item.get("value"))
            for item in found
            if isinstance(item, dict) and "key" in item
        }
    return {}


def _text(values: Mapping[str, object], keys: Sequence[str]) -> str:
    for key in keys:
        value = values.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _number(values: Mapping[str, object], keys: Sequence[str]) -> int:
    for key in keys:
        value = values.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value))
            except ValueError:
                continue
    return 0


def _ok(values: Mapping[str, object], error: str) -> bool:
    success = values.get("success")
    if isinstance(success, bool):
        return success
    if isinstance(success, str) and success.lower() in {"true", "false"}:
        return success.lower() == "true"
    status = _text(values, SERVER_STATUS).lower()
    return not error and status not in FAILED_STATES


def _stamp(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_ms(epoch_ms: int) -> str:
    moment = datetime.fromtimestamp(epoch_ms / 1000, UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def spawn_event(run_id: str, trace_id: str, epoch_ms: int) -> LedgerEvent:
    return LedgerEvent(
        run_id=run_id, source=SPAWN_SOURCE, trace_id=trace_id, kind=SPAWN_KIND, ts=iso_ms(epoch_ms)
    )


def _millis(later: datetime, earlier: datetime) -> int:
    return max(0, int((later - earlier).total_seconds() * 1000))


def _request_start(event: LedgerEvent, end: datetime) -> datetime:
    return end - timedelta(milliseconds=event.duration_ms)


def _stamps(events: Sequence[LedgerEvent], kind: str) -> list[datetime]:
    return [stamp for event in events if event.kind == kind and (stamp := _stamp(event.ts))]


def startup_of(events: Sequence[LedgerEvent]) -> Startup | None:
    requests = [
        (_request_start(event, stamp), stamp, event)
        for event in events
        if event.kind == FIRST_REQUEST and (stamp := _stamp(event.ts)) is not None
    ]
    if not requests:
        return None
    start, end, first = min(requests, key=lambda item: item[0])
    session = [
        stamp
        for event in events
        if event.kind not in NOT_SESSION | {FIRST_REQUEST}
        and not event.kind.startswith(METRIC_PREFIX)
        and (stamp := _stamp(event.ts)) is not None
    ]
    opened = min([*session, start])
    prompts = _stamps(events, PROMPT_KIND)
    prompt = min(prompts) if prompts else opened
    spawns = _stamps(events, SPAWN_KIND)
    return Startup(
        spawn_ms=_millis(opened, min(spawns)) if spawns else None,
        setup_ms=_millis(prompt, opened),
        queue_ms=_millis(start, prompt),
        request_ms=_millis(end, start),
        ttft_ms=first.ttft_ms or _number(attributes(first.raw), ("ttft_ms",)),
        output_tokens=first.output_tokens,
    )


def prompt_length(events: Sequence[LedgerEvent]) -> int:
    for event in events:
        if event.kind == PROMPT_KIND:
            length = _number(attributes(event.raw), ("prompt_length",))
            if length:
                return length
    return 0


def session_overhead(events: Sequence[LedgerEvent], prompt_chars: int) -> SessionOverhead:
    prompt_chars = prompt_chars or prompt_length(events)
    plugins: dict[str, str] = {}
    servers: dict[str, ServerConnection] = {}
    hooks: list[HookRun] = []
    for event in events:
        if event.kind not in HOOK_KINDS | PLUGIN_KINDS | MCP_KINDS:
            continue
        values = {**attributes(event.raw)}
        if event.kind in PLUGIN_KINDS:
            name = _text(values, PLUGIN_NAME) or "plugin"
            version = _text(values, PLUGIN_VERSION)
            plugins[name] = f"{name} {version}".strip()
        elif event.kind in MCP_KINDS:
            name = _text(values, SERVER_NAME) or "server"
            error = _text(values, SERVER_ERROR)
            duration = _number(values, DURATION) or event.duration_ms
            servers[name] = ServerConnection(name, _ok(values, error), error, duration)
        else:
            name = _text(values, HOOK_NAME) or "hook"
            duration = _number(values, DURATION) or event.duration_ms
            hooks.append(HookRun(name, duration, _number(values, HOOK_OUTPUT)))
    return SessionOverhead(
        split=context_split(events, prompt_chars),
        plugins=tuple(plugins.values()),
        servers=tuple(servers.values()),
        hooks=tuple(hooks),
        startup=startup_of(events),
    )


def _names(items: Sequence[str]) -> str:
    shown = ", ".join(items[:LIST_LIMIT])
    extra = len(items) - LIST_LIMIT
    return f"{shown} +{extra}" if extra > 0 else shown


def overhead_messages(overhead: SessionOverhead) -> tuple[Message, ...]:
    lines: list[Message] = []
    split = overhead.split
    if split is not None and split.request:
        lines.append(
            msg(
                "overhead.context",
                fixed=f"{split.fixed:,}",
                share=f"{split.fixed_share:.0%}",
                total=f"{split.first_request:,}",
            )
        )
    elif split is not None:
        lines.append(msg("overhead.context_total", total=f"{split.first_request:,}"))
    if overhead.plugins:
        lines.append(
            msg("overhead.plugins", count=len(overhead.plugins), names=_names(overhead.plugins))
        )
    if overhead.servers:
        names = [server.name for server in overhead.servers]
        lines.append(msg("overhead.servers", count=len(names), names=_names(names)))
    for server in overhead.failed_servers:
        lines.append(
            msg(
                "overhead.failed",
                name=server.name,
                seconds=f"{server.duration_ms / 1000:.1f}",
                error=server.error or "no reason given",
            )
        )
    if overhead.hooks:
        lines.append(
            msg(
                "overhead.hooks",
                count=len(overhead.hooks),
                ms=f"{overhead.hook_ms:,}",
                chars=f"{overhead.hook_chars:,}",
            )
        )
    if overhead.startup is not None and overhead.startup.measured:
        lines.append(startup_message(overhead.startup))
    return tuple(lines)


def _seconds(ms: int | None) -> str:
    return "n/a" if ms is None else f"{ms / 1000:.1f} s"


def startup_message(startup: Startup) -> Message:
    return msg(
        "overhead.startup",
        before=_seconds(startup.before_request_ms),
        spawn=_seconds(startup.spawn_ms),
        setup=_seconds(startup.setup_ms),
        queue=_seconds(startup.queue_ms),
        request=_seconds(startup.request_ms),
        ttft=_seconds(startup.ttft_ms or None),
        output=f"{startup.output_tokens:,}",
    )


def startup_payload(startup: Startup | None) -> dict[str, object] | None:
    if startup is None:
        return None
    return {
        "spawn_to_session_ms": startup.spawn_ms,
        "session_setup_ms": startup.setup_ms,
        "prompt_to_request_ms": startup.queue_ms,
        "first_request_ms": startup.request_ms,
        "first_token_ms": startup.ttft_ms or None,
        "first_request_output_tokens": startup.output_tokens,
    }


def overhead_payload(overhead: SessionOverhead) -> dict[str, object]:
    split = overhead.split
    return {
        "first_request_tokens": split.first_request if split is not None else None,
        "fixed_context_tokens": split.fixed if split is not None else None,
        "plugins": list(overhead.plugins),
        "mcp_servers": [
            {"name": server.name, "ok": server.ok, "error": server.error or None}
            for server in overhead.servers
        ],
        "hooks": [
            {"name": hook.name, "duration_ms": hook.duration_ms, "chars": hook.output_chars}
            for hook in overhead.hooks
        ],
        "startup_ms": overhead.startup_ms,
        "startup": startup_payload(overhead.startup),
    }
