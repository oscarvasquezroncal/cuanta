from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget

from cuanta.domain.michi import HEIGHT, WIDTH, cells
from cuanta.domain.voice import Mood

BLINK_EVERY_S = 6.0
BLINK_FOR_S = 0.15
SNORE_COLUMNS = 2
DEFAULT_EYE = "#1C1B26"


def michi_text(mood: Mood, eye: str, scale: int = 1, blink: bool = False) -> Text:
    text = Text(no_wrap=True, overflow="crop")
    rows = cells(mood, eye, scale, blink)
    for index, row in enumerate(rows):
        for cell in row:
            text.append(cell.char, Style(color=cell.fg, bgcolor=cell.bg))
        if index < len(rows) - 1:
            text.append("\n")
    return text


class Michi(Widget):
    DEFAULT_CSS = """
    Michi {
        width: auto;
        height: auto;
        background: transparent;
    }
    """

    mood: reactive[Mood] = reactive(Mood.WATCHING)
    blinking: reactive[bool] = reactive(False)

    def __init__(
        self,
        mood: Mood = Mood.WATCHING,
        scale: int = 1,
        motion: bool = True,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.scale = scale
        self.motion = motion
        self._timer: Timer | None = None
        self.set_reactive(Michi.mood, mood)

    def on_mount(self) -> None:
        if self.motion:
            self._timer = self.set_interval(BLINK_EVERY_S, self.blink)

    def blink(self) -> None:
        self.blinking = True
        self.set_timer(BLINK_FOR_S, self._open)

    def _open(self) -> None:
        self.blinking = False

    def get_content_width(self, container: object, viewport: object) -> int:
        return WIDTH * self.scale + SNORE_COLUMNS

    def get_content_height(self, container: object, viewport: object, width: int) -> int:
        return (HEIGHT * self.scale + 1) // 2

    def render(self) -> Text:
        theme = self.app.current_theme
        color = (theme.background if theme.dark else theme.foreground) or ""
        eye = color if color.startswith("#") else DEFAULT_EYE
        return michi_text(self.mood, eye, self.scale, self.blinking)
