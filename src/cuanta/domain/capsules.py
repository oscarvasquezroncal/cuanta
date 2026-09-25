from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

WINDOW_LINES = 40
FAILURE_MARKER = re.compile(
    r"Traceback|FAILED|FAIL\b|Error\b|Exception|panicked|✕|AssertionError|--- FAIL", re.IGNORECASE
)


class Level(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


def capsule_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def capsule_id(digest: str) -> str:
    return f"cap:{digest[:16]}"


@dataclass(frozen=True, slots=True)
class LineRange:
    start: int
    end: int


def parse_range(value: str) -> LineRange:
    left, separator, right = value.partition(":")
    if not separator:
        raise ValueError("expected a:b")
    start = int(left) if left.strip() else 1
    end = int(right) if right.strip() else 0
    if start < 1 or (end and end < start):
        raise ValueError("range must be 1-based and ascending")
    return LineRange(start, end)


def slice_lines(lines: list[str], selection: LineRange) -> list[tuple[int, str]]:
    end = selection.end or len(lines)
    return [(index + 1, lines[index]) for index in range(selection.start - 1, min(end, len(lines)))]


def failure_window(lines: list[str], size: int = WINDOW_LINES) -> list[tuple[int, str]]:
    marker = next((index for index, line in enumerate(lines) if FAILURE_MARKER.search(line)), None)
    start = max(len(lines) - size, 0) if marker is None else max(marker - size // 4, 0)
    end = min(start + size, len(lines))
    return [(index + 1, lines[index]) for index in range(start, end)]
