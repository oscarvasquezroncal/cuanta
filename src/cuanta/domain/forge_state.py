from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from cuanta.domain.detection import Detection

FORGE_PHASES = ("0", "0.5", "1", "2A", "2B", "2.5", "3", "4", "5", "6")
STATE_VERSION = "0.3"
PRODUCER = "cuanta"


@dataclass(frozen=True, slots=True)
class ForgeRunState:
    version: str
    started_at: str
    size_tier: str
    graph_mode: str
    docs_state: str
    forge_state: str
    verify_tier: str
    phases_completed: tuple[str, ...] = ()
    dirs_written: tuple[str, ...] = ()
    dirs_queued: tuple[str, ...] = ()
    dirs_rejected: tuple[Mapping[str, Any], ...] = ()
    unverified: tuple[Any, ...] = ()
    verify_markers: tuple[Any, ...] = ()
    producer: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return all(phase in self.phases_completed for phase in FORGE_PHASES)

    @property
    def next_phase(self) -> str | None:
        for phase in FORGE_PHASES:
            if phase not in self.phases_completed:
                return phase
        return None

    @property
    def cuanta_handoff(self) -> bool:
        return self.producer == PRODUCER and {"0", "0.5"} <= set(self.phases_completed)


class CorruptState(ValueError):
    pass


def _texts(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CorruptState(f"{key} must be a list of strings")
    return tuple(value)


def _items(value: object, key: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CorruptState(f"{key} must be a list")
    return tuple(value)


KNOWN_KEYS = frozenset(
    {
        "version",
        "started_at",
        "size_tier",
        "graph_mode",
        "docs_state",
        "forge_state",
        "verify_tier",
        "phases_completed",
        "dirs_written",
        "dirs_queued",
        "dirs_rejected",
        "unverified",
        "verify_markers",
        "producer",
    }
)


def parse_state(data: object) -> ForgeRunState:
    if not isinstance(data, dict):
        raise CorruptState("state is not a JSON object")
    phases = _texts(data.get("phases_completed"), "phases_completed")
    unknown = [phase for phase in phases if phase not in FORGE_PHASES]
    if unknown:
        raise CorruptState(f"unknown phases {unknown}")

    def text(key: str) -> str:
        value = data.get(key, "")
        return value if isinstance(value, str) else str(value)

    rejected = _items(data.get("dirs_rejected"), "dirs_rejected")
    return ForgeRunState(
        version=text("version"),
        started_at=text("started_at"),
        size_tier=text("size_tier"),
        graph_mode=text("graph_mode"),
        docs_state=text("docs_state"),
        forge_state=text("forge_state"),
        verify_tier=text("verify_tier"),
        phases_completed=phases,
        dirs_written=_texts(data.get("dirs_written"), "dirs_written"),
        dirs_queued=_texts(data.get("dirs_queued"), "dirs_queued"),
        dirs_rejected=tuple(item for item in rejected if isinstance(item, dict)),
        unverified=_items(data.get("unverified"), "unverified"),
        verify_markers=_items(data.get("verify_markers"), "verify_markers"),
        producer=text("producer"),
        extra={key: value for key, value in data.items() if key not in KNOWN_KEYS},
    )


def state_to_dict(state: ForgeRunState) -> dict[str, Any]:
    document: dict[str, Any] = {
        "version": state.version,
        "started_at": state.started_at,
        "size_tier": state.size_tier,
        "graph_mode": state.graph_mode,
        "docs_state": state.docs_state,
        "forge_state": state.forge_state,
        "verify_tier": state.verify_tier,
        "phases_completed": list(state.phases_completed),
        "dirs_written": list(state.dirs_written),
        "dirs_queued": list(state.dirs_queued),
        "dirs_rejected": [dict(item) for item in state.dirs_rejected],
        "unverified": list(state.unverified),
        "verify_markers": list(state.verify_markers),
    }
    document.update(state.extra)
    if state.producer:
        document["producer"] = state.producer
    return document


def handoff_state(detection: Detection, started_at: str, graph_mode: str) -> ForgeRunState:
    return ForgeRunState(
        version=STATE_VERSION,
        started_at=started_at,
        size_tier=detection.size_tier.value,
        graph_mode=graph_mode,
        docs_state=detection.docs_state.value,
        forge_state=detection.forge_state.value,
        verify_tier=detection.verify_tier.value,
        phases_completed=("0", "0.5"),
        producer=PRODUCER,
    )


def with_graph_mode(state: ForgeRunState, graph_mode: str) -> ForgeRunState:
    return replace(state, graph_mode=graph_mode)


def run_note(state: ForgeRunState | None, problem: str | None) -> str:
    if problem is not None:
        return f"state file discarded: {problem}"
    if state is None or state.complete:
        return "fresh run"
    phase = state.next_phase
    return f"resuming from Phase {phase}" if phase else "fresh run"


@dataclass(frozen=True, slots=True)
class InstallSummary:
    written: tuple[str, ...]
    unchanged: tuple[str, ...]
    new_files: tuple[str, ...]
