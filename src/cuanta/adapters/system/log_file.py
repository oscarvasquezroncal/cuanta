from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from cuanta.domain.redaction import redact_for_storage

LOG_LIMIT_BYTES = 1_048_576
ROTATED_SUFFIX = ".1"


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class LogFile:
    def __init__(
        self, path: Path | None, echo: bool = False, limit_bytes: int = LOG_LIMIT_BYTES
    ) -> None:
        self._path = path
        self._echo = echo
        self._limit = limit_bytes
        self._lock = threading.Lock()

    @property
    def path(self) -> Path | None:
        return self._path

    def write(self, title: str, details: str = "") -> None:
        body = details.rstrip("\n")
        entry = f"{_stamp()} {title}\n{body}\n" if body else f"{_stamp()} {title}\n"
        text = redact_for_storage(entry)
        with self._lock:
            if self._path is not None:
                self._append(self._path, text)
                daily = self._path.parent / f"{datetime.now().date().isoformat()}.log"
                if "Traceback (most recent call last):" in body and daily != self._path:
                    self._append(daily, text)
            if self._echo:
                self._show(text)

    def _append(self, path: Path, text: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate(path)
            with path.open("a", encoding="utf-8", errors="replace", newline="\n") as stream:
                stream.write(text)
        except (OSError, UnicodeError):
            return

    def _rotate(self, path: Path) -> None:
        try:
            if path.stat().st_size >= self._limit:
                path.replace(path.with_name(f"{path.name}{ROTATED_SUFFIX}"))
        except OSError:
            return

    def _show(self, text: str) -> None:
        stream = sys.stderr
        if stream is None:
            return
        try:
            stream.write(text)
            stream.flush()
        except (OSError, UnicodeError, ValueError):
            return
