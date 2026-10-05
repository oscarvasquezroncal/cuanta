from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cuanta.domain.messages import Message, english, msg

SLOW_STEP_S = 2.0
INDEX_STEP = "index"
PLAN_STEP = "plan"
FORECAST_STEP = "forecast"
PREPARE_STEPS = (INDEX_STEP, PLAN_STEP, FORECAST_STEP)


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
    seconds: float | None = None


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


@dataclass(frozen=True, slots=True)
class LiveStatus:
    seconds: float
    tokens: int
    role: str
    tool: str
    file: str
    phase: str = ""
    runner: str = ""


ProgressEvent = StepStarted | StepFinished | Note | Metric | LiveStatus


def started(key: str, message: Message) -> StepStarted:
    return StepStarted(key, english(message), message)


def finished(
    key: str, status: Status, message: Message, seconds: float | None = None
) -> StepFinished:
    return StepFinished(key, status, english(message), message, seconds)


def took(seconds: float, detail: Message | None = None) -> Message:
    shown = f"{seconds:,.0f}"
    if detail is None:
        return msg("progress.took", seconds=shown)
    return msg("progress.done", detail=detail, seconds=shown)


def file_count(files: int) -> Message:
    return msg("progress.files_one") if files == 1 else msg("progress.files", files=f"{files:,}")


def note(status: Status, message: Message) -> Note:
    return Note(status, english(message), message)
