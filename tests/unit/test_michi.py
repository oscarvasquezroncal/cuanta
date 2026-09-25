from __future__ import annotations

import pytest

from cuanta.domain.michi import (
    EYE,
    HEIGHT,
    LID_LINE,
    PIXELS,
    SNORE,
    WIDTH,
    cells,
    pixel_map,
)
from cuanta.domain.voice import Mood

EYE_ZONE = frozenset((y, x) for y in (5, 6) for x in (2, 3, 4, 8, 9, 10))


def _diff(first: tuple[str, ...], second: tuple[str, ...]) -> set[tuple[int, int]]:
    return {
        (y, x)
        for y, (left, right) in enumerate(zip(first, second, strict=True))
        for x, (a, b) in enumerate(zip(left, right, strict=True))
        if a != b
    }


def test_map_is_thirteen_by_eleven() -> None:
    assert len(PIXELS) == HEIGHT
    assert all(len(row) == WIDTH for row in PIXELS)


def test_watching_matches_the_source_map() -> None:
    assert pixel_map(Mood.WATCHING) == PIXELS


@pytest.mark.parametrize("mood", list(Mood))
def test_moods_change_only_eye_pixels(mood: Mood) -> None:
    assert _diff(pixel_map(Mood.WATCHING), pixel_map(mood)) <= EYE_ZONE


def test_mood_eye_shapes() -> None:
    def eyes(mood: Mood, blink: bool = False) -> set[tuple[int, int]]:
        rows = pixel_map(mood, blink)
        return {(y, x) for y, row in enumerate(rows) for x, code in enumerate(row) if code == EYE}

    assert eyes(Mood.WATCHING) == {(5, 3), (6, 3), (5, 9), (6, 9)}
    assert eyes(Mood.HAPPY) == {(5, 3), (5, 9)}
    assert len(eyes(Mood.ALARMED)) == 8
    assert eyes(Mood.SLEEPY) == set(LID_LINE)
    assert eyes(Mood.WATCHING, blink=True) == set(LID_LINE)


@pytest.mark.parametrize(("scale", "rows", "columns"), [(1, 6, 15), (2, 11, 28)])
def test_half_block_sizes(scale: int, rows: int, columns: int) -> None:
    grid = cells(Mood.WATCHING, "#000000", scale)
    assert len(grid) == rows
    assert {len(row) for row in grid} == {columns}


def test_half_blocks_carry_top_and_bottom_colors() -> None:
    grid = cells(Mood.WATCHING, "#010203")
    assert grid[0][1].char == "▀"
    assert grid[0][1].fg == "#F4A87C"
    assert grid[0][0].char == " "
    eye_cell = grid[3][3]
    assert (eye_cell.fg, eye_cell.bg) == ("#010203", "#F6E3D4")


def test_only_sleepy_snores() -> None:
    assert cells(Mood.SLEEPY, "#000000")[0][-1].char == SNORE
    assert cells(Mood.WATCHING, "#000000")[0][-1].char == " "
    assert cells(Mood.SLEEPY, "#000000", blink=True)[0][-1].char == " "
