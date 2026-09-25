from __future__ import annotations

from rich.style import Style
from rich.text import Text

from cuanta.cli.theme import ThemeName
from cuanta.domain.michi import cells
from cuanta.domain.voice import Mood

DARK_EYE = "#1C1B26"
LIGHT_EYE = "#2A2733"

_EARS = " /\\_/\\"

_FACES: dict[Mood, tuple[str, str]] = {
    Mood.WATCHING: ("( o.o )", " > ^ <"),
    Mood.HAPPY: ("( ^.^ )", " > ^ <"),
    Mood.SLEEPY: ("( -.- ) z", " > ^ <"),
    Mood.ALARMED: ("( O.O )", " > ! <"),
}


def michi(mood: Mood) -> tuple[str, str, str]:
    face, paws = _FACES[mood]
    return _EARS, face, paws


def pixel_michi(mood: Mood, theme: ThemeName) -> Text:
    eye = LIGHT_EYE if theme is ThemeName.LIGHT else DARK_EYE
    text = Text(no_wrap=True)
    rows = cells(mood, eye)
    for index, row in enumerate(rows):
        if index:
            text.append("\n")
        for cell in row:
            text.append(cell.char, Style(color=cell.fg, bgcolor=cell.bg))
    return text
