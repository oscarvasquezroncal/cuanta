from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from cuanta.domain.errors import CuantaError
from cuanta.tui.i18n import Catalog


class FailureScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(self, catalog: Catalog, error: CuantaError) -> None:
        super().__init__(classes="confirm-screen")
        self._t = catalog
        self._error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-card", classes="card"):
            yield Static(self._t("failure.title"), classes="card-title")
            yield Static(Content("\n".join(self._t.failure(self._error))), id="confirm-body")
            pasted = self._error.hint.split("\n")[1:]
            if pasted:
                yield Static(Content("\n".join(pasted)), id="failure-hint")
            yield Button(self._t("failure.close"), id="failure-close", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
