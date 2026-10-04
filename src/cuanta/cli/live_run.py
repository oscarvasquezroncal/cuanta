from __future__ import annotations

import threading
import time
from types import TracebackType

from cuanta.cli.presenters.base import Presenter
from cuanta.domain.engine import EngineEvent
from cuanta.domain.live_run import LiveRun
from cuanta.domain.progress import Note, ProgressEvent, Status


class RunProgress:
    def __init__(self, sink: Presenter, verbose: bool) -> None:
        self._sink = sink
        self._verbose = verbose
        self._live = LiveRun(time.monotonic())
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._tick, daemon=True)

    def publish(self, event: ProgressEvent) -> None:
        if isinstance(event, Note) and event.status is Status.INFO and not self._verbose:
            key = event.message.key if event.message is not None else ""
            if key.startswith(
                ("forecast.", "envelope.", "time.", "docs.", "template.", "scout.", "pack.")
            ):
                return
        if (
            isinstance(event, Note)
            and event.message is not None
            and not self._verbose
            and event.message.key == "scout.leaks"
        ):
            return
        self._sink.publish(event)

    def observe(self, event: EngineEvent) -> None:
        with self._lock:
            self._live.observe(event)

    def _tick(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                state = self._live.take(time.monotonic())
            if state is not None:
                self._sink.publish(state)
            self._stop.wait(0.5)

    def __enter__(self) -> RunProgress:
        self._thread.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._thread.join()
