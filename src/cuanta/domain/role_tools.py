from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from cuanta.domain.context_ceiling import prompt_tokens
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, msg
from cuanta.domain.routing import Role
from cuanta.domain.spectrum import USAGE_KINDS

READERS = ("Read", "Grep", "Glob")
WRITERS = (*READERS, "Edit", "Write")
SESSION_TOOLS = (*WRITERS, "Bash")
MAIN_AGENTS = frozenset({"", "main"})
ROLE_TOOLS: Mapping[Role, tuple[str, ...]] = {
    Role.ANALYST: READERS,
    Role.SCOUT: READERS,
    Role.SENIOR: SESSION_TOOLS,
    Role.TESTER: SESSION_TOOLS,
    Role.DOCS: WRITERS,
}
FIXED_TARGET = 12_000


@dataclass(frozen=True, slots=True)
class CacheUse:
    requests: int
    first_tokens: int
    read_share: float
    misses: int


def role_tools(role: Role) -> tuple[str, ...] | None:
    return ROLE_TOOLS.get(role)


def role_events(events: Sequence[LedgerEvent], roles: Mapping[str, str]) -> tuple[LedgerEvent, ...]:
    return tuple(
        replace(event, agent=role)
        if event.agent in MAIN_AGENTS and (role := roles.get(event.run_id, ""))
        else event
        for event in events
    )


def cache_use(events: Sequence[LedgerEvent]) -> dict[str, CacheUse]:
    grouped: dict[str, list[LedgerEvent]] = {}
    for event in events:
        if event.kind in USAGE_KINDS:
            grouped.setdefault(event.agent or "main", []).append(event)
    found: dict[str, CacheUse] = {}
    for agent, rows in grouped.items():
        prompts = sum(prompt_tokens(row) for row in rows)
        reads = sum(row.cache_read_tokens for row in rows)
        found[agent] = CacheUse(
            len(rows),
            prompt_tokens(rows[0]),
            reads / prompts if prompts else 0.0,
            sum(1 for row in rows[1:] if row.cache_read_tokens == 0),
        )
    return found


def cache_message(role: str, use: CacheUse) -> Message:
    return msg(
        "cache.role",
        role=role,
        requests=use.requests,
        share=f"{use.read_share:.0%}",
        misses=use.misses,
        first=f"{use.first_tokens:,}",
    )
