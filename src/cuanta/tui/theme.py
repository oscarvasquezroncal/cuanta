from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from textual.theme import Theme

CALICO_DARK = "calico-dark"
CALICO_LIGHT = "calico-light"
ANSI_DARK = "ansi-dark"
ANSI_LIGHT = "ansi-light"
AUTO = "auto"
ANSI = "ansi"
CHOICES = (CALICO_DARK, CALICO_LIGHT, AUTO, ANSI)
BACKGROUND_SOLID = "solid"
BACKGROUND_TERMINAL = "terminal"
BACKGROUNDS = (BACKGROUND_SOLID, BACKGROUND_TERMINAL)
LIGHT_BACKGROUNDS = frozenset({7, 15})


@dataclass(frozen=True, slots=True)
class Palette:
    background: str
    surface: str
    border: str
    primary: str
    secondary: str
    accent: str
    success: str
    warning: str
    error: str
    text: str
    muted: str


DARK = Palette(
    background="#1C1B26",
    surface="#24222F",
    border="#2E2C3B",
    primary="#F4A87C",
    secondary="#B4B9F5",
    accent="#F2A7C3",
    success="#9FD8A0",
    warning="#F0D08A",
    error="#EF8A94",
    text="#F6E3D4",
    muted="#9AA0B8",
)
LIGHT = Palette(
    background="#F7F7FB",
    surface="#FFFFFF",
    border="#E3E1EA",
    primary="#C8693A",
    secondary="#5A62C9",
    accent="#C2567F",
    success="#3E8E4A",
    warning="#A87A12",
    error="#C0404F",
    text="#2A2733",
    muted="#6B7087",
)


def build(name: str, palette: Palette, dark: bool) -> Theme:
    return Theme(
        name=name,
        primary=palette.primary,
        secondary=palette.secondary,
        accent=palette.accent,
        success=palette.success,
        warning=palette.warning,
        error=palette.error,
        foreground=palette.text,
        background=palette.background,
        surface=palette.surface,
        panel=palette.surface,
        dark=dark,
        variables={
            "border": palette.border,
            "border-blurred": palette.border,
            "text-muted": palette.muted,
            "foreground-muted": palette.muted,
            "footer-background": palette.surface,
            "footer-key-foreground": palette.primary,
            "block-cursor-text-style": "bold",
        },
    )


def calico_themes() -> tuple[Theme, Theme]:
    return build(CALICO_DARK, DARK, True), build(CALICO_LIGHT, LIGHT, False)


def light_terminal(colorfgbg: str) -> bool:
    background = colorfgbg.rsplit(";", maxsplit=1)[-1].strip()
    return background.isdigit() and int(background) in LIGHT_BACKGROUNDS


def resolve(choice: str, legacy: bool, environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    light = light_terminal(env.get("COLORFGBG", ""))
    if choice == CALICO_DARK:
        return CALICO_DARK
    if choice == CALICO_LIGHT:
        return CALICO_LIGHT
    if choice == ANSI or legacy:
        return ANSI_LIGHT if light else ANSI_DARK
    return CALICO_LIGHT if light else CALICO_DARK
