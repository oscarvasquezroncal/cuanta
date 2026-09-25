from __future__ import annotations

from enum import StrEnum

from cuanta.domain.progress import Status

TAGLINE = "every token, accounted for"
MASCOT_NAME = "Michi"


class Mood(StrEnum):
    WATCHING = "watching"
    HAPPY = "happy"
    SLEEPY = "sleepy"
    ALARMED = "alarmed"


UNICODE_GLYPHS: dict[Status, str] = {
    Status.OK: "✓",
    Status.FAIL: "✗",
    Status.WARN: "!",
    Status.RESUME: "↻",
    Status.INFO: "·",
    Status.SKIP: "-",
}

ASCII_GLYPHS: dict[Status, str] = {
    Status.OK: "+",
    Status.FAIL: "x",
    Status.WARN: "!",
    Status.RESUME: "~",
    Status.INFO: "-",
    Status.SKIP: "-",
}


def glyph(status: Status, unicode: bool) -> str:
    table = UNICODE_GLYPHS if unicode else ASCII_GLYPHS
    return table[status]


def separator(unicode: bool) -> str:
    return "·" if unicode else "-"


def verdict_word(passed: bool) -> str:
    return "purr" if passed else "hiss"


def hairball_phrase(failed: int, clusters: int) -> str:
    noun = "hairball" if clusters == 1 else "hairballs"
    return f"{failed:,} failed in {clusters:,} {noun}"
