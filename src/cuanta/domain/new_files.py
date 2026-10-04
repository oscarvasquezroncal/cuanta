from __future__ import annotations

import difflib
from dataclasses import dataclass
from enum import StrEnum

NEW_MARKER = ".new"
SUGGESTED_DIR = ".cuanta/forge-suggested"
CLAUDE_DIR = ".claude"
MIRRORED_CLAUDE = "claude"


class Change(StrEnum):
    SAME = "same"
    CHANGED = "changed"
    REMOVED = "removed"
    ADDED = "added"


@dataclass(frozen=True, slots=True)
class DiffRow:
    change: Change
    left: str
    right: str
    left_number: int | None
    right_number: int | None


@dataclass(frozen=True, slots=True)
class Suggestion:
    path: str
    original: str
    added: int
    removed: int


def same_text(left: str, right: str) -> bool:
    return left.replace("\r\n", "\n") == right.replace("\r\n", "\n")


def suggested_path(original: str) -> str:
    head, slash, rest = original.partition("/")
    if head == CLAUDE_DIR and slash:
        return f"{SUGGESTED_DIR}/{MIRRORED_CLAUDE}/{rest}"
    return f"{SUGGESTED_DIR}/{original}"


def is_suggested(path: str) -> bool:
    return path.startswith(f"{SUGGESTED_DIR}/")


def sibling_original(path: str) -> str | None:
    folder, _, name = path.rpartition("/")
    prefix = f"{folder}/" if folder else ""
    if name.endswith(NEW_MARKER) and len(name) > len(NEW_MARKER):
        return prefix + name[: -len(NEW_MARKER)]
    stem, dot, suffix = name.rpartition(".")
    if dot and stem.endswith(NEW_MARKER) and len(stem) > len(NEW_MARKER):
        return f"{prefix}{stem[: -len(NEW_MARKER)]}.{suffix}"
    return None


def _mirrored(path: str) -> str:
    rest = path.removeprefix(f"{SUGGESTED_DIR}/")
    head, slash, tail = rest.partition("/")
    original = f"{CLAUDE_DIR}/{tail}" if head == MIRRORED_CLAUDE and slash else rest
    return sibling_original(original) or original


def original_of(new_path: str) -> str:
    if is_suggested(new_path):
        return _mirrored(new_path)
    original = sibling_original(new_path)
    if original is None:
        raise ValueError(f"{new_path} is not a Forge suggestion or a .new file")
    return original


def line_delta(mine: str, new: str) -> tuple[int, int]:
    matcher = difflib.SequenceMatcher(a=mine.splitlines(), b=new.splitlines(), autojunk=False)
    added = removed = 0
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag != "equal":
            removed += a1 - a0
            added += b1 - b0
    return added, removed


def side_by_side(mine: str, new: str) -> list[DiffRow]:
    left = mine.splitlines()
    right = new.splitlines()
    rows: list[DiffRow] = []
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            rows.extend(
                DiffRow(Change.SAME, left[a0 + i], right[b0 + i], a0 + i + 1, b0 + i + 1)
                for i in range(a1 - a0)
            )
            continue
        span = max(a1 - a0, b1 - b0)
        for i in range(span):
            has_left = a0 + i < a1
            has_right = b0 + i < b1
            if has_left and has_right:
                change = Change.CHANGED
            elif has_left:
                change = Change.REMOVED
            else:
                change = Change.ADDED
            rows.append(
                DiffRow(
                    change,
                    left[a0 + i] if has_left else "",
                    right[b0 + i] if has_right else "",
                    a0 + i + 1 if has_left else None,
                    b0 + i + 1 if has_right else None,
                )
            )
    return rows
