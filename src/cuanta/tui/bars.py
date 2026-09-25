from __future__ import annotations

from collections.abc import Sequence

from textual.content import Content

from cuanta.tui.fmt import compact

FULL = "█"
PARTS = ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")
LABEL_WIDTH = 26
BAR_WIDTH = 30
VALUE_WIDTH = 7
SHARE_WIDTH = 6


def bar(fraction: float, width: int = BAR_WIDTH) -> str:
    clamped = max(0.0, min(1.0, fraction))
    eighths = round(clamped * width * 8)
    whole, part = divmod(eighths, 8)
    return FULL * whole + PARTS[part]


def fit(label: str, width: int = LABEL_WIDTH) -> str:
    if len(label) <= width:
        return label.ljust(width)
    return "…" + label[-(width - 1) :]


def bar_lines(
    rows: Sequence[tuple[str, int, float]],
    empty: str,
    label_width: int = LABEL_WIDTH,
    bar_width: int = BAR_WIDTH,
) -> Content:
    if not rows:
        return Content.styled(empty, "$text-muted")
    peak = max(tokens for _, tokens, _ in rows) or 1
    lines = [
        Content.assemble(
            fit(label, label_width),
            " ",
            (bar(tokens / peak, bar_width).ljust(bar_width), "$primary"),
            " ",
            compact(tokens).rjust(VALUE_WIDTH),
            (f"{share:.0%}".rjust(SHARE_WIDTH), "$text-muted"),
        )
        for label, tokens, share in rows
    ]
    return Content("\n").join(lines)
