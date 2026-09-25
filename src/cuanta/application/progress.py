from __future__ import annotations

from collections.abc import Callable

from cuanta.domain.progress import ProgressEvent

Listener = Callable[[ProgressEvent], None]


class ProgressBus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def publish(self, event: ProgressEvent) -> None:
        for listener in self._listeners:
            listener(event)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)
