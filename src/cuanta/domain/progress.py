from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.messages import Message, english


class Status(StrEnum):
    OK = "ok"
    FAIL = "fail"
    WARN = "warn"
    RESUME = "resume"
    INFO = "info"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class StepStarted:
    key: str
    label: str
    message: Message | None = None


@dataclass(frozen=True, slots=True)
class StepFinished:
    key: str
    status: Status
    detail: str = ""
    message: Message | None = None


@dataclass(frozen=True, slots=True)
class Note:
    status: Status
    text: str
    message: Message | None = None


@dataclass(frozen=True, slots=True)
class Metric:
    key: str
    label: str
    value: str


ProgressEvent = StepStarted | StepFinished | Note | Metric


def started(key: str, message: Message) -> StepStarted:
    return StepStarted(key, english(message), message)


def finished(key: str, status: Status, message: Message) -> StepFinished:
    return StepFinished(key, status, english(message), message)


def note(status: Status, message: Message) -> Note:
    return Note(status, english(message), message)
