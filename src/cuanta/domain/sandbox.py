from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

SKIPPED_DIRS = frozenset(
    {
        ".git",
        ".next",
        ".turbo",
        ".nuxt",
        ".svelte-kit",
        ".parcel-cache",
        ".angular",
        ".gradle",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        ".cuanta-sandbox",
    }
)
LINKED_DIRS = frozenset({"node_modules"})
DEPENDENCY_CACHES = frozenset({".cache", ".vite", ".vite-temp", ".astro"})
STATE_DIR = ".cuanta"
STATE_KEPT = frozenset({"config.toml"})
STATE_SETTINGS = "config.toml"
GUARDED_TRIAL_FILES = ("trial.json", "commit.txt")
UNGUARDED_SETTINGS = frozenset({"ui", "terminal"})
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".adoc")
UNSAFE_CHARACTERS = frozenset(':*?"<>|')
SHORT_NAME = re.compile(r"^[^~.]{1,6}~\d+(\.[^.]{0,3})?$")
DOC_DIRS = ("docs/", "doc/")
TRIALS_DIR = ".cuanta/trials"
SANDBOX_MODE = "sandbox"
STATE_ROOT_ENV = "CUANTA_STATE_ROOT"
GIT_CEILING_ENV = "GIT_CEILING_DIRECTORIES"
PYTHON_PATH_ENV = "PYTHONPATH"
NODE_MODULES_DENIED = ("Edit(node_modules/**)", "Edit(**/node_modules/**)")
NODE_MODULES_NOTE = (
    "You are working in an isolated copy of the project. node_modules is shared with the "
    "original project through hard links: never edit, delete or install anything under "
    "node_modules."
)


@dataclass(frozen=True, slots=True)
class SandboxLaunch:
    env: tuple[tuple[str, str], ...] = ()
    denied: tuple[str, ...] = ()
    note: str = ""


def sandbox_launch(origin: str, slot: str, linked: bool, python_path: str = "") -> SandboxLaunch:
    extra = ((PYTHON_PATH_ENV, python_path),) if python_path else ()
    return SandboxLaunch(
        env=((STATE_ROOT_ENV, origin), (GIT_CEILING_ENV, slot), *extra),
        denied=NODE_MODULES_DENIED if linked else (),
        note=NODE_MODULES_NOTE if linked else "",
    )


class CopyAction(StrEnum):
    COPY = "copy"
    LINK = "link"
    SKIP = "skip"


def _parts(relative: str) -> list[str]:
    return relative.casefold().split("/")


def copy_action(relative: str, is_dir: bool) -> CopyAction:
    parts = _parts(relative)
    if any(part in SKIPPED_DIRS for part in parts):
        return CopyAction.SKIP
    if parts[0] == STATE_DIR and len(parts) > 1:
        return CopyAction.COPY if len(parts) == 2 and parts[1] in STATE_KEPT else CopyAction.SKIP
    if is_dir and parts[-1] in LINKED_DIRS:
        return CopyAction.LINK
    return CopyAction.COPY


def dependency_cache(relative: str) -> bool:
    parts = _parts(relative)
    return len(parts) > 1 and parts[-1] in DEPENDENCY_CACHES and parts[-2] in LINKED_DIRS


def tracked(relative: str) -> bool:
    parts = _parts(relative)
    if parts[0] == STATE_DIR:
        return False
    return not any(part in SKIPPED_DIRS or part in LINKED_DIRS for part in parts)


class ChangeKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    kind: ChangeKind
    before: str | None
    after: str | None


def diff_manifests(base: Mapping[str, str], end: Mapping[str, str]) -> tuple[FileChange, ...]:
    changes: list[FileChange] = []
    for path in sorted(set(base) | set(end)):
        before, after = base.get(path), end.get(path)
        if before == after:
            continue
        if before is None:
            kind = ChangeKind.ADDED
        elif after is None:
            kind = ChangeKind.DELETED
        else:
            kind = ChangeKind.MODIFIED
        changes.append(FileChange(path, kind, before, after))
    return tuple(changes)


def drifted(changes: Iterable[FileChange], current: Mapping[str, str | None]) -> tuple[str, ...]:
    listed = tuple(changes)
    renamed = {
        change.path.casefold(): change.before
        for change in listed
        if change.kind is ChangeKind.DELETED
    }
    moved: list[str] = []
    for change in listed:
        now = current.get(change.path)
        if now == change.before:
            continue
        if change.kind is ChangeKind.ADDED and now == renamed.get(change.path.casefold()):
            continue
        moved.append(change.path)
    return tuple(moved)


def _unsafe_part(part: str) -> bool:
    return (
        part in {"", ".", ".."}
        or SHORT_NAME.match(part) is not None
        or part.endswith((".", " "))
        or any(char in UNSAFE_CHARACTERS or ord(char) < 32 for char in part)
    )


def unsafe_path(relative: str) -> bool:
    if not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        return True
    return any(_unsafe_part(part) for part in relative.split("/")) or not tracked(relative)


def renamed_away(changes: Iterable[FileChange]) -> frozenset[str]:
    listed = tuple(changes)
    added = {change.path.casefold() for change in listed if change.kind is ChangeKind.ADDED}
    return frozenset(
        change.path
        for change in listed
        if change.kind is ChangeKind.DELETED and change.path.casefold() in added
    )


def docs_only(paths: Iterable[str]) -> bool:
    listed = tuple(paths)
    return bool(listed) and all(
        path.lower().endswith(DOC_SUFFIXES) or path.lower().startswith(DOC_DIRS) for path in listed
    )


def trial_folder(run_id: str) -> str:
    return f"{TRIALS_DIR}/{run_id}"


def trial_of(path: str) -> str:
    prefix = f"{TRIALS_DIR}/"
    if not path.startswith(prefix):
        return ""
    return path[len(prefix) :].split("/", 1)[0]
