from __future__ import annotations

from dataclasses import dataclass

from cuanta.domain.voice import Mood

PIXELS: tuple[str, ...] = (
    ".G.........K.",
    ".GG.......KK.",
    ".GPG.....KPK.",
    ".GGGGCCCKKKK.",
    "GGGCCCCCCCKKK",
    "GGCECCCCCECKK",
    "CCCECCCCCECCC",
    "CCCCCCPCCCCCC",
    ".CCCCPCPCCCC.",
    "..CCCCCCCCC..",
    "....CCCCC....",
)
WIDTH = 13
HEIGHT = 11
EYE = "E"
FUR = "C"
COLORS: dict[str, str] = {
    "G": "#F4A87C",
    "K": "#5A5468",
    "C": "#F6E3D4",
    "P": "#F2A7C3",
}
EYE_ROWS = (5, 6)
UPPER_EYES = ((5, 3), (5, 9))
LOWER_EYES = ((6, 3), (6, 9))
WIDE_EYES = ((5, 4), (6, 4), (5, 8), (6, 8))
LID_LINE = ((6, 2), (6, 3), (6, 4), (6, 8), (6, 9), (6, 10))
SNORE = "z"
TOP = "▀"
BOTTOM = "▄"


@dataclass(frozen=True, slots=True)
class Cell:
    char: str
    fg: str | None = None
    bg: str | None = None


def eyes(mood: Mood, blink: bool = False) -> frozenset[tuple[int, int]]:
    if blink or mood is Mood.SLEEPY:
        return frozenset(LID_LINE)
    if mood is Mood.HAPPY:
        return frozenset(UPPER_EYES)
    if mood is Mood.ALARMED:
        return frozenset((*UPPER_EYES, *LOWER_EYES, *WIDE_EYES))
    return frozenset((*UPPER_EYES, *LOWER_EYES))


def pixel_map(mood: Mood, blink: bool = False) -> tuple[str, ...]:
    open_eyes = eyes(mood, blink)
    rows: list[str] = []
    for y, row in enumerate(PIXELS):
        chars = []
        for x, code in enumerate(row):
            if (y, x) in open_eyes:
                chars.append(EYE)
            elif code == EYE:
                chars.append(FUR)
            else:
                chars.append(code)
        rows.append("".join(chars))
    return tuple(rows)


def _color(code: str, eye: str) -> str | None:
    if code == EYE:
        return eye
    return COLORS.get(code)


def _scaled(rows: tuple[str, ...], scale: int) -> list[str]:
    grown: list[str] = []
    for row in rows:
        wide = "".join(code * scale for code in row)
        grown.extend([wide] * scale)
    if len(grown) % 2:
        grown.append("." * len(grown[0]))
    return grown


def _cell(top: str | None, bottom: str | None) -> Cell:
    if top is None and bottom is None:
        return Cell(" ")
    if top is None:
        return Cell(BOTTOM, bottom)
    if bottom is None:
        return Cell(TOP, top)
    return Cell(TOP, top, bottom)


def cells(
    mood: Mood, eye: str, scale: int = 1, blink: bool = False
) -> tuple[tuple[Cell, ...], ...]:
    grown = _scaled(pixel_map(mood, blink), max(1, scale))
    lines: list[tuple[Cell, ...]] = []
    for index in range(0, len(grown), 2):
        top_row, bottom_row = grown[index], grown[index + 1]
        line = tuple(
            _cell(_color(top, eye), _color(bottom, eye))
            for top, bottom in zip(top_row, bottom_row, strict=True)
        )
        lines.append(line)
    snore = Cell(SNORE if mood is Mood.SLEEPY and not blink else " ")
    return tuple(
        (*line, Cell(" "), snore if row == 0 else Cell(" ")) for row, line in enumerate(lines)
    )
