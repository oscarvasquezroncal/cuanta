from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from cuanta.domain.errors import EnvironmentFailure
from cuanta.domain.queue import QueueEntry

QUEUE_FILE = "queue.json"
LOCK_FILE = "queue.lock"
WRITE_LOCK_FILE = "queue.write.lock"
QUEUE_VERSION = 1
FIRST_ID = 1
WRITE_WAIT_S = 5.0
WRITE_POLL_S = 0.02


class FileQueueStore:
    def __init__(
        self,
        directory: Path,
        wait_s: float = WRITE_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._directory = directory
        self._wait_s = wait_s
        self._clock = clock
        self._pause = pause

    def _path(self) -> Path:
        return self._directory / QUEUE_FILE

    def _broken(self, reason: str) -> EnvironmentFailure:
        return EnvironmentFailure(
            f"the mandate queue {self._path()} is {reason}",
            "fix the file, or delete it to empty the queue",
        )

    def _entry(self, item: object) -> QueueEntry:
        if not isinstance(item, dict):
            raise self._broken("not a list of mandates")
        entry_id, added_at, args = item.get("id"), item.get("added_at"), item.get("args")
        if (
            not isinstance(entry_id, str)
            or not isinstance(added_at, str)
            or not isinstance(args, list)
            or not all(isinstance(arg, str) for arg in args)
        ):
            raise self._broken("missing an id, a time or the mandate options")
        return QueueEntry(entry_id, added_at, tuple(str(arg) for arg in args))

    def _read(self) -> tuple[int, tuple[QueueEntry, ...]]:
        path = self._path()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return FIRST_ID, ()
        except OSError as error:
            raise EnvironmentFailure(
                f"cannot read the mandate queue {path}: {error}", "check the file's permissions"
            ) from error
        try:
            data = json.loads(text)
        except ValueError as error:
            raise self._broken(f"not valid JSON ({error})") from error
        if not isinstance(data, dict):
            raise self._broken("not a JSON object")
        version, next_id, items = data.get("version"), data.get("next"), data.get("items")
        if version != QUEUE_VERSION:
            raise self._broken(f"version {version!r}; this cuanta reads version {QUEUE_VERSION}")
        if not isinstance(next_id, int) or isinstance(next_id, bool) or next_id < FIRST_ID:
            raise self._broken("missing its next id")
        if not isinstance(items, list):
            raise self._broken("missing its items")
        return next_id, tuple(self._entry(item) for item in items)

    def _write(self, next_id: int, entries: Sequence[QueueEntry]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": QUEUE_VERSION,
            "next": next_id,
            "items": [
                {"id": entry.id, "added_at": entry.added_at, "args": list(entry.args)}
                for entry in entries
            ],
        }
        staged = self._directory / f"queue.{secrets.token_hex(8)}.tmp"
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as writer:
                writer.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            os.replace(staged, self._path())
        finally:
            staged.unlink(missing_ok=True)

    @contextmanager
    def _writing(self) -> Iterator[None]:
        self._directory.mkdir(parents=True, exist_ok=True)
        lock = self._directory / WRITE_LOCK_FILE
        deadline = self._clock() + self._wait_s
        while True:
            try:
                descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as error:
                if self._clock() >= deadline:
                    raise EnvironmentFailure(
                        f"the mandate queue is locked for writing by {lock}",
                        "wait for the other cuanta queue command; if none is running, delete it",
                    ) from error
                self._pause(WRITE_POLL_S)
                continue
            break
        os.close(descriptor)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    def entries(self) -> tuple[QueueEntry, ...]:
        return self._read()[1]

    def append(self, args: tuple[str, ...], added_at: str) -> QueueEntry:
        with self._writing():
            next_id, entries = self._read()
            entry = QueueEntry(f"q{next_id}", added_at, args)
            self._write(next_id + 1, (*entries, entry))
        return entry

    def remove(self, ids: Collection[str]) -> tuple[str, ...]:
        with self._writing():
            next_id, entries = self._read()
            removed = tuple(entry.id for entry in entries if entry.id in ids)
            if removed:
                self._write(next_id, tuple(entry for entry in entries if entry.id not in ids))
        return removed

    def claim(self) -> bool:
        self._directory.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self._directory / LOCK_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
            writer.write(f"{os.getpid()}\n")
        return True

    def release(self) -> None:
        (self._directory / LOCK_FILE).unlink(missing_ok=True)

    def lock_file(self) -> str:
        return str(self._directory / LOCK_FILE)
