from __future__ import annotations

from typing import Protocol

from cuanta.cli.document import Document
from cuanta.domain.errors import CuantaError
from cuanta.domain.progress import ProgressEvent, Status

STATUS_STYLE: dict[Status, str] = {
    Status.OK: "ok",
    Status.FAIL: "err",
    Status.WARN: "warn",
    Status.RESUME: "info",
    Status.INFO: "muted",
    Status.SKIP: "muted",
}


class Presenter(Protocol):
    def publish(self, event: ProgressEvent) -> None: ...

    def render(self, document: Document) -> None: ...

    def fail(self, error: CuantaError) -> None: ...

    def close(self) -> None: ...
