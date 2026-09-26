from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from cuanta.domain.ledger import LedgerEvent
from cuanta.domain.messages import Message, msg
from cuanta.domain.report import first_request_event
from cuanta.domain.spectrum import usage_events

CACHE_ENGINE = "claude"


class CacheState(StrEnum):
    WARM = "warm"
    COLD = "cold"


@dataclass(frozen=True, slots=True)
class FirstRequestCache:
    fresh: int
    read: int
    written: int

    @property
    def total(self) -> int:
        return self.fresh + self.read + self.written

    @property
    def share(self) -> float:
        return self.read / self.total if self.total > 0 else 0.0

    @property
    def state(self) -> CacheState:
        return CacheState.WARM if self.read > 0 else CacheState.COLD


def first_request_cache(events: Sequence[LedgerEvent]) -> FirstRequestCache | None:
    first = first_request_event(events)
    if first is None:
        return None
    cache = FirstRequestCache(first.input_tokens, first.cache_read_tokens, first.cache_write_tokens)
    return cache if cache.total > 0 else None


def cache_message(cache: FirstRequestCache) -> Message:
    if cache.state is CacheState.WARM:
        return msg(
            "overhead.cache_warm",
            read=f"{cache.read:,}",
            total=f"{cache.total:,}",
            share=f"{cache.share:.0%}",
        )
    return msg("overhead.cache_cold", written=f"{cache.written:,}", total=f"{cache.total:,}")


def cache_payload(cache: FirstRequestCache | None) -> dict[str, object] | None:
    if cache is None:
        return None
    return {
        "state": cache.state.value,
        "cache_read_tokens": cache.read,
        "cache_write_tokens": cache.written,
        "context_tokens": cache.total,
        "share": round(cache.share, 4),
    }


class PrefixState(StrEnum):
    UNKNOWN = "unknown"
    WARM = "warm"
    COLD = "cold"


@dataclass(frozen=True, slots=True)
class PrefixWindow:
    state: PrefixState
    until: datetime | None = None


UNKNOWN_PREFIX = PrefixWindow(PrefixState.UNKNOWN)


def _moment(ts: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def last_request_at(events: Sequence[LedgerEvent]) -> datetime | None:
    moments = [moment for event in usage_events(events) if (moment := _moment(event.ts))]
    return max(moments, default=None)


def moment_of(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms / 1000, UTC)


def prefix_window(events: Sequence[LedgerEvent], ttl_s: int, now: datetime) -> PrefixWindow:
    if ttl_s <= 0:
        return UNKNOWN_PREFIX
    cache = first_request_cache(events)
    if cache is None or (cache.read <= 0 and cache.written <= 0):
        return UNKNOWN_PREFIX
    last = last_request_at(events)
    if last is None:
        return UNKNOWN_PREFIX
    until = last + timedelta(seconds=ttl_s)
    moment = now if now.tzinfo else now.replace(tzinfo=UTC)
    return PrefixWindow(PrefixState.WARM if moment < until else PrefixState.COLD, until)
