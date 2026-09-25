from __future__ import annotations

from cuanta.domain.progress import Status

UNITS = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k"))
GLYPHS = {
    Status.OK: "✓",
    Status.WARN: "!",
    Status.FAIL: "✗",
    Status.INFO: "i",
    Status.SKIP: "–",
    Status.RESUME: "↺",
}


def compact(value: float) -> str:
    for size, unit in UNITS:
        if abs(value) >= size:
            scaled = value / size
            text = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{int(value):,}"


def grouped(value: int) -> str:
    return f"{value:,}"


def money(value: float | None, unknown: str = "–") -> str:
    if value is None:
        return unknown
    return f"${value:,.2f}"


def glyph(status: Status) -> str:
    return GLYPHS.get(status, "·")


def status_style(status: Status) -> str:
    styles = {
        Status.OK: "$success",
        Status.WARN: "$warning",
        Status.FAIL: "$error",
        Status.INFO: "$secondary",
        Status.RESUME: "$primary",
    }
    return styles.get(status, "$text-muted")
