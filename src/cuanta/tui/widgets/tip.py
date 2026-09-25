from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.content import Content
from textual.message import Message
from textual.widgets import Button, Static

from cuanta.tui.i18n import Catalog


class LegacyTip(Horizontal):
    class Dismissed(Message):
        def __init__(self, forever: bool) -> None:
            super().__init__()
            self.forever = forever

    def __init__(self, catalog: Catalog) -> None:
        super().__init__(id="legacy-tip")
        self._t = catalog

    def compose(self) -> ComposeResult:
        t = self._t
        yield Static(Content.styled(t("app.legacy_tip"), "$warning"), id="legacy-tip-text")
        yield Button(t("app.tip_forever"), id="legacy-tip-forever", compact=True)
        yield Button(t("app.tip_close"), id="legacy-tip-close", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Dismissed(event.button.id == "legacy-tip-forever"))
        self.remove()
