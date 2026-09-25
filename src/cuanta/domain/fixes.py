from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import StrEnum


class FixKind(StrEnum):
    OPEN = "open"
    RUN = "run"
    COPY = "copy"


class FixAction(StrEnum):
    INIT = "init"
    TESTS = "tests"
    TELEMETRY_ON = "telemetry_on"
    LISTENER_START = "listener_start"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Fix:
    kind: FixKind
    action: FixAction
    command: str
    argument: str = ""


def _words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def classify(command: str) -> Fix:
    words = _words(command.strip())
    if len(words) < 2 or words[0] != "cuanta":
        return Fix(FixKind.COPY, FixAction.NONE, command)
    verb = words[1]
    if verb in {"init", "refresh"}:
        return Fix(FixKind.OPEN, FixAction.INIT, command)
    if verb == "test":
        return Fix(FixKind.RUN, FixAction.TESTS, command)
    if verb == "telemetry" and words[2:3] == ["on"]:
        engine = "all"
        if "--engine" in words:
            position = words.index("--engine") + 1
            engine = words[position] if position < len(words) else "all"
        return Fix(FixKind.RUN, FixAction.TELEMETRY_ON, command, engine)
    if verb == "listen" and "--background" in words:
        return Fix(FixKind.RUN, FixAction.LISTENER_START, command)
    return Fix(FixKind.COPY, FixAction.NONE, command)
