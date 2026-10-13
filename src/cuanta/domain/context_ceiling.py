from __future__ import annotations

from collections.abc import Sequence

from cuanta.domain.advisor import family_of
from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, msg
from cuanta.domain.spectrum import USAGE_KINDS
from cuanta.domain.versions import at_least

COMPACT_MIN_VERSION = "2.1.296"
COMPACT_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
WIDE_CEILING = 120_000
SMALL_CEILING = 100_000


def context_ceiling(model: str) -> int:
    return SMALL_CEILING if family_of(model) == "haiku" else WIDE_CEILING


def compact_supported(version: str) -> bool:
    return at_least(version, COMPACT_MIN_VERSION, False)


def prompt_tokens(event: LedgerEvent) -> int:
    return event.input_tokens + event.cache_read_tokens + event.cache_write_tokens


def max_context(events: Sequence[LedgerEvent]) -> dict[str, tuple[int, str]]:
    found: dict[str, tuple[int, str]] = {}
    for event in events:
        if event.kind not in USAGE_KINDS:
            continue
        agent = event.agent or "main"
        tokens = prompt_tokens(event)
        if tokens > found.get(agent, (0, ""))[0]:
            found[agent] = (tokens, event.model)
    return found


def context_message(role: str, tokens: int, ceiling: int) -> Message:
    key = "context.over" if tokens > ceiling else "context.max"
    return msg(key, role=role, tokens=f"{tokens:,}", ceiling=f"{ceiling:,}")
