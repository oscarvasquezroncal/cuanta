from __future__ import annotations

from dataclasses import dataclass

from cuanta import __version__
from cuanta.domain.voice import MASCOT_NAME, TAGLINE


@dataclass(frozen=True, slots=True)
class MeowReport:
    name: str
    version: str
    tagline: str
    mascot: str


def meow() -> MeowReport:
    return MeowReport(name="cuanta", version=__version__, tagline=TAGLINE, mascot=MASCOT_NAME)
