from __future__ import annotations

from textual.containers import Container
from textual.events import Resize
from textual.widgets import Button

LABEL_PADDING = 6
FALLBACK_CELL = 12


class FlowRow(Container):
    def on_mount(self) -> None:
        self.call_after_refresh(self.reflow)

    def on_resize(self, event: Resize) -> None:
        self.reflow(event.size.width)

    def reflow(self, width: int | None = None) -> None:
        available = width if width is not None else self.size.width
        cells = [
            len(str(child.label)) + LABEL_PADDING if isinstance(child, Button) else FALLBACK_CELL
            for child in self.children
            if child.display
        ]
        if not cells or available <= 0:
            return
        widest = max(cells)
        columns = max(1, min(len(cells), (available + 1) // (widest + 1)))
        if self.styles.grid_size_columns != columns:
            self.styles.grid_size_columns = columns
