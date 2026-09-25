from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from cuanta.domain.messages import Message, msg

TEST_NAME = re.compile(r"(^test_.+\.py$)|(_test\.py$)|(\.(test|spec)\.[cm]?[jt]sx?$)|(_test\.go$)")
TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})
PATH_ARGS_RUNNERS = frozenset({"pytest", "jest", "vitest"})
GO_ALL = "./..."
NO_CACHE = "no:cacheprovider"
LAST_FAILED_FIRST = "--ff"
AFFECTED_CAP = 200
BASELINE_RUN = "gateway-baseline"
BASELINE_PHASE = "tests"


@dataclass(frozen=True, slots=True)
class Selection:
    changed: tuple[str, ...]
    tests: tuple[str, ...]
    arguments: tuple[str, ...]
    full: bool
    reason: Message


def normalized(path: str) -> str:
    cleaned = path.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def is_test_file(path: str) -> bool:
    posix = PurePosixPath(normalized(path))
    if TEST_NAME.search(posix.name):
        return True
    return posix.suffix == ".rs" and bool(TEST_DIRS & set(posix.parts[:-1]))


def stem_of(path: str) -> str:
    posix = PurePosixPath(normalized(path))
    name = posix.name
    if name in {"__init__.py", "index.ts", "index.js", "mod.rs"} and posix.parent.name:
        return posix.parent.name
    for suffix in (".test", ".spec"):
        name = name.replace(suffix + ".", ".")
    stem = name.split(".", 1)[0]
    return stem.removeprefix("test_").removesuffix("_test")


def changed_files(baseline: Mapping[str, str], current: Mapping[str, str]) -> tuple[str, ...]:
    paths = set(baseline) | set(current)
    changed = (path for path in paths if baseline.get(path) != current.get(path))
    return tuple(sorted(normalized(path) for path in changed if path in current))


def related_tests(
    changed: Sequence[str],
    files: Iterable[str],
    neighbours: Mapping[str, frozenset[str]],
) -> tuple[str, ...]:
    tests = sorted({normalized(path) for path in files if is_test_file(path)})
    picked: set[str] = set()
    for path in changed:
        if is_test_file(path):
            picked.add(path)
            continue
        stem = stem_of(path)
        if stem:
            picked.update(test for test in tests if stem_of(test) == stem)
        picked.update(
            normalized(other) for other in neighbours.get(path, frozenset()) if is_test_file(other)
        )
    return tuple(sorted(test for test in picked if test in tests))


def runner_arguments(runner: str, tests: Sequence[str], changed: Sequence[str]) -> tuple[str, ...]:
    if runner == "pytest":
        return (*tests, LAST_FAILED_FIRST)
    if runner in PATH_ARGS_RUNNERS:
        return tuple(tests)
    if runner == "go":
        parents = {str(PurePosixPath(path).parent) for path in (*tests, *changed)}
        return tuple(sorted("." if parent == "." else f"./{parent}" for parent in parents))
    return ()


def narrowed(base: Sequence[str], runner: str, arguments: Sequence[str]) -> list[str]:
    command = list(base)
    extra = list(arguments)
    if runner == "go" and extra:
        command = [part for part in command if part != GO_ALL]
    if runner == "pytest" and NO_CACHE in command:
        extra = [part for part in extra if part != LAST_FAILED_FIRST]
    return [*command, *extra]


def select(
    runner: str,
    changed: Sequence[str],
    files: Iterable[str],
    neighbours: Mapping[str, frozenset[str]],
    has_baseline: bool,
) -> Selection:
    def full(reason: Message, tests: tuple[str, ...] = ()) -> Selection:
        return Selection(tuple(changed), tests, (), True, reason)

    if not has_baseline:
        return full(msg("affected.no_baseline"))
    if runner not in PATH_ARGS_RUNNERS | {"go"}:
        return full(msg("affected.unsupported", runner=runner))
    if not changed:
        return full(msg("affected.nothing_changed"))
    tests = related_tests(changed, files, neighbours)
    if not tests and runner != "go":
        return full(msg("affected.no_related", count=len(changed)))
    if len(tests) > AFFECTED_CAP:
        return full(msg("affected.too_many", count=len(tests)), tests)
    arguments = runner_arguments(runner, tests, changed)
    reason = msg("affected.selected", tests=len(tests), changed=len(changed))
    return Selection(tuple(changed), tests, arguments, False, reason)


def file_neighbours(links: Iterable[tuple[str, str]]) -> dict[str, frozenset[str]]:
    graph: dict[str, set[str]] = {}
    for left, right in links:
        a, b = normalized(left), normalized(right)
        if not a or not b or a == b:
            continue
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    return {path: frozenset(others) for path, others in graph.items()}


TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def blast_radius(
    text: str, files: Iterable[str], neighbours: Mapping[str, frozenset[str]]
) -> tuple[str, ...]:
    terms = {term.lower() for term in TERM.findall(text)}
    if not terms:
        return ()
    touched = {
        normalized(path)
        for path in files
        if terms & {part.lower() for part in re.split(r"[/._-]", normalized(path)) if part}
    }
    reach = set(touched)
    for path in touched:
        reach.update(normalized(other) for other in neighbours.get(path, frozenset()))
    return tuple(sorted(reach))
