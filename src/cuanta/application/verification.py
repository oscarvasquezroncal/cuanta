from __future__ import annotations

import re
import shlex
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from cuanta.domain.implementation import without_duration
from cuanta.domain.role_handoff import VerifyResult
from cuanta.ports.system import ProcessRunner

ERROR_LIMIT = 6
LINE_LIMIT = 220
SCAN_LIMIT = 400
TOKEN_LIMIT = 260
KEPT_LINES = 400
DEFAULT_TIMEOUT_S = 900.0
STOP_POLL_S = 0.2
STOPPED_ERROR = "stopped before it finished"
TRACEBACK = "Traceback (most recent call last):"
CAUSE_LINES = 3
CAUSE_CHARS = 160
TAIL_LINES = 40
PYTEST_NODE = re.compile(r"(?:FAILED|ERROR)\s+\S+?\.py::\S.*")
LOCATION = re.compile(
    r"(?P<path>[^\s:()]+\.[A-Za-z][A-Za-z0-9]{0,5})[:(](?P<line>\d+)(?:[:,](?P<column>\d+))?"
)
DIRECT_LOCATION = re.compile(
    r"^(?P<path>(?:[A-Za-z]:)?(?!at\s|\d+\s)[\w.~/\\@][^:\r\n<>|\"*?]*\.[A-Za-z][A-Za-z0-9]{0,5})"
    r"[:(](?P<line>\d+)(?:[:,](?P<column>\d+))?\)?(?=:\s|\s+-\s|$)"
)
DRIVE = re.compile(r"[A-Za-z]:[\\/]")
ERROR_WORDS = re.compile(r"\b(error|failed|failure|cannot|exception)\b", re.IGNORECASE)
LINT_LOCATION = re.compile(r"^\s*(\d+):(\d+)\s+(?:error|warning):?\s+(.+)", re.IGNORECASE)
FILE_HEADER = re.compile(r"(?:[A-Za-z]:)?[\w.~/\\@][^:\r\n<>|\"*?]*\.[A-Za-z][A-Za-z0-9]{0,9}")
FAILED_TEST = re.compile(
    r"●\s+(?!Console$)\S.*"
    r"|(?:FAIL|ERROR): [\w.]+ \([\w.]+\).*"
    r"|FAIL\s+\S.*?\s[>\[]\s.+"
    r"|\d+\) \S.*"
)
TEST_FILE = re.compile(r"(?:FAIL|PASS)\s+(.+?)(?:\s+\([^()]*\))?")
FAILURE_REPRINT = "Summary of all failing tests"
PASS_MARKERS = ("✓", "✔", "√")
SUMMARY = re.compile(
    r"=+ .* =+"
    r"|.*\[\s*\d+%\]"
    r"|\W*(?:found\s+)?\d+\s+(?:problems?|errors?|failed|failures?)\b.*"
    r"|\W*failed (?:tests|suites) \d+\W*"
    r"|failed\s+\(.*\)"
    r"|test result:.*"
    r"|(?:tests|test suites|test files|snapshots):?\s+\d.*"
    r"|[❯✓×↓]\s+\S.*?\s\(\d+ tests?(?: \| \d+ \w+)*\)(?:\s.*)?",
    re.IGNORECASE,
)


def never() -> bool:
    return False


def command_argv(command: str, windows: bool) -> list[str]:
    parts = shlex.split(command, posix=not windows)
    return [part.strip('"') for part in parts] if windows else parts


def _relative(path: str, root: str) -> str:
    normalized = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/") + "/"
    if normalized.casefold().startswith(base.casefold()):
        normalized = normalized[len(base) :]
    return str(PurePosixPath(normalized)).removeprefix("./")


def _position(match: re.Match[str]) -> str:
    return f"{match['line']}:{match['column']}" if match["column"] else match["line"]


def _location(token: str) -> tuple[str, str, int] | None:
    if len(token) > TOKEN_LIMIT:
        return None
    drive = DRIVE.match(token)
    start = drive.end() if drive else 0
    match = LOCATION.match(token, start)
    if match is None:
        return None
    return token[: match.end("path")], _position(match), match.end()


def _entry(raw: str, root: str, maximum_chars: int | None) -> tuple[str, bool] | None:
    scanned = raw[:SCAN_LIMIT] if maximum_chars is not None else raw
    direct = DIRECT_LOCATION.match(scanned.strip())
    if direct is not None:
        rest = " ".join(scanned.strip()[direct.end() :].split()).lstrip(" :-),0123456789")
        entry = f"{_relative(direct['path'], root)}:{_position(direct)} {rest}"
        return entry.strip()[:maximum_chars], True
    line = " ".join(scanned.split())
    if not line:
        return None
    tokens = line.split(" ")
    for index, token in enumerate(tokens):
        found = _location(token)
        if found is not None:
            path, number, end = found
            rest = " ".join((token[end:], *tokens[index + 1 :])).lstrip(" :-),0123456789")
            return f"{_relative(path, root)}:{number} {rest}".strip()[:maximum_chars], True
    if ERROR_WORDS.search(line):
        return line[:maximum_chars], False
    return None


def first_errors(
    output: str, root: str, limit: int = ERROR_LIMIT, maximum_chars: int | None = LINE_LIMIT
) -> tuple[str, ...]:
    located: list[str] = []
    general: list[str] = []
    for raw in output.splitlines():
        entry = _entry(raw, root, maximum_chars)
        if entry is not None:
            (located if entry[1] else general).append(entry[0])
    chosen = list(dict.fromkeys(located))[:limit]
    if len(chosen) < limit:
        chosen.extend(list(dict.fromkeys(general))[: limit - len(chosen)])
    return tuple(chosen)


def crash_line(lines: Sequence[str]) -> str:
    starts = [index for index, line in enumerate(lines) if line.strip() == TRACEBACK]
    if not starts:
        return ""
    after = [line for line in lines[starts[-1] + 1 :] if line.strip()]
    found = next((line for line in after if not line[:1].isspace()), after[-1] if after else "")
    return " ".join((found or TRACEBACK).split())[:LINE_LIMIT]


def last_lines(lines: Sequence[str], count: int = CAUSE_LINES) -> tuple[str, ...]:
    filled = [" ".join(line.split()) for line in lines if line.strip()]
    return tuple(line[:CAUSE_CHARS] for line in filled[-count:])


class Diagnostics:
    def __init__(self, root: str) -> None:
        self._root = root
        self._lint_path = ""
        self._test_file = ""
        self._reprint = False
        self._titles: dict[str, int] = {}
        self.errors: dict[str, int] = {}
        self.summaries: dict[str, None] = {}
        self.located = False

    def collect(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            self._lint_path = ""
            return
        if FILE_HEADER.fullmatch(line.rstrip()):
            self._lint_path = _relative(stripped, self._root)
        text = " ".join(stripped.split())
        tested = TEST_FILE.fullmatch(text)
        if tested is not None and " > " not in text and " [ " not in text:
            self._test_file = _relative(tested[1], self._root)
            if text.startswith("PASS"):
                return
        if text == FAILURE_REPRINT:
            self._reprint = True
        match = LINT_LOCATION.match(line)
        if self._lint_path and match is not None:
            message = " ".join(match[3].split())
            self.errors.setdefault(f"{self._lint_path}:{match[1]}:{match[2]} {message}", 1)
            self.located = True
        elif FAILED_TEST.fullmatch(stripped):
            self._failed_test(text)
        elif SUMMARY.fullmatch(stripped):
            if ERROR_WORDS.search(stripped):
                self.summaries[without_duration(text)] = None
        elif not stripped.startswith(PASS_MARKERS):
            self.located = self.located or PYTEST_NODE.fullmatch(text) is not None
            for raw in line.splitlines():
                entry = _entry(raw, self._root, None)
                if entry is not None:
                    self.located = self.located or entry[1]
                    self.errors.setdefault(without_duration(entry[0]), 1)

    def _failed_test(self, text: str) -> None:
        self.located = True
        if self._reprint and self._titles:
            return
        title = f"{self._test_file} {text}" if self._test_file and text.startswith("●") else text
        seen = self._titles.get(title, 0) + 1
        self._titles[title] = seen
        self.errors[title] = max(self.errors.get(title, 0), seen)

    def found(self) -> tuple[str, ...]:
        errors = tuple(error for error, count in self.errors.items() for _ in range(count))
        return errors or tuple(self.summaries)


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

    def run(
        self, commands: Sequence[str], stopped: Callable[[], bool] = never
    ) -> tuple[VerifyResult, ...]:
        results: list[VerifyResult] = []
        for command in commands:
            if stopped():
                break
            try:
                argv = command_argv(command, self._windows)
            except ValueError:
                results.append(VerifyResult(command, None, 0.0, ("could not parse the command",)))
                continue
            if argv:
                results.append(self._one(command, argv, stopped))
        return tuple(results)

    def run_checks(
        self, commands: Sequence[str], stopped: Callable[[], bool] = never
    ) -> tuple[VerifyResult, ...]:
        parallel = tuple(
            command
            for command in commands
            if any(word in command.lower() for word in ("typecheck", "tsc", "lint"))
        )
        serial = tuple(command for command in commands if command not in parallel)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.run, (command,), stopped) for command in parallel]
            results = tuple(result for future in futures for result in future.result())
        return (*results, *self.run(serial, stopped))

    def _one(self, command: str, argv: list[str], stopped: Callable[[], bool]) -> VerifyResult:
        started = self._monotonic()
        try:
            handle = self._runner.stream(argv, cwd=self._cwd, env=self._env)
        except OSError as error:
            return VerifyResult(command, None, 0.0, (str(error)[:LINE_LIMIT],))
        fired = threading.Event()
        halted = threading.Event()
        done = threading.Event()
        closing = threading.Lock()

        def shut() -> None:
            with closing:
                handle.close()

        def expire() -> None:
            fired.set()
            shut()

        def watch() -> None:
            while not done.wait(STOP_POLL_S):
                if stopped():
                    halted.set()
                    shut()
                    return

        timer = threading.Timer(self._timeout, expire)
        timer.daemon = True
        timer.start()
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        kept: deque[str] = deque(maxlen=KEPT_LINES)
        diagnostics = Diagnostics(str(self._cwd))
        try:
            for line in handle.lines():
                kept.append(line)
                diagnostics.collect(line)
            code = handle.wait()
        finally:
            done.set()
            timer.cancel()
            watcher.join()
            shut()
        seconds = round(self._monotonic() - started, 2)
        if halted.is_set():
            return VerifyResult(command, None, seconds, (STOPPED_ERROR,))
        stdout = tuple(kept)
        stderr = handle.stderr_text().splitlines()
        lines = (*stdout, *stderr[-KEPT_LINES:])
        for line in stderr:
            diagnostics.collect(line)
        tail = tuple(lines[-TAIL_LINES:])
        if fired.is_set():
            return VerifyResult(
                command, code, seconds, diagnostics.found(), timed_out=True, tail=tail
            )
        if code == 0:
            return VerifyResult(command, code, seconds)
        errors = diagnostics.found() or tuple(
            without_duration(error) for error in first_errors("\n".join(lines), str(self._cwd))
        )
        crash = crash_line(stderr) or crash_line(stdout)
        return VerifyResult(
            command,
            code,
            seconds,
            errors,
            cause=crash or " | ".join(last_lines(stderr) or last_lines(stdout)),
            broken=bool(crash) and not diagnostics.located,
            tail=tail,
        )
