from __future__ import annotations

import re
import shlex
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath

from cuanta.domain.role_handoff import VerifyResult
from cuanta.ports.system import ProcessRunner

ERROR_LIMIT = 6
LINE_LIMIT = 220
SCAN_LIMIT = 400
TOKEN_LIMIT = 260
KEPT_LINES = 400
DEFAULT_TIMEOUT_S = 900.0
LOCATION = re.compile(r"(?P<path>[^\s:()]+\.[A-Za-z][A-Za-z0-9]{0,5})[:(](?P<line>\d+)")
DRIVE = re.compile(r"[A-Za-z]:[\\/]")
ERROR_WORDS = re.compile(r"\b(error|failed|failure|cannot|exception)\b", re.IGNORECASE)


def command_argv(command: str, windows: bool) -> list[str]:
    parts = shlex.split(command, posix=not windows)
    return [part.strip('"') for part in parts] if windows else parts


def _relative(path: str, root: str) -> str:
    normalized = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/") + "/"
    if normalized.casefold().startswith(base.casefold()):
        normalized = normalized[len(base) :]
    return str(PurePosixPath(normalized)).removeprefix("./")


def _location(token: str) -> tuple[str, str, int] | None:
    if len(token) > TOKEN_LIMIT:
        return None
    drive = DRIVE.match(token)
    start = drive.end() if drive else 0
    match = LOCATION.match(token, start)
    if match is None:
        return None
    return token[: match.end("path")], match["line"], match.end()


def first_errors(output: str, root: str, limit: int = ERROR_LIMIT) -> tuple[str, ...]:
    located: list[str] = []
    general: list[str] = []
    for raw in output.splitlines():
        line = " ".join(raw[:SCAN_LIMIT].split())
        if not line:
            continue
        found = None
        tokens = line.split(" ")
        for index, token in enumerate(tokens):
            found = _location(token)
            if found is not None:
                path, number, end = found
                rest = " ".join((token[end:], *tokens[index + 1 :])).lstrip(" :-),0123456789")
                located.append(f"{_relative(path, root)}:{number} {rest}".strip()[:LINE_LIMIT])
                break
        if found is None and ERROR_WORDS.search(line):
            general.append(line[:LINE_LIMIT])
    chosen = list(dict.fromkeys(located))[:limit]
    if len(chosen) < limit:
        chosen.extend(list(dict.fromkeys(general))[: limit - len(chosen)])
    return tuple(chosen)


class Verifier:
    def __init__(
        self,
        runner: ProcessRunner,
        cwd: Path,
        windows: bool,
        env: Mapping[str, str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner = runner
        self._cwd = cwd
        self._windows = windows
        self._env = env
        self._timeout = timeout_s
        self._monotonic = monotonic

    def run(self, commands: Sequence[str]) -> tuple[VerifyResult, ...]:
        results: list[VerifyResult] = []
        for command in commands:
            try:
                argv = command_argv(command, self._windows)
            except ValueError:
                results.append(VerifyResult(command, None, 0.0, ("could not parse the command",)))
                continue
            if argv:
                results.append(self._one(command, argv))
        return tuple(results)

    def _one(self, command: str, argv: list[str]) -> VerifyResult:
        started = self._monotonic()
        try:
            handle = self._runner.stream(argv, cwd=self._cwd, env=self._env)
        except OSError as error:
            return VerifyResult(command, None, 0.0, (str(error)[:LINE_LIMIT],))
        fired = threading.Event()

        def expire() -> None:
            fired.set()
            handle.close()

        timer = threading.Timer(self._timeout, expire)
        timer.daemon = True
        timer.start()
        kept: deque[str] = deque(maxlen=KEPT_LINES)
        try:
            for line in handle.lines():
                kept.append(line)
            code = handle.wait()
        finally:
            timer.cancel()
            handle.close()
        seconds = round(self._monotonic() - started, 2)
        output = "\n".join((*kept, *handle.stderr_text().splitlines()[-KEPT_LINES:]))
        if fired.is_set():
            return VerifyResult(
                command, code, seconds, first_errors(output, str(self._cwd)), timed_out=True
            )
        errors = () if code == 0 else first_errors(output, str(self._cwd))
        return VerifyResult(command, code, seconds, errors)
