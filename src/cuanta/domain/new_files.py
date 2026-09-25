from __future__ import annotations

import difflib
from dataclasses import dataclass
from enum import StrEnum

NEW_MARKER = ".new"


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


def original_of(new_path: str) -> str:
    folder, _, name = new_path.rpartition("/")
    prefix = f"{folder}/" if folder else ""
    if name.endswith(NEW_MARKER):
        return prefix + name[: -len(NEW_MARKER)]
    stem, dot, suffix = name.rpartition(".")
    if dot and stem.endswith(NEW_MARKER):
        return f"{prefix}{stem[: -len(NEW_MARKER)]}.{suffix}"
    raise ValueError(f"{new_path} is not a .new file")


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
