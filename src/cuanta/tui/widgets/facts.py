from __future__ import annotations

import textwrap

from textual.content import Content
from textual.events import Resize
from textual.widgets import Static

from cuanta.tui.cells import LABEL_WIDTH

MIN_VALUE_WIDTH = 12


def wrapped_facts(pairs: list[tuple[str, str]], label_width: int, total: int) -> Content:
    width = max([label_width, *(len(label) + 1 for label, _ in pairs)])
    room = max(MIN_VALUE_WIDTH, total - width) if total > 0 else 0
    lines: list[Content] = []
    for label, value in pairs:
        pieces = textwrap.wrap(value, room) if room and value else [value]
        for index, piece in enumerate(pieces or [""]):
            head = label.ljust(width) if index == 0 else " " * width
            lines.append(Content.assemble((head, "$text-muted"), piece))
    return Content("\n").join(lines)


class Facts(Static):
    def __init__(self, content: object = "", *, id: str | None = None) -> None:
        super().__init__(content if isinstance(content, str | Content) else "", id=id)
        self.pairs: list[tuple[str, str]] = []
        self.label_width = LABEL_WIDTH

    def show(self, pairs: list[tuple[str, str]], label_width: int = LABEL_WIDTH) -> None:
        self.pairs = pairs
        self.label_width = label_width
        self.update(wrapped_facts(pairs, label_width, self.content_region.width))

    def on_resize(self, event: Resize) -> None:
        if self.pairs:
            self.update(wrapped_facts(self.pairs, self.label_width, event.size.width))
