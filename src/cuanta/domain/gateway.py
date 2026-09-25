from __future__ import annotations

import shlex
from dataclasses import dataclass


def split_command(command: str, windows: bool) -> tuple[str, ...]:
    if not windows:
        return tuple(shlex.split(command, posix=True))
    tokens = shlex.split(command, posix=False)
    return tuple(
        token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token
        for token in tokens
    )


STACK_RUNNERS = {
    "pytest": "pytest",
    "jest": "jest",
    "vitest": "vitest",
    "go test": "go",
    "cargo test": "cargo",
}


@dataclass(frozen=True, slots=True)
class RunnerChoice:
    name: str
    command: str


def choose_runner(
    stack_runner: str,
    stack_command: str,
    config_runner: str,
    config_command: str,
) -> RunnerChoice | None:
    name = config_runner or STACK_RUNNERS.get(stack_runner, "")
    command = config_command or stack_command
    if name:
        return RunnerChoice(name, command)
    if command:
        return RunnerChoice("generic", command)
    return None
