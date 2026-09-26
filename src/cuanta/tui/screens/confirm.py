from __future__ import annotations

from collections.abc import Mapping

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from cuanta.tui.i18n import Catalog
from cuanta.tui.widgets.flow import FlowRow


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(
        self,
        catalog: Catalog,
        title_key: str,
        body_key: str,
        confirm_key: str,
        cancel_key: str,
        params: Mapping[str, object] | None = None,
        details: tuple[str, ...] = (),
    ) -> None:
        super().__init__(classes="confirm-screen")
        self._t = catalog
        self._keys = (title_key, body_key, confirm_key, cancel_key)
        self._params = dict(params or {})
        self._details = details

    def compose(self) -> ComposeResult:
        t = self._t
        title, body, confirm, cancel = self._keys
        with Vertical(id="confirm-card", classes="card"):
            yield Static(t(title), classes="card-title")
            yield Static(Content.styled(t(body, **self._params), "$text-muted"), id="confirm-body")
            if self._details:
                yield Static(Content("\n".join(self._details)), id="confirm-details")
            with FlowRow(classes="button-row", id="confirm-actions"):
                yield Button(t(cancel), id="confirm-cancel", variant="primary", compact=True)
                yield Button(t(confirm), id="confirm-ok", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm-ok")

    def action_cancel(self) -> None:
        self.dismiss(False)
