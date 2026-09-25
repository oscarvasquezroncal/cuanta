from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from cuanta.tui.i18n import Catalog
from cuanta.tui.widgets.flow import FlowRow


class AttachScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, catalog: Catalog) -> None:
        super().__init__(id="attach-screen")
        self._t = catalog

    def compose(self) -> ComposeResult:
        t = self._t
        with Vertical(id="attach-dialog", classes="card"):
            yield Static(t("mandate.attach_title"), classes="card-title")
            yield Label(t("mandate.attach_path"))
            yield Input(id="attach-path")
            with FlowRow(classes="button-row"):
                yield Button(
                    t("mandate.attach_confirm"), id="attach-ok", variant="primary", compact=True
                )
                yield Button(t("mandate.cancel"), id="attach-cancel", compact=True)

    def on_mount(self) -> None:
        self.query_one("#attach-path", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "attach-ok":
            self._confirm()
        else:
            self.action_cancel()

    def _confirm(self) -> None:
        path = self.query_one("#attach-path", Input).value.strip()
        self.dismiss(path or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
