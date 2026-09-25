from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ThemeName(StrEnum):
    DARK = "dark"
    LIGHT = "light"
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class Swatch:
    semantic: str
    token: str
    dark: str
    light: str


PALETTE: tuple[Swatch, ...] = (
    Swatch("brand", "ginger", "#F4A87C", "#C8693A"),
    Swatch("accent", "blush", "#F2A7C3", "#C2567F"),
    Swatch("secondary", "iris", "#B4B9F5", "#5A62C9"),
    Swatch("title", "cream", "#F6E3D4", "#4A3F3A"),
    Swatch("ok", "catnip", "#9FD8A0", "#3E8E4A"),
    Swatch("warn", "amber", "#F0D08A", "#A87A12"),
    Swatch("err", "hiss", "#EF8A94", "#C0404F"),
    Swatch("info", "sky", "#8FD3E8", "#2B83A3"),
    Swatch("muted", "fur", "#9AA0B8", "#6B7087"),
)

DARK_WORDMARK: tuple[str, ...] = ("#F4A87C", "#F3A8A0", "#F2A7C3", "#DDADD4", "#C9B3E4", "#B4B9F5")

WORDMARK = "cuanta"


def resolve_theme(requested: ThemeName, colorfgbg: str | None) -> ThemeName:
    if requested is not ThemeName.AUTO:
        return requested
    if not colorfgbg:
        return ThemeName.DARK
    background = colorfgbg.split(";")[-1].strip()
    if not background.isdigit():
        return ThemeName.DARK
    return ThemeName.LIGHT if int(background) in {7, 15} else ThemeName.DARK


def semantic_colors(theme: ThemeName) -> dict[str, str]:
    light = theme is ThemeName.LIGHT
    return {swatch.semantic: swatch.light if light else swatch.dark for swatch in PALETTE}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def interpolate(stops: tuple[str, ...], count: int) -> tuple[str, ...]:
    if count <= 1:
        return stops[:1]
    points = [_hex_to_rgb(stop) for stop in stops]
    segments = len(points) - 1
    colors: list[str] = []
    for index in range(count):
        position = index * segments / (count - 1)
        left = min(int(position), segments - 1)
        fraction = position - left
        start, end = points[left], points[left + 1]
        mixed = (
            round(start[0] + (end[0] - start[0]) * fraction),
            round(start[1] + (end[1] - start[1]) * fraction),
            round(start[2] + (end[2] - start[2]) * fraction),
        )
        colors.append(_rgb_to_hex(mixed))
    return tuple(colors)


def wordmark_colors(theme: ThemeName) -> tuple[str, ...]:
    if theme is ThemeName.LIGHT:
        colors = semantic_colors(ThemeName.LIGHT)
        stops = (colors["brand"], colors["accent"], colors["secondary"])
        return interpolate(stops, len(WORDMARK))
    return DARK_WORDMARK
