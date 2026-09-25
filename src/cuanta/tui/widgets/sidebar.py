from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Label

from cuanta.tui.commands import ICONS, SIDEBAR
from cuanta.tui.i18n import Catalog


class NavItem(Horizontal, can_focus=True):
    BINDINGS = [Binding("enter,space", "choose", show=False)]

    class Chosen(Message):
        def __init__(self, section: str) -> None:
            super().__init__()
            self.section = section

    def __init__(self, section: str, label: str) -> None:
        super().__init__(id=f"nav-{section}", classes="nav-item")
        self.section = section
        self._label = label

    def compose(self) -> ComposeResult:
        yield Label(ICONS[self.section], classes="nav-icon")
        yield Label(self._label, classes="nav-label")

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.action_choose()

    def action_choose(self) -> None:
        self.post_message(self.Chosen(self.section))


class Sidebar(Vertical):
    def __init__(self, catalog: Catalog) -> None:
        super().__init__(id="sidebar")
        self._t = catalog

    def compose(self) -> ComposeResult:
        for section in SIDEBAR:
            yield NavItem(section, self._t(f"nav.{section}"))

    def mark(self, section: str) -> None:
        for item in self.query(NavItem):
            item.set_class(item.section == section, "-active")
