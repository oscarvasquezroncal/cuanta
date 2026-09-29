from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.cache import PrefixState, PrefixWindow
from cuanta.domain.messages import Message, msg

TITLE_LIMIT = 60


@dataclass(frozen=True, slots=True)
class QueueEntry:
    id: str
    added_at: str
    args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QueueSlot:
    entry: QueueEntry
    provider: str
    model: str = ""
    kind: str = ""
    request: str = ""
    copy: bool = False

    @property
    def lane(self) -> tuple[str, str, bool]:
        return (self.provider.strip().casefold(), self.model.strip().casefold(), self.copy)

    @property
    def title(self) -> str:
        line = " ".join(self.request.split())
        return line if len(line) <= TITLE_LIMIT else line[: TITLE_LIMIT - 1] + "…"


class QueueStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    NOT_RUN = "not_run"
    GONE = "gone"


@dataclass(frozen=True, slots=True)
class QueueOutcome:
    slot: QueueSlot
    status: QueueStatus
    detail: str = ""


def queue_order(slots: Sequence[QueueSlot]) -> tuple[QueueSlot, ...]:
    lanes: dict[tuple[str, str, bool], list[QueueSlot]] = {}
    for slot in slots:
        lanes.setdefault(slot.lane, []).append(slot)
    return tuple(slot for lane in lanes.values() for slot in lane)


def warm_known(window: PrefixWindow) -> bool:
    return window.until is not None and window.state is not PrefixState.UNKNOWN


def warm_message(window: PrefixWindow, time: str) -> Message:
    if not warm_known(window):
        return msg("queue.warm_unknown")
    if window.state is PrefixState.WARM:
        return msg("queue.warm_until", time=time)
    return msg("queue.cold_since", time=time)


def warm_payload(window: PrefixWindow) -> dict[str, object]:
    if window.until is None or not warm_known(window):
        return {"state": PrefixState.UNKNOWN.value, "until": None}
    return {"state": window.state.value, "until": window.until.isoformat()}
