from __future__ import annotations

from textual.content import Content

LABEL_WIDTH = 12


def labeled(pairs: list[tuple[str, str]], width: int = LABEL_WIDTH) -> Content:
    width = max([width, *(len(label) + 1 for label, _ in pairs)])
    lines = [Content.assemble((label.ljust(width), "$text-muted"), value) for label, value in pairs]
    return Content("\n").join(lines)
