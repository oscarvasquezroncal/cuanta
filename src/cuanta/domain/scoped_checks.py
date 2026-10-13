from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping

from cuanta.domain.affected import is_test_file, normalized, select
from cuanta.domain.change_plan import ChangePlan

DMYPY_STATUS = ".cuanta/tmp/dmypy.json"
PYTEST_SCOPE = ("-x", "-q", "-p", "no:cacheprovider")
PYTEST_PARALLEL = ("-n", "auto")
PATH_RUNNERS = ("pytest", "vitest", "jest")
PARALLEL_FILES = 6
STRONG_TARGET = 0.9
WEAK_SOURCES = 3
TOO_MANY = "affected.too_many"


def _words(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def daemon_command(command: str) -> str | None:
    words = _words(command)
    for index, word in enumerate(words):
        if word == "mypy" and index and words[index - 1] == "-m":
            head = " ".join(words[:index])
            rest = " ".join(words[index + 1 :])
            return _joined(f"{head} mypy.dmypy --status-file {DMYPY_STATUS} run --", rest)
        if word == "mypy":
            head = " ".join(words[:index])
            rest = " ".join(words[index + 1 :])
            prefix = f"{head} dmypy" if head else "dmypy"
            return _joined(f"{prefix} --status-file {DMYPY_STATUS} run --", rest)
    return None


def daemon_control(command: str, action: str) -> str:
    head, _, _ = command.partition(f"--status-file {DMYPY_STATUS}")
    return f"{head}--status-file {DMYPY_STATUS} {action}"


def _joined(head: str, rest: str) -> str:
    return f"{head} {rest}" if rest else head


def runner_of(command: str) -> str:
    words = [word.rsplit("/", 1)[-1].lower() for word in _words(command)]
    for name in PATH_RUNNERS:
        if name in words:
            return name
    if "go" in words and "test" in words:
        return "go"
    return ""


def scoped_test_command(
    command: str, runner: str, tests: tuple[str, ...], parallel: bool, marks: str = ""
) -> str | None:
    if runner not in PATH_RUNNERS:
        return None
    quoted = " ".join(f'"{test}"' if " " in test else test for test in tests)
    flags: tuple[str, ...] = ()
    if runner == "pytest":
        flags = (
            *PYTEST_SCOPE,
            *(PYTEST_PARALLEL if parallel else ()),
            *(("-m", f'"{marks}"') if marks else ()),
        )
    return " ".join(part for part in (command, " ".join(flags), quoted) if part)


def plan_sources(plan: ChangePlan) -> tuple[str, ...]:
    targets = sorted(plan.edit, key=lambda item: (-item.confidence, item.path))
    strong = [item.path for item in targets if item.confidence >= STRONG_TARGET]
    chosen = strong or [item.path for item in targets if not is_test_file(item.path)][:WEAK_SOURCES]
    return tuple(dict.fromkeys(normalized(path) for path in chosen))


def changed_paths(before: Mapping[str, str], after: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(normalized(path) for path, digest in after.items() if before.get(path) != digest)
    )


def affected_tests(
    runner: str,
    changed: Iterable[str],
    files: Iterable[str],
    neighbours: Mapping[str, frozenset[str]],
) -> tuple[tuple[str, ...], bool]:
    paths = tuple(dict.fromkeys(changed))
    if not paths or runner not in PATH_RUNNERS:
        return (), False
    selection = select(runner, paths, files, neighbours, True)
    if selection.full:
        return (), selection.reason.key == TOO_MANY
    return selection.tests, False


def parallel_selection(tests: tuple[str, ...], full: bool) -> bool:
    return full or len(tests) >= PARALLEL_FILES


def check_workers(count: int, cores: int | None) -> int:
    return max(1, min(count, cores or 1))
