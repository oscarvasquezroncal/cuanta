from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.widgets import Static

from cuanta.tui.commands import CLI_EQUIVALENT
from cuanta.tui.i18n import Catalog


class PendingView(Vertical):
    def __init__(self, section: str, catalog: Catalog) -> None:
        super().__init__(id=section, classes="view pending")
        self.section = section
        self._t = catalog

    def compose(self) -> ComposeResult:
        t = self._t
        name = t(f"nav.{self.section}")
        with Vertical(classes="card"):
            yield Static(t("pending.title", section=name), classes="card-title")
            yield Static(Content.styled(t("pending.body"), "$text-muted"))
            yield Static(Content.styled(CLI_EQUIVALENT[self.section], "bold $accent"))
