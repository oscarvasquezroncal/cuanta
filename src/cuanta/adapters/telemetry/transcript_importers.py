from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from cuanta.adapters.telemetry.mapping import as_int, as_text
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.redaction import redact_for_storage
from cuanta.domain.telemetry import MAIN_AGENT

CLAUDE_SOURCE = "claude_transcript"
CODEX_SOURCE = "codex_transcript"
FILE_KEYS = ("file_path", "path", "notebook_path")


@dataclass
class ImportCounts:
    files: int = 0
    records: int = 0
    events: int = 0
    skipped: int = 0
    errors: int = 0
    sessions: set[str] = field(default_factory=set)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def claude_project_dir(home: Path, project: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project))
    return home / ".claude" / "projects" / slug


def _lines(path: Path, counts: ImportCounts) -> Iterator[dict[str, Any]]:
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        counts.errors += 1
        return
    with handle:
        for line in handle:
            counts.records += 1
            try:
                data = json.loads(line)
            except ValueError:
                counts.skipped += 1
                continue
            if isinstance(data, dict):
                yield data
            else:
                counts.skipped += 1


AGENT_PREFIX = "agent-"


def _agent_id(path: Path) -> str:
    return path.stem.removeprefix(AGENT_PREFIX) if path.stem.startswith(AGENT_PREFIX) else ""


def subagent_label(agent_id: str) -> str:
    return f"subagent {agent_id[:5]}…" if agent_id else "subagent"


def _meta_names(root: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for meta in root.rglob(f"{AGENT_PREFIX}*.meta.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        kind = _dict(data).get("agentType")
        if isinstance(kind, str) and kind:
            names[meta.name.removeprefix(AGENT_PREFIX).removesuffix(".meta.json")] = kind
    return names


def _parent_names(root: Path) -> dict[str, str]:
    spawned: dict[str, str] = {}
    agents: dict[str, str] = {}
    for path in sorted(root.glob("*.jsonl")):
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if "subagent_type" not in line and "agentId" not in line:
                    continue
                try:
                    data = _dict(json.loads(line))
                except ValueError:
                    continue
                for raw_block in _list(_dict(data.get("message")).get("content")):
                    block = _dict(raw_block)
                    if block.get("type") == "tool_use":
                        kind = _dict(block.get("input")).get("subagent_type")
                        if isinstance(kind, str):
                            spawned[as_text(block.get("id"))] = kind
                    if block.get("type") == "tool_result":
                        agent = _dict(data.get("toolUseResult")).get("agentId")
                        if isinstance(agent, str):
                            agents[agent] = as_text(block.get("tool_use_id"))
    return {agent: spawned[use] for agent, use in agents.items() if use in spawned}


def _agent_for_file(path: Path, root: Path, names: dict[str, str]) -> str:
    relative = path.relative_to(root).parts
    agent_id = _agent_id(path)
    if "subagents" in relative or agent_id:
        return names.get(agent_id) or subagent_label(agent_id)
    return MAIN_AGENT


def _tool_use(block: dict[str, Any], base: dict[str, Any]) -> LedgerEvent:
    tool_input = _dict(block.get("input"))
    file_path = next((as_text(tool_input[key]) for key in FILE_KEYS if tool_input.get(key)), "")
    spawned = as_text(tool_input.get("subagent_type"))
    return LedgerEvent(
        **base,
        kind="tool_use",
        tool_name=as_text(block.get("name")),
        tool_use_id=as_text(block.get("id")),
        tool_input_bytes=len(json.dumps(tool_input, default=str)),
        file_path=file_path,
        command=as_text(tool_input.get("command"))[:500],
        raw=redact_for_storage(json.dumps({"spawned_agent": spawned} if spawned else {})),
    )


def _result_bytes(item: dict[str, Any]) -> int:
    content = item.get("content")
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        return sum(
            len(as_text(part.get("text")).encode("utf-8"))
            for part in content
            if isinstance(part, dict)
        )
    return 0


def _results(
    data: dict[str, Any], base: dict[str, Any], pending: dict[str, LedgerEvent]
) -> Iterator[LedgerEvent]:
    ts = as_text(data.get("timestamp"))
    for raw_item in _list(_dict(data.get("message")).get("content")):
        item = _dict(raw_item)
        if item.get("type") != "tool_result":
            continue
        use_id = as_text(item.get("tool_use_id"))
        use = pending.pop(use_id, None)
        size = _result_bytes(item)
        success = not bool(item.get("is_error"))
        if use is None:
            yield LedgerEvent(
                **base,
                kind="tool_result",
                tool_use_id=use_id,
                tool_result_bytes=size,
                success=success,
            )
            continue
        yield replace(
            use, kind="tool_result", ts=ts or use.ts, tool_result_bytes=size, success=success
        )


def import_claude(
    home: Path, project: Path, since: dict[str, str]
) -> tuple[list[LedgerEvent], ImportCounts]:
    counts = ImportCounts()
    root = claude_project_dir(home, project)
    events: list[LedgerEvent] = []
    if not root.is_dir():
        return events, counts
    names = {**_parent_names(root), **_meta_names(root)}
    seen_messages: set[str] = set()
    for path in sorted(root.rglob("*.jsonl")):
        counts.files += 1
        agent = _agent_for_file(path, root, names)
        pending: dict[str, LedgerEvent] = {}
        for data in _lines(path, counts):
            kind = data.get("type")
            session = as_text(data.get("sessionId"))
            ts = as_text(data.get("timestamp"))
            if session and ts and ts <= since.get(session, ""):
                continue
            base: dict[str, Any] = {
                "source": CLAUDE_SOURCE,
                "session_id": session,
                "agent": agent,
                "ts": ts,
                "prompt_id": as_text(data.get("promptId")),
            }
            if kind == "assistant":
                message = _dict(data.get("message"))
                identifier = as_text(message.get("id")) or as_text(data.get("requestId"))
                model = as_text(message.get("model"))
                for raw_block in _list(message.get("content")):
                    block = _dict(raw_block)
                    if block.get("type") == "tool_use":
                        use = _tool_use(block, {**base, "model": model})
                        pending[use.tool_use_id] = use
                if identifier in seen_messages:
                    continue
                seen_messages.add(identifier)
                usage = _dict(message.get("usage"))
                counts.sessions.add(session)
                events.append(
                    LedgerEvent(
                        **base,
                        kind="api_request",
                        model=model,
                        input_tokens=as_int(usage.get("input_tokens")),
                        output_tokens=as_int(usage.get("output_tokens")),
                        cache_read_tokens=as_int(usage.get("cache_read_input_tokens")),
                        cache_write_tokens=as_int(usage.get("cache_creation_input_tokens")),
                        tool_use_id=identifier,
                    )
                )
            elif kind == "user":
                events.extend(_results(data, base, pending))
            elif kind == "system" and data.get("subtype") == "compact_boundary":
                events.append(LedgerEvent(**base, kind="compaction"))
            else:
                counts.skipped += 1
        events.extend(pending.values())
    counts.events = len(events)
    return events, counts


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").lower()


def _same_path(left: str, right: Path) -> bool:
    return _normalize_path(left) == _normalize_path(str(right))


def import_codex(
    home: Path, project: Path, since: dict[str, str]
) -> tuple[list[LedgerEvent], ImportCounts]:
    counts = ImportCounts()
    root = home / ".codex" / "sessions"
    events: list[LedgerEvent] = []
    if not root.is_dir():
        return events, counts
    for path in sorted(root.rglob("rollout-*.jsonl")):
        counts.files += 1
        session = ""
        model = ""
        matched = False
        last_total = -1
        for data in _lines(path, counts):
            record_type = data.get("type")
            payload = _dict(data.get("payload"))
            if record_type == "session_meta":
                session = as_text(payload.get("id"))
                matched = _same_path(as_text(payload.get("cwd")), project)
                continue
            if not matched:
                counts.skipped += 1
                continue
            if record_type == "turn_context":
                model = as_text(payload.get("model")) or model
                continue
            ts = as_text(data.get("timestamp"))
            if ts and ts <= since.get(session, ""):
                continue
            if record_type == "event_msg" and payload.get("type") == "token_count":
                info = _dict(payload.get("info"))
                total = _dict(info.get("total_token_usage"))
                last = _dict(info.get("last_token_usage"))
                total_tokens = as_int(total.get("total_tokens"))
                if total_tokens <= last_total or not last:
                    counts.skipped += 1
                    continue
                last_total = total_tokens
                cached = as_int(last.get("cached_input_tokens"))
                counts.sessions.add(session)
                events.append(
                    LedgerEvent(
                        source=CODEX_SOURCE,
                        session_id=session,
                        agent=MAIN_AGENT,
                        kind="api_request",
                        model=model,
                        input_tokens=max(as_int(last.get("input_tokens")) - cached, 0),
                        cache_read_tokens=cached,
                        cache_write_tokens=as_int(last.get("cache_write_input_tokens")),
                        output_tokens=as_int(last.get("output_tokens")),
                        reasoning_tokens=as_int(last.get("reasoning_output_tokens")),
                        ts=ts,
                    )
                )
            elif record_type == "compacted":
                events.append(
                    LedgerEvent(source=CODEX_SOURCE, session_id=session, kind="compaction", ts=ts)
                )
            else:
                counts.skipped += 1
    counts.events = len(events)
    return events, counts
