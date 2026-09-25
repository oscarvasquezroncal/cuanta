from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import TextIO

from cuanta.cli.document import Document
from cuanta.domain.errors import CuantaError
from cuanta.domain.progress import ProgressEvent


class JsonPresenter:
    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        self._emitted = False

    def publish(self, event: ProgressEvent) -> None:
        record = {"event": type(event).__name__, **asdict(event)}
        self._err.write(json.dumps(record, default=str) + "\n")

    def render(self, document: Document) -> None:
        self._emit(document.payload)

    def fail(self, error: CuantaError) -> None:
        payload = {
            "error": {
                "kind": type(error).__name__,
                "message": error.message,
                "hint": error.hint,
                "exit_code": int(error.exit_code),
            }
        }
        self._emit(payload)

    def close(self) -> None:
        self._out.flush()

    def _emit(self, payload: object) -> None:
        if self._emitted:
            self._err.write(json.dumps(payload, default=str) + "\n")
            return
        self._emitted = True
        self._out.write(json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n")
