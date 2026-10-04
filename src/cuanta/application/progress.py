from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass

from cuanta.domain.messages import Message
from cuanta.domain.progress import SLOW_STEP_S, ProgressEvent, Status, finished, started, took
from cuanta.ports.progress import ProgressSink

Listener = Callable[[ProgressEvent], None]
Cancel = Callable[[], None]
Schedule = Callable[[float, Callable[[], None]], Cancel]
Done = Callable[[Message], None]
Phase = Callable[[str, Message], None]


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


def later(seconds: float, action: Callable[[], None]) -> Cancel:
    timer = threading.Timer(seconds, action)
    timer.daemon = True
    timer.start()
    return timer.cancel


def _unscheduled() -> None:
    return None


def _no_detail(_detail: Message) -> None:
    return None


def _no_phase(_key: str, _label: Message) -> None:
    return None


@dataclass(slots=True)
class _Open:
    key: str
    label: Message
    opened: float
    cancel: Cancel = _unscheduled
    detail: Message | None = None
    shown: bool = False
    closed: bool = False

    def describe(self, detail: Message) -> None:
        self.detail = detail


class SlowSteps:
    def __init__(
        self,
        sink: ProgressSink | None,
        monotonic: Callable[[], float] = time.monotonic,
        schedule: Schedule = later,
    ) -> None:
        self._sink = sink
        self._monotonic = monotonic
        self._schedule = schedule
        self._lock = threading.Lock()

    @contextmanager
    def step(self, key: str, label: Message) -> Iterator[Done]:
        sink = self._sink
        if sink is None:
            yield _no_detail
            return
        current = self._open(sink, key, label)
        try:
            yield current.describe
        except BaseException:
            self._close(sink, current, Status.FAIL)
            raise
        self._close(sink, current, Status.OK)

    @contextmanager
    def sequence(self) -> Iterator[Phase]:
        sink = self._sink
        if sink is None:
            yield _no_phase
            return
        current: list[_Open] = []

        def phase(key: str, label: Message) -> None:
            if current:
                self._close(sink, current.pop(), Status.OK)
            current.append(self._open(sink, key, label))

        try:
            yield phase
        except BaseException:
            if current:
                self._close(sink, current.pop(), Status.FAIL)
            raise
        if current:
            self._close(sink, current.pop(), Status.OK)

    def _open(self, sink: ProgressSink, key: str, label: Message) -> _Open:
        current = _Open(key, label, self._monotonic())
        current.cancel = self._schedule(SLOW_STEP_S, lambda: self._reveal(sink, current))
        return current

    def _reveal(self, sink: ProgressSink, current: _Open) -> None:
        with self._lock:
            if current.shown or current.closed:
                return
            current.shown = True
            with suppress(Exception):
                sink.publish(started(current.key, current.label))

    def _close(self, sink: ProgressSink, current: _Open, status: Status) -> None:
        current.cancel()
        seconds = self._monotonic() - current.opened
        with self._lock:
            current.closed = True
            if not current.shown and seconds < SLOW_STEP_S:
                return
            if not current.shown:
                current.shown = True
                sink.publish(started(current.key, current.label))
            sink.publish(finished(current.key, status, took(seconds, current.detail), seconds))
